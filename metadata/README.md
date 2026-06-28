# Metadata Store — DataHub

## Quyết định lựa chọn

| Tiêu chí | DataHub ✅ | OpenMetadata |
|---------|-----------|-------------|
| Airflow 3 support | Có plugin native, giữ optional để tránh làm nặng Astro runtime | Chưa chính thức |
| dbt connector | `datahub ingest -c recipe.yml` | Có nhưng phức tạp hơn |
| Local deploy | `datahub docker quickstart` — 1 lệnh | Nhiều config tay |
| API cho Agent | GraphQL — mạnh, linh hoạt | REST API |
| RAM tối thiểu | ~8 GB | ~6 GB test / 16 GB prod |

**Kết luận**: Chọn **DataHub** vì hỗ trợ Airflow 3 native tốt nhất và cài đặt đơn giản nhất.

## Kiến trúc

```
Airflow 3 DAG + dbt build
     │  (manifest/catalog/run_results + ClickHouse schema)
      ▼
[DataHub ingestion recipes]
     │  REST emit to DataHub GMS
     ▼
[DataHub GMS :28080]            ← stores lineage as Dataset → DataJob edges
     │
     ▼
[DataHub UI :9002]              ← lineage graph visible
[DataHub GraphQL API]           ← Agent queries at runtime
```

Ghi chú: `acryl-datahub-airflow-plugin` có thể được thêm lại sau để emit Airflow task events trực tiếp, nhưng không nằm trong `requirements.txt` của Astro runtime vì dependency tree lớn và dễ làm chậm/flaky quá trình build local.

## Ports & Credentials

| Service | URL | Credential |
|---------|-----|------------|
| DataHub UI | http://localhost:9002 | `datahub` / `datahub` |
| DataHub GMS API | http://localhost:28080 | (token nếu cần) |
| ClickHouse (host) | http://localhost:18123 | `default` / `securepassword` |
| MinIO API | http://localhost:19001 | `minioadmin` / `minioadmin` |
| MinIO Console | http://localhost:19002 | `minioadmin` / `minioadmin` |

## Cách vận hành

### 1. Khởi động DataHub
```powershell
.\metadata\scripts\start-datahub.ps1
```

### 2. Kết nối Airflow containers vào DataHub network
```powershell
.\scripts\connect-networks.ps1
```
Script tự động tìm mạng Docker tên dạng `datahub*`.

### 3. Chạy ingestion (dbt + ClickHouse)
```powershell
.\metadata\scripts\run-ingestion.ps1
```
Script này tự động:
1. Sinh dbt artifacts (`dbt source freshness`, `dbt build`, `dbt docs generate`)
2. Ingest dbt metadata vào DataHub
3. Ingest ClickHouse physical schema vào DataHub

### 4. Verify lineage
Mở http://localhost:9002, tìm `gold_app_daily_cost` hoặc `fct_llm_interactions` → tab **Lineage**.

DataHub ingestion hiện đọc các ClickHouse schemas:
- `vsf_raw`
- `vsf_stg`
- `vsf_mart`

## Python Client (cho Agent)
```python
from metadata.client import DataHubClient

client = DataHubClient()
tables = client.list_tables(schema="vsf_mart")
columns = client.get_columns(tables[0]["urn"])
context = client.get_schema_context_for_question("Total tokens per model?")
```

## MCP Server (cho Agent/IDE)

DataHub MCP server expose metadata tools ổn định cho agent:

- `datahub_health`
- `search_tables`
- `get_table_schema`
- `get_lineage`
- `get_freshness`
- `get_table_context_for_question`

### Cấu hình môi trường

```bash
export DATAHUB_GMS_URL=http://localhost:28080
export DATAHUB_TOKEN=
export DATAHUB_MCP_DEFAULT_PLATFORM=clickhouse
export DATAHUB_MCP_DEFAULT_SCHEMA=vsf_mart
export DATAHUB_MCP_MAX_TABLES=5
export DATAHUB_MCP_MAX_COLUMNS=80
```

### Chạy qua stdio

```bash
python -m metadata.mcp_server
```

Ví dụ cấu hình MCP client:

```json
{
  "mcpServers": {
    "agentic-bi-datahub": {
      "command": "python",
      "args": ["-m", "metadata.mcp_server"],
      "env": {
        "DATAHUB_GMS_URL": "http://localhost:28080",
        "DATAHUB_MCP_DEFAULT_SCHEMA": "vsf_mart"
      }
    }
  }
}
```

Production note: ưu tiên stdio cho desktop/IDE agents. Chỉ dùng `DATAHUB_MCP_TRANSPORT=sse`
khi cần remote MCP client, và đặt sau auth/network boundary nội bộ.

## JVM Tuning cho VPS 16GB

| Component | Heap Size | Biến môi trường |
|-----------|-----------|-----------------|
| Elasticsearch | 1 GB | `ES_JAVA_OPTS="-Xms1g -Xmx1g"` |
| Kafka | 512 MB | `KAFKA_HEAP_OPTS="-Xms512m -Xmx512m"` |
| Zookeeper | 256 MB | `JVMFLAGS="-Xms256m -Xmx256m"` |
| GMS | 1 GB | `DATAHUB_GMS_JAVA_OPTS="-Xms1g -Xmx1g"` |

Tổng dự kiến: ~12.5 GB RAM. Còn ~3.5 GB buffer cho OS + dbt transform.
