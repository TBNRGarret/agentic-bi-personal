# Hướng dẫn Cấu hình & Tối ưu hóa VPS Hosting

Tài liệu này phân tích năng lực chịu tải của VPS cấu hình **4 vCPU / 16 GB RAM / 320 GB SSD** và hướng dẫn tối ưu hóa để khởi chạy toàn bộ hệ thống dự án **Agentic BI** cùng các dịch vụ APM & Metadata Catalog một cách ổn định, không bị crash do hết bộ nhớ (Out-Of-Memory - OOM).

---

## 1. Phân tích tài nguyên & Thách thức

Tổng dung lượng RAM cần thiết nếu chạy cấu hình mặc định (default configs):

| Nhóm Dịch vụ | Thành phần chi tiết | RAM mặc định | RAM sau tối ưu |
| :--- | :--- | :--- | :--- |
| **DataHub Stack** | GMS, Frontend, Kafka, Zookeeper, Elasticsearch | ~4.5 - 5.5 GB | **~2.5 GB** |
| **APM SigNoz Stack** | Query Service, Otel Collector, ClickHouse | ~2.5 - 3.5 GB | **~1.5 GB** (Dùng chung ClickHouse) |
| **Langfuse v3 APM** | Next.js Web/Worker, Redis, Postgres, ClickHouse | ~2.5 - 3.0 GB | **~1.5 GB** (Dùng chung ClickHouse) |
| **Airflow 3 Stack** | Scheduler, Webserver, PostgreSQL metadata db | ~2.0 GB | **~1.0 GB** |
| **DWH & Apps** | ClickHouse Central DWH, MinIO, FastAPI Backend, React UI | ~2.5 GB | **~1.5 GB** |
| **Hệ điều hành** | OS & Docker Daemon | ~1.0 GB | **~1.0 GB** |
| **Tổng cộng** | | **~15.0 - 17.5 GB** | **~10.0 GB** |

> [!WARNING]
> Nếu không tối ưu hóa, hệ thống chắc chắn sẽ vượt quá 16 GB RAM vật lý của VPS dẫn đến tình trạng các container quan trọng như Elasticsearch, Clickhouse hoặc Airflow Scheduler tự động bị hệ điều hành tắt (Killed) do lỗi OOM.

---

## 2. Chiến lược Tối ưu hóa trên VPS

Để chạy ổn định toàn bộ stack trên VPS 16GB RAM, bắt buộc phải áp dụng các chiến lược sau:

### 2.1. Dùng chung một Instance ClickHouse (ClickHouse Consolidation)
Thay vì chạy 3 container ClickHouse riêng biệt (1 cho SigNoz, 1 cho Langfuse v3, 1 cho Central DWH), ta cấu hình cấu trúc dùng chung 1 container ClickHouse duy nhất:
*   Tách biệt dữ liệu bằng cách sử dụng các cơ sở dữ liệu (databases/namespaces) riêng:
    *   `vsf_raw`, `vsf_stg`, `vsf_mart` (Cho Central DWH)
    *   `signoz_logs`, `signoz_traces`, `signoz_metrics` (Cho SigNoz APM)
    *   `langfuse` (Cho Langfuse v3)
*   *Lợi ích:* Tiết kiệm ngay lập tức **~2.5 GB RAM**.

### 2.2. Giới hạn bộ nhớ JVM (Java Virtual Machine) Heap
DataHub sử dụng nhiều thành phần viết bằng Java/Kotlin (Elasticsearch, Kafka, Zookeeper, GMS), cần cấu hình giới hạn dung lượng heap trong `docker-compose.vps.yml`:
*   **Elasticsearch:** Cấu hình biến môi trường:
    ```yaml
    environment:
      - ES_JAVA_OPTS=-Xms512m -Xmx1g
    ```
    *(Giới hạn RAM tối đa cho Elasticsearch là 1GB thay vì mặc định tự nhận theo RAM máy).*
*   **DataHub GMS (metadata-io):**
    ```yaml
    environment:
      - JAVA_TOOL_OPTIONS=-Xms256m -Xmx512m
    ```
*   **Kafka & Zookeeper:**
    ```yaml
    environment:
      - KAFKA_HEAP_OPTS=-Xms128m -Xmx256m
    ```

### 2.3. Cấu hình phân vùng Swap (SSD Swap Space)
VPS có **320 GB SSD**, việc trích xuất **8 GB** làm bộ nhớ đệm (Swap) trên SSD là cực kỳ cần thiết để làm giá đỡ cứu cánh khi RAM vật lý bị quá tải tạm thời (Spikes):
```bash
# Tạo file swap 8GB trên VPS Linux
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Cấu hình tự động mount khi khởi động VPS
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Tinh chỉnh độ nhạy swap (swappiness) về mức 10 để ưu tiên dùng RAM vật lý trước
sudo sysslctl vm.swappiness=10
```

### 2.4. Giới hạn tài nguyên trong Docker Compose
Thiết lập giới hạn RAM tối đa cho từng dịch vụ trong `docker-compose.vps.yml` để ngăn chặn rò rỉ bộ nhớ (Memory Leak):
```yaml
services:
  fastapi-backend:
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
```

---

## 3. Cấu hình CPU core (4 vCPUs)
Với 4 cores CPU, việc chia sẻ tải nên được thiết lập như sau:
1.  **Airflow Executor:** Nên dùng `LocalExecutor` thay vì `CeleryExecutor` để tránh chạy thêm Redis/RabbitMQ trung chuyển và tránh tạo quá nhiều process worker.
2.  **Airflow Workers:** Giới hạn số lượng task chạy đồng thời ở `airflow.cfg`:
    ```ini
    parallelism = 4
    max_active_tasks_per_dag = 2
    ```
3.  **ClickHouse Thread Limit:** Giới hạn số luồng xử lý truy vấn của ClickHouse để tránh chiếm dụng hết CPU của các ứng dụng web khác:
    *   Cấu hình tham số `max_threads` trong file cấu hình ClickHouse hoặc set trong connection string: `max_threads = 2`.
