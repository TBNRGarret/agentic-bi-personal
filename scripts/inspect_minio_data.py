#!/usr/bin/env python3
"""
Inspect dữ liệu từ MinIO (S3-compatible) — FULL SCAN tất cả tables & owners.
Xử lý đúng: .json = manifest (single JSON) | .jsonl = legacy (JSON Lines)
"""

import boto3
from botocore.config import Config
import json
import sys
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

ENDPOINT = os.getenv("MINIO_ENDPOINT")
ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
BUCKET = os.getenv("MINIO_BUCKET")

def create_client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

def list_all_objects(client, prefix=""):
    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=prefix)
    objects = []
    for page in pages:
        if "Contents" in page:
            for obj in page["Contents"]:
                objects.append(obj)
    return objects

def get_file_extension(key):
    _, ext = os.path.splitext(key)
    return ext.lower()

def inspect_parquet(client, key):
    import pyarrow.parquet as pq
    import io

    print(f"\n{'='*80}")
    print(f"📄 Parquet: {key}")
    print(f"{'='*80}")

    obj = client.get_object(Bucket=BUCKET, Key=key)
    data = obj["Body"].read()
    table = pq.read_table(io.BytesIO(data))
    schema = table.schema

    print(f"\n📊 Schema ({table.num_columns} columns, {table.num_rows} rows, {len(data)/1024:.1f} KB):")
    print(f"{'─'*80}")
    print(f"{'Column':30} {'Type':20} {'Example'}")
    print(f"{'─'*80}")
    for i in range(table.num_columns):
        col_name = schema.field(i).name
        col_type = str(schema.field(i).type)
        col_data = table.column(i)
        example = str(col_data[0].as_py()) if len(col_data) > 0 else "N/A"
        print(f"{col_name:30} {col_type:20} {str(example)[:50]}")

    if table.num_rows > 0:
        print(f"\n📝 Row 0 sample:")
        sample = table.to_pydict()
        row = {k: str(v[0])[:80] for k, v in sample.items()}
        print(f"  {json.dumps(row, ensure_ascii=False)[:500]}")
    return table

def inspect_jsonl(client, key):
    print(f"\n{'='*80}")
    print(f"📄 JSONL: {key}")
    print(f"{'='*80}")

    obj = client.get_object(Bucket=BUCKET, Key=key)
    content = obj["Body"].read().decode("utf-8")
    lines = content.strip().split("\n")

    print(f"\n📊 Total lines: {len(lines)}")
    if lines:
        try:
            first = json.loads(lines[0])
            print(f"\n📋 Schema ({len(first)} fields):")
            print(f"{'─'*80}")
            print(f"{'Field':35} {'Type':20} {'Example'}")
            print(f"{'─'*80}")
            for k, v in first.items():
                val_type = type(v).__name__
                example = str(v)[:60] if v is not None else "None"
                print(f"{k:35} {val_type:20} {example}")
        except json.JSONDecodeError:
            print("⚠️  Not valid JSONL")
            print(content[:500])
    return lines

