#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import codecs
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = Path("output")

EXAMPLE_QUESTIONS = {
    "chart_agent": "Vẽ biểu đồ p95 latency theo ngày trong 7 ngày gần nhất",
    "insight_agent": "Tại sao service payment-service bị latency cao trong 7 ngày gần nhất?",
    "forecast_agent": "Dự báo p95 latency trong 7 ngày tới dựa trên dữ liệu 30 ngày gần nhất",
    "qa_agent": "Kiểm tra ingestion health và failed batches trong 7 ngày gần nhất",
}


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {body}") from exc


def login(base_url: str, username: str, password: str) -> str:
    result = request_json(
        "POST",
        f"{base_url.rstrip('/')}/api/auth/login",
        {"username": username, "password": password},
    )
    token = str(result.get("accessToken") or "")
    if not token:
        raise RuntimeError("Login succeeded but accessToken is missing.")
    return token


def slugify(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return normalized.strip("-")[:50] or "question"


def create_output_folder(question: str, output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    suffix = hashlib.sha1(question.encode("utf-8")).hexdigest()[:8]
    folder = root / f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{slugify(question)}-{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def save_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _safe_file_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return name[:50] or "file"


def _decode_data_uri(data_uri: str) -> tuple[bytes, str] | None:
    if not data_uri.startswith("data:"):
        return None
    try:
        header, encoded = data_uri.split(",", 1)
        if ";base64" not in header:
            return None
        mime = header.split(";", 1)[0][5:]
        if not mime.startswith("image/"):
            return None
        data = base64.b64decode(encoded)
        ext = mime.split("/", 1)[1].split("+", 1)[0]
        if ext == "svg+xml":
            ext = "svg"
        return data, ext
    except Exception:
        return None


def save_media_from_payload(value: Any, folder: Path, prefix: str = "chart") -> list[Path]:
    saved: list[Path] = []
    if isinstance(value, str):
        uri = _decode_data_uri(value)
        if uri is not None:
            data, ext = uri
            path = folder / f"{prefix}.{ext}"
            path.write_bytes(data)
            return [path]
        return []
    if isinstance(value, dict):
        for key, inner in value.items():
            saved.extend(save_media_from_payload(inner, folder, f"{prefix}_{_safe_file_name(str(key))}"))
        return saved
    if isinstance(value, list):
        for index, item in enumerate(value):
            saved.extend(save_media_from_payload(item, folder, f"{prefix}_{index}"))
        return saved
    return []


def collect_media_files(result: dict[str, Any], folder: Path) -> list[Path]:
    return save_media_from_payload(result, folder, "chart")


def build_markdown_summary(
    folder: Path,
    question: str,
    target: str,
    result: dict[str, Any],
    media_files: list[Path],
) -> None:
    lines: list[str] = [
        f"# Agent Debug Result",
        "",
        f"**Question:** {question}",
        f"**Target:** {target}",
        "",
    ]

    summary_fields = [
        ("Status", result.get("status")),
        ("Intent", result.get("intent")),
        ("Agents", result.get("agentsUsed")),
        ("Selected tables", result.get("selectedTables")),
        ("Row count", result.get("rowCount")),
        ("Execution ms", result.get("executionTimeMs")),
        ("Route note", result.get("routeNote")),
    ]
    for label, value in summary_fields:
        if value is not None:
            lines.append(f"- **{label}:** {value}")
    lines.append("")

    if result.get("sql"):
        lines.extend(["## Generated SQL", "", "```sql", str(result["sql"]).strip(), "```", ""])

    if result.get("chartSpec"):
        lines.extend(["## Chart Spec", "", "```json", json.dumps(result["chartSpec"], ensure_ascii=False, indent=2), "```", ""])

    answer = result.get("answer")
    if answer:
        lines.extend(["## Answer", "", str(answer).strip(), ""])

    agent_responses = result.get("agentResponses") or []
    if agent_responses:
        lines.append("## Agent Responses")
        lines.append("")
        for response in agent_responses:
            agent_text = str(response.get("text") or "").strip()
            lines.append(f"### {response.get('agent')}")
            lines.append("")
            lines.append(f"- status: {response.get('status')}")
            lines.append(f"- confidence: {response.get('confidence')}")
            warnings = response.get("warnings") or []
            if warnings:
                lines.append(f"- warnings: {warnings}")
            if agent_text:
                lines.extend(["", agent_text, ""])

    if media_files:
        lines.append("## Media Files")
        lines.append("")
        for path in media_files:
            relative = path.name
            lines.append(f"- {relative}")
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
                lines.append(f"  ![{relative}]({relative})")
        lines.append("")

    lines.append("## Saved Files")
    lines.append("")
    lines.append("- result.json")
    if answer:
        lines.append("- answer.txt")
    if result.get("sql"):
        lines.append("- query.sql")
    if result.get("chartSpec"):
        lines.append("- chart_spec.json")
    for path in media_files:
        lines.append(f"- {path.name}")
    lines.append("- stream.log")

    save_text(folder / "result.md", "\n".join(lines))


def stream_agent(
    base_url: str,
    question: str,
    *,
    token: str | None,
    target: str,
    limit: int,
    include_synthesis: bool,
) -> tuple[dict[str, Any], list[str]]:
    url = f"{base_url.rstrip('/')}/api/query/chat/stream"
    payload = {
        "question": question,
        "target": target,
        "limit": limit,
        "includeSynthesis": include_synthesis,
        "conversation": [],
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    print("\n=== MULTI-AGENT DEBUG STREAM ===")
    print(f"Question: {question}")
    print(f"Target:   {target}")
    print("")

    final_payload: dict[str, Any] = {}
    buffer = ""
    stream_lines: list[str] = []
    decoder = codecs.getincrementaldecoder("utf-8")()

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            while True:
                chunk = response.read(1024)
                if not chunk:
                    buffer += decoder.decode(b"", final=True)
                    break
                buffer += decoder.decode(chunk)
                if "\n\n" not in buffer:
                    continue

                raw_event, buffer = buffer.split("\n\n", 1)
                for line in raw_event.splitlines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line.removeprefix("data: "))
                    event_type = event.get("type")
                    stream_lines.append(json.dumps(event, ensure_ascii=False))

                    if event_type == "stage":
                        stage = event.get("stage")
                        message = event.get("message")
                        status = event.get("status")
                        suffix = f" [{status}]" if status else ""
                        print(f"[stage:{stage}]{suffix} {message}")

                    elif event_type == "sql":
                        print("\n[sql]")
                        print(event.get("sql"))
                        print(f"intent={event.get('intent')} datasource={event.get('datasource')}")
                        print("")

                    elif event_type == "delta":
                        text = str(event.get("text") or "")
                        if text:
                            print(f"[answer-delta] {text}")

                    elif event_type == "final":
                        final_payload = event.get("payload") or {}
                        print("\n[final] received final payload")

                    else:
                        print(f"[event:{event_type}] {json.dumps(event, ensure_ascii=False)}")

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Streaming request failed: HTTP {exc.code}: {body}") from exc

    return final_payload, stream_lines


def save_result_files(folder: Path, question: str, target: str, result: dict[str, Any], stream_lines: list[str]) -> None:
    save_json(folder / "result.json", result)
    if answer := result.get("answer"):
        save_text(folder / "answer.txt", str(answer))
    if sql := result.get("sql"):
        save_text(folder / "query.sql", str(sql))
    if chart_spec := result.get("chartSpec"):
        save_json(folder / "chart_spec.json", chart_spec)
    if rows := result.get("rows"):
        save_json(folder / "rows.json", rows)
    save_text(folder / "stream.log", "\n".join(stream_lines) + "\n")
    media_files = collect_media_files(result, folder)
    build_markdown_summary(folder, question, target, result, media_files)


def print_final_summary(result: dict[str, Any]) -> None:
    print("\n=== FINAL RESULT SUMMARY ===")
    print(f"status:        {result.get('status')}")
    print(f"intent:        {result.get('intent')}")
    print(f"target:        {result.get('target')}")
    print(f"agentsUsed:    {result.get('agentsUsed')}")
    print(f"selectedTables:{result.get('selectedTables')}")
    print(f"rowCount:      {result.get('rowCount')}")
    print(f"executionMs:   {result.get('executionTimeMs')}")

    route_note = result.get("routeNote")
    if route_note:
        print(f"routeNote:     {route_note}")

    sql = result.get("sql")
    if sql:
        print("\n--- SQL ---")
        print(sql)

    table_selection = result.get("tableSelection")
    if table_selection:
        print("\n--- TABLE SELECTION ---")
        print(json.dumps(table_selection, ensure_ascii=False, indent=2, default=str))

    orchestration = result.get("orchestration")
    if orchestration:
        print("\n--- ORCHESTRATION ---")
        compact = {
            "routeIntent": orchestration.get("routeIntent"),
            "agents": orchestration.get("agents"),
            "slots": orchestration.get("slots"),
            "planner": orchestration.get("planner"),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2, default=str))

    responses = result.get("agentResponses") or []
    if responses:
        print("\n--- AGENT RESPONSES ---")
        for response in responses:
            print(
                f"- {response.get('agent')}: "
                f"status={response.get('status')} "
                f"confidence={response.get('confidence')}"
            )
            print(f"  text: {str(response.get('text') or '')[:500]}")
            warnings = response.get("warnings") or []
            if warnings:
                print(f"  warnings: {warnings}")

    answer = result.get("answer")
    if answer:
        print("\n--- ANSWER ---")
        print(answer)

    warnings = result.get("warnings") or []
    if warnings:
        print("\n--- WARNINGS ---")
        for warning in warnings:
            print(f"- {warning}")


def choose_question() -> str:
    print("Chọn input mẫu hoặc nhập câu hỏi mới:\n")
    keys = list(EXAMPLE_QUESTIONS)
    for index, key in enumerate(keys, start=1):
        print(f"{index}. {key}: {EXAMPLE_QUESTIONS[key]}")
    print("5. Nhập câu hỏi khác")
    choice = input("\nChoice [1-5]: ").strip()

    if choice in {"1", "2", "3", "4"}:
        return EXAMPLE_QUESTIONS[keys[int(choice) - 1]]

    question = input("Nhập câu hỏi: ").strip()
    if not question:
        raise SystemExit("Question is empty.")
    return question


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Debug từng bước MultiAgentOrchestrator qua SSE stream."
    )
    parser.add_argument("--base-url", default="http://47.131.228.222:3003")
    parser.add_argument("--question", default="")
    parser.add_argument("--target", default="auto")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--token", default="")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="pt2tCF8QBokuUix+0IOoUiCa")
    parser.add_argument("--no-synthesis", action="store_true")
    args = parser.parse_args()

    token = args.token or None
    if not token and args.username and args.password:
        token = login(args.base_url, args.username, args.password)

    question = args.question.strip() or choose_question()
    result, stream_lines = stream_agent(
        args.base_url,
        question,
        token=token,
        target=args.target,
        limit=args.limit,
        include_synthesis=not args.no_synthesis,
    )
    print_final_summary(result)

    output_folder = create_output_folder(question, args.output_dir)
    save_result_files(output_folder, question, args.target, result, stream_lines)
    print(f"\nSaved output into: {output_folder}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)