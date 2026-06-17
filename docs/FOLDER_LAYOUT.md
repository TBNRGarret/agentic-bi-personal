# Hướng dẫn Cấu trúc Thư mục Dự án

Tài liệu này định nghĩa cấu trúc thư mục chuẩn hóa của dự án **Agentic BI** để các thành viên trong nhóm cùng tuân thủ thống nhất, phân chia rõ ràng trách nhiệm giữa lớp dẫn dữ liệu (Data Pipeline), Siêu dữ liệu (Metadata Store), và Ứng dụng AI (Agent & Giao diện).

---

## 1. Cây thư mục chi tiết (Directory Tree)

```text
agentic-bi/
├── .github/workflows/          # Tự động hóa CI/CD kiểm thử và deploy
├── dags/                       # Apache Airflow DAGs
│   ├── .airflowignore          # Chỉ định các tệp Airflow bỏ qua không quét
│   ├── agentic_bi_pipeline.py  # Luồng xử lý E2E (Raw -> Silver -> Gold)
│   └── smoke_dag.py            # DAG dùng chạy kiểm tra nhanh hạ tầng Airflow
├── include/                    # Chứa mã nguồn lưu trữ và biến đổi dữ liệu chính
│   ├── clickhouse/             # Thư mục khởi tạo cơ sở dữ liệu ClickHouse
│   │   └── init_vsf.sql        # Lệnh SQL tạo các database vsf_raw, vsf_stg, vsf_mart
│   └── dbt_project/            # Dự án dbt (Data Build Tool) biến đổi dữ liệu
│       ├── macros/             # Định nghĩa hàm SQL dùng lại (custom macros)
│       │   └── generate_schema_name.sql
│       ├── models/             # Định nghĩa cấu trúc các bảng SQL
│       │   ├── sources.yml     # Khai báo cấu trúc bảng nguồn (raw)
│       │   ├── schema.yml      # Định nghĩa luật kiểm thử cột (dbt tests)
│       │   ├── silver/         # Các bảng tầng Staging (Cleaned & De-duplicated)
│       │   └── gold/           # Các bảng tầng phân tích cuối (Analytical/Marts)
│       ├── seeds/              # Tệp CSV nạp dữ liệu cấu hình tĩnh
│       │   └── source_apps.csv # Danh sách ánh xạ Domain/Environment của các App
│       ├── dbt_project.yml     # File cấu hình cấu trúc dự án dbt
│       └── profiles.yml        # Định nghĩa thông số kết nối vào ClickHouse
├── metadata/                   # Quản lý siêu dữ liệu phục vụ AI Agent
│   ├── client/                 # Thư viện client tự viết cho Agent gọi lấy metadata
│   │   ├── __init__.py
│   │   └── datahub_client.py   # Code gọi API lấy schema/lineage từ DataHub GMS
│   ├── recipes/                # Cấu hình quét metadata của DataHub (Ingestion Recipes)
│   │   ├── clickhouse_recipe.yml        # Chạy ở Local máy cá nhân
│   │   ├── clickhouse_recipe_docker.yml # Chạy trên mạng Docker của VPS
│   │   ├── dbt_recipe.yml
│   │   └── dbt_recipe_docker.yml
│   └── scripts/                # Tập lệnh kích hoạt đẩy dữ liệu lên DataHub
│       └── run-ingestion.ps1
├── src/                        # Thư mục chứa mã nguồn chạy sản phẩm chính
│   ├── backend/                # Observation Service (Viết bằng FastAPI / Python)
│   │   ├── app/
│   │   │   ├── main.py         # Cổng khởi chạy API backend (/chat, /query, /schema)
│   │   │   ├── agent/          # Core AI Agent (Prompt, sinh SQL, Self-Correction)
│   │   │   │   ├── core.py
│   │   │   │   └── prompts.py
│   │   │   └── db/             # Kết nối ClickHouse để thực thi câu lệnh SQL sinh ra
│   │   ├── Dockerfile          # Cách đóng gói service thành Docker Image độc lập
│   │   └── requirements.txt    # Các thư viện Python cần thiết (langchain, uvicorn, openai)
│   └── frontend/               # Chat UI & Dashboard giao diện người dùng
│       ├── src/                # Code giao diện Chat, biểu đồ (Next.js / Vite React)
│       ├── Dockerfile          # Cách đóng gói Frontend web server trên VPS
│       └── package.json
├── scripts/                    # Scripts tiện ích chạy kiểm thử và quản trị hệ thống
│   ├── connect-networks.ps1    # Script thiết lập card mạng Docker
│   ├── export_dwh_to_minio.py  # Script đồng bộ dữ liệu từ Postgres cũ sang MinIO
│   ├── seed_dwh.py             # Script sinh dữ liệu giả lập
│   ├── start-system.ps1        # Script khởi chạy nhanh hệ thống local
│   └── verify-system.ps1       # Script xác minh tính đúng đắn tích hợp hệ thống
├── tests/                      # Bộ kiểm thử tự động của dự án (pytest)
│   ├── conftest.py
│   ├── test_datahub_client.py  # Test gọi API metadata
│   ├── test_export_dwh_to_minio.py
│   └── test_static_contracts.py# Test kiểm tra tính đúng đắn cấu trúc hợp đồng dữ liệu
├── docker-compose.override.yml # Cấu hình docker chạy local (Astro CLI tự nhận diện thêm vào)
├── docker-compose.vps.yml      # File Docker Compose khởi chạy toàn bộ hệ thống trên VPS
├── airflow_settings.yaml       # Tự động nạp cấu hình connection cho Airflow khi chạy local
├── requirements.txt            # Thư viện Python chạy luồng Airflow
└── packages.txt                # Thư viện hệ điều hành cài thêm cho Airflow container
```

---

## 2. Ánh xạ cấu trúc thư mục tới 4 Epics của Sprint

| Thư mục liên quan | Epic tương ứng | Nhiệm vụ chính |
| :--- | :--- | :--- |
| `dags/`, `include/dbt_project/`, `scripts/` | **Epic 1 — Sample Data Pipeline** | Nạp dữ liệu thô từ Postgres -> MinIO -> `vsf_raw` (ClickHouse), sau đó dùng dbt transform qua `vsf_stg` và `vsf_mart`, đảm bảo Data Quality chạy tự động. |
| `metadata/`, `airflow_settings.yaml` | **Epic 2 — Metadata Store** | Cài đặt DataHub quét toàn bộ cấu trúc bảng và lineage từ dbt/ClickHouse, viết module client để Agent gọi API lấy thông tin schema thời gian thực. |
| `src/backend/` | **Epic 3 — Observation Service & Agent Core** | Viết FastAPI backend làm API trung gian, phát triển AI Agent sinh câu lệnh SQL ClickHouse từ ngôn ngữ tự nhiên và cơ chế tự động sửa lỗi SQL (Self-Correction). |
| `src/frontend/` | **Epic 4 — Visualization & Dashboard** | Thiết kế Chat Box cho người dùng, phân tích kết quả trả về của Agent để sinh biểu đồ (cột, đường, tròn) tương ứng và nhúng Dashboard chuẩn hóa. |
