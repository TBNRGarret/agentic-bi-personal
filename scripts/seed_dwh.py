import psycopg2
from datetime import datetime, timedelta, timezone
import json
import os


POSTGRES_DWH_HOST = os.getenv("POSTGRES_DWH_HOST", "postgres_dwh")
POSTGRES_DWH_PORT = int(os.getenv("POSTGRES_DWH_PORT", "5432"))
POSTGRES_DWH_USER = os.getenv("POSTGRES_DWH_USER", "postgres")
POSTGRES_DWH_PASSWORD = os.getenv("POSTGRES_DWH_PASSWORD", "securepassword")
POSTGRES_DWH_DB = os.getenv("POSTGRES_DWH_DB", "dwh_db")
POSTGRES_DWH_FALLBACK_HOST = os.getenv("POSTGRES_DWH_FALLBACK_HOST", "localhost")


def seed():
    try:
        conn = psycopg2.connect(
            host=POSTGRES_DWH_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB
        )
    except psycopg2.OperationalError:
        conn = psycopg2.connect(
            host=POSTGRES_DWH_FALLBACK_HOST,
            port=POSTGRES_DWH_PORT,
            user=POSTGRES_DWH_USER,
            password=POSTGRES_DWH_PASSWORD,
            dbname=POSTGRES_DWH_DB
        )
    cur = conn.cursor()

    # Clear existing data in staging tables
    cur.execute("TRUNCATE TABLE staging.signoz_logs CASCADE;")
    cur.execute("TRUNCATE TABLE staging.signoz_traces CASCADE;")
    cur.execute("TRUNCATE TABLE staging.signoz_metrics CASCADE;")
    cur.execute("TRUNCATE TABLE staging.langfuse_generations CASCADE;")
    cur.execute("TRUNCATE TABLE staging.user_interactions CASCADE;")
    cur.execute("TRUNCATE TABLE staging.ingestion_watermarks CASCADE;")
    print("Cleared existing staging tables.")

    now = datetime.now(timezone.utc)

    # 1. User Interactions
    # Row 1: Success Interaction
    int_1 = (
        "int-1001",
        "trace-success-123",
        "session-abc",
        "user-hoangvl",
        now - timedelta(minutes=5),
        now - timedelta(minutes=4, seconds=50),
        "gpt-4o",
        "Hello, what is ClickHouse?",
        "ClickHouse is a fast open-source column-oriented database management system for online analytical processing (OLAP).",
        15,
        25,
        40,
        0.00020,
        "success",
        json.dumps({"metadata": "success_chat"}),
        now
    )
    # Row 2: Failed Interaction (due to SQL exception in backend)
    int_2 = (
        "int-1002",
        "trace-error-456",
        "session-abc",
        "user-hoangvl",
        now - timedelta(minutes=2),
        now - timedelta(minutes=1, seconds=55),
        "gpt-4o",
        "Give me the total sales by country",
        "Internal server error occurred while executing the query.",
        10,
        15,
        25,
        0.00012,
        "failed",
        json.dumps({"metadata": "failed_chat"}),
        now
    )
    # Row 3: Another Success Interaction (different model)
    int_3 = (
        "int-1003",
        "trace-success-789",
        "session-xyz",
        "user-anlq",
        now - timedelta(hours=1),
        now - timedelta(minutes=59),
        "gemini-2.0-flash",
        "List all databases in system",
        "The system has 3 databases: vsf_raw, vsf_stg, and vsf_mart.",
        8,
        12,
        20,
        0.00005,
        "success",
        json.dumps({"metadata": "success_gemini"}),
        now
    )

    cur.executemany("""
        INSERT INTO staging.user_interactions 
        (interaction_id, langfuse_trace_id, session_id, user_id, started_at, ended_at, model, prompt_text, response_text, input_tokens, output_tokens, total_tokens, estimated_cost_usd, status, raw_payload, ingested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, [int_1, int_2, int_3])

    # 2. Langfuse Generations
    gen_1 = (
        "gen-2001",
        "trace-success-123",
        now - timedelta(minutes=4, seconds=58),
        "gpt-4o",
        "Hello, what is ClickHouse?",
        "ClickHouse is a fast open-source column-oriented database management system for online analytical processing (OLAP).",
        15,
        25,
        40,
        0.00020,
        10000,
        "success",
        json.dumps({"metadata": "gen_success_detail"}),
        now
    )
    gen_2 = (
        "gen-2002",
        "trace-error-456",
        now - timedelta(minutes=1, seconds=58),
        "gpt-4o",
        "Give me the total sales by country",
        "Internal server error occurred while executing the query.",
        10,
        15,
        25,
        0.00012,
        5000,
        "failed",
        json.dumps({"metadata": "gen_failed_detail"}),
        now
    )
    gen_3 = (
        "gen-2003",
        "trace-success-789",
        now - timedelta(hours=1, minutes=1),
        "gemini-2.0-flash",
        "List all databases in system",
        "The system has 3 databases: vsf_raw, vsf_stg, and vsf_mart.",
        8,
        12,
        20,
        0.00005,
        60000,
        "success",
        json.dumps({"metadata": "gen_gemini_detail"}),
        now
    )

    cur.executemany("""
        INSERT INTO staging.langfuse_generations 
        (generation_id, trace_id, timestamp, model, prompt_text, response_text, input_tokens, output_tokens, total_tokens, estimated_cost_usd, duration_ms, status, raw_payload, ingested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, [gen_1, gen_2, gen_3])

    # 3. SigNoz Logs (Including one ERROR log matching the failed trace)
    log_1 = (
        "log-3001",
        now - timedelta(minutes=5),
        "trace-success-123",
        "span-123",
        "hoangvl-ai-chat",
        "INFO",
        "Chat turn completed successfully.",
        json.dumps({"level": "info", "component": "app"}),
        now
    )
    log_2 = (
        "log-3002",
        now - timedelta(minutes=1, seconds=57),
        "trace-error-456",
        "span-456",
        "hoangvl-ai-chat",
        "ERROR",
        "DB::Exception: Table sales_data does not exist in database default",
        json.dumps({"level": "error", "component": "database", "error": "TableNotFound"}),
        now
    )
    log_3 = (
        "log-3003",
        now - timedelta(hours=1),
        "trace-success-789",
        "span-789",
        "anlq-ai-chat",
        "INFO",
        "Gemini request processed",
        json.dumps({"level": "info"}),
        now
    )

    cur.executemany("""
        INSERT INTO staging.signoz_logs 
        (log_id, timestamp, trace_id, span_id, service_name, severity, message, raw_payload, ingested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, [log_1, log_2, log_3])

    # 4. SigNoz Traces
    trace_rec_1 = (
        "trace-success-123",
        "span-123",
        now - timedelta(minutes=5),
        "hoangvl-ai-chat",
        "ai.chat.turn",
        10000,
        json.dumps({"name": "chat_turn"}),
        now
    )
    trace_rec_2 = (
        "trace-error-456",
        "span-456",
        now - timedelta(minutes=2),
        "hoangvl-ai-chat",
        "ai.chat.turn",
        5000,
        json.dumps({"name": "chat_turn"}),
        now
    )

    cur.executemany("""
        INSERT INTO staging.signoz_traces
        (trace_id, span_id, timestamp, service_name, name, duration_ms, raw_payload, ingested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, [trace_rec_1, trace_rec_2])

    # 5. SigNoz Metrics
    metric_1 = (
        "metric-4001",
        now - timedelta(minutes=5),
        "hoangvl-ai-chat",
        "chat.turns",
        1.0,
        "count",
        json.dumps({"status": "completed"}),
        json.dumps({"metric_family": "chat.turns"}),
        now
    )
    metric_2 = (
        "metric-4002",
        now - timedelta(minutes=2),
        "hoangvl-ai-chat",
        "chat.turns",
        1.0,
        "count",
        json.dumps({"status": "failed"}),
        json.dumps({"metric_family": "chat.turns"}),
        now
    )

    cur.executemany("""
        INSERT INTO staging.signoz_metrics
        (metric_id, timestamp, service_name, metric_name, metric_value, unit, attributes, raw_payload, ingested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, [metric_1, metric_2])

    # 6. Ingestion Watermarks (Initialize)
    cur.execute("""
        INSERT INTO staging.ingestion_watermarks (source_name, table_name, last_watermark, updated_at)
        VALUES 
        ('signoz', 'logs', %s, %s),
        ('signoz', 'traces', %s, %s),
        ('signoz', 'metrics', %s, %s),
        ('langfuse', 'generations', %s, %s),
        ('langfuse', 'user_interactions', %s, %s)
        ON CONFLICT (source_name, table_name) DO UPDATE SET last_watermark = EXCLUDED.last_watermark;
    """, [now, now, now, now, now, now, now, now, now, now])

    conn.commit()
    cur.close()
    conn.close()
    print("Successfully seeded DWH staging tables with mock telemetry data.")

if __name__ == "__main__":
    seed()