def main():
    print(f"🔍 Agentic BI — MinIO Full Data Inspector")
    print(f"{'='*80}")
    print(f"Endpoint: {ENDPOINT}")
    print(f"Bucket:   {BUCKET}")
    print(f"Time:     {datetime.now().isoformat()}")
    print(f"{'='*80}")

    try:
        import pyarrow  # noqa
    except ImportError:
        print("\n❌ pip install boto3 pyarrow pandas")
        sys.exit(1)

    client = create_client()
    all_objs = list_all_objects(client)

    if not all_objs:
        print("❌ No objects found!")
        try:
            buckets = client.list_buckets()
            print(f"   Buckets: {[b['Name'] for b in buckets['Buckets']]}")
        except Exception as e:
            print(f"   Connection error: {e}")
        sys.exit(1)

    print(f"\n✅ Total objects: {len(all_objs)}")

    # File types
    by_ext = {}
    for obj in all_objs:
        ext = get_file_extension(obj["Key"]) or "(no ext)"
        by_ext.setdefault(ext, []).append(obj)

    print(f"\n📂 File types:")
    for ext, objs in sorted(by_ext.items()):
        total_size = sum(o["Size"] for o in objs) / (1024*1024)
        print(f"  {ext:10} → {len(objs):5} files, {total_size:.1f} MB")

    # ===== 1. INSPECT MANIFEST.JSON (single JSON - batch metadata) =====
    print(f"\n{'='*80}")
    print(f"🔬 MANIFEST FILES (.json)")
    print(f"{'='*80}")
    for obj in by_ext.get(".json", [])[:4]:
        key = obj["Key"]
        print(f"\n📄 {key} ({obj['Size']/1024:.1f} KB)")
        try:
            resp = client.get_object(Bucket=BUCKET, Key=key)
            content = json.loads(resp["Body"].read().decode("utf-8"))
            print(f"   {json.dumps(content, indent=2, ensure_ascii=False)}")
        except Exception as e:
            print(f"   ⚠️  {e}")

    # ===== 2. INSPECT ALL PARQUET TABLES (mỗi table × mỗi owner) =====
    print(f"\n{'='*80}")
    print(f"🔬 PARQUET TABLES (theo từng owner)")
    print(f"{'='*80}")

    parquet_objs = by_ext.get(".parquet", [])
    parquet_groups = {}
    for obj in parquet_objs:
        parts = obj["Key"].split("/")
        if len(parts) >= 5:
            owner = parts[0].split("_")[0]
            table_type = parts[3]
            parquet_groups.setdefault((table_type, owner), []).append(obj)

    for (table_type, owner), objs in sorted(parquet_groups.items()):
        sample = objs[0]
        total_size = sum(o["Size"] for o in objs)
        print(f"\n📁 [{owner}] {table_type} ({len(objs)} files, {total_size/1024:.1f} KB)")
        try:
            inspect_parquet(client, sample["Key"])
        except Exception as e:
            print(f"⚠️  Error: {e}")

    # ===== 3. INSPECT LEGACY JSONL =====
    print(f"\n{'='*80}")
    print(f"🔬 LEGACY JSONL (archive/legacy/)")
    print(f"{'='*80}")
    for obj in by_ext.get(".jsonl", [])[:3]:
        key = obj["Key"]
        print(f"\n📁 {key} ({obj['Size']/1024:.1f} KB)")
        try:
            inspect_jsonl(client, key)
        except Exception as e:
            print(f"⚠️  Error: {e}")

    # ===== SUMMARY =====
    print(f"\n{'='*80}")
    print(f"✅ SUMMARY")
    print(f"{'='*80}")
    print(f"\n📊 Tables Overview:")
    print(f"{'─'*100}")
    print(f"{'Table Type':40} {'Owners':35} {'Files':10} {'Size':10}")
    print(f"{'─'*100}")

    table_summary = {}
    for obj in all_objs:
        key = obj["Key"]
        if "archive" in key:
            continue
        parts = key.split("/")
        if len(parts) < 5:
            continue
        table_type = parts[3]
        owner = parts[0].split("_")[0]
        ext = get_file_extension(key)
        if ext != ".parquet":
            continue
        table_summary.setdefault(table_type, {"owners": set(), "files": 0, "size": 0})
        table_summary[table_type]["owners"].add(owner)
        table_summary[table_type]["files"] += 1
        table_summary[table_type]["size"] += obj["Size"]

    for table_type, info in sorted(table_summary.items()):
        owners_str = ", ".join(sorted(info["owners"]))
        size_mb = info["size"] / (1024*1024)
        print(f"{table_type:40} {owners_str:35} {info['files']:10} {size_mb:.1f} MB")

    total_gb = sum(o["Size"] for o in all_objs) / (1024**3)
    print(f"\n{'='*80}")
    print(f"📊 TOTAL: {total_gb:.2f} GB | {len(all_objs)} files")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()