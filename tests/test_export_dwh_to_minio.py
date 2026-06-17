import json
from uuid import UUID


class FakeCursor:
    def __init__(self):
        self.current_table = None
        self.queries = []
        self.description = []

    def execute(self, query):
        self.queries.append(query)
        for table_name, columns in {
            "signoz_logs": ["log_id", "timestamp"],
            "signoz_traces": ["trace_id", "span_id"],
            "signoz_metrics": ["metric_id", "metric_name"],
            "langfuse_generations": ["generation_id", "model"],
            "user_interactions": ["interaction_id", "user_id"],
        }.items():
            if f"staging.{table_name}" in query:
                self.current_table = table_name
                self.description = [(column,) for column in columns]
                return
        raise AssertionError(f"unexpected query: {query}")

    def fetchall(self):
        return [(f"{self.current_table}-1", "value")]

    def close(self):
        pass


class FakePostgresConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class FakeMinioClient:
    def __init__(self):
        self.buckets = set()
        self.uploads = []

    def bucket_exists(self, bucket_name):
        return bucket_name in self.buckets

    def make_bucket(self, bucket_name):
        self.buckets.add(bucket_name)

    def put_object(self, bucket_name, object_name, data, length, content_type):
        self.uploads.append(
            {
                "bucket_name": bucket_name,
                "object_name": object_name,
                "payload": data.read(length).decode("utf-8"),
                "content_type": content_type,
            }
        )


def test_export_all_tables_uploads_jsonl_objects_with_source_and_batch_metadata(monkeypatch):
    monkeypatch.setenv("MINIO_PATH_PREFIX", "raw/hoangvl_ai_chat")
    from scripts.export_dwh_to_minio import export_all_tables

    pg_conn = FakePostgresConnection()
    minio_client = FakeMinioClient()
    batch_id = UUID("11111111-1111-1111-1111-111111111111")

    summary = export_all_tables(
        pg_connection=pg_conn,
        minio_client=minio_client,
        source_app_id="hoangvl_ai_chat",
        batch_id=batch_id,
        bucket_name="agentic-bi-raw",
    )

    object_names = [upload["object_name"] for upload in minio_client.uploads]

    assert "agentic-bi-raw" in minio_client.buckets
    assert len(minio_client.uploads) == 5
    assert "raw/hoangvl_ai_chat/staging_llm_generations/11111111-1111-1111-1111-111111111111.jsonl" in object_names
    assert summary["batch_id"] == str(batch_id)
    assert summary["tables"]["langfuse_generations"]["row_count"] == 1

    first_payload_line = minio_client.uploads[0]["payload"].strip().splitlines()[0]
    first_record = json.loads(first_payload_line)

    assert first_record["source_app_id"] == "hoangvl_ai_chat"
    assert first_record["batch_id"] == str(batch_id)
    assert minio_client.uploads[0]["content_type"] == "application/jsonl"
