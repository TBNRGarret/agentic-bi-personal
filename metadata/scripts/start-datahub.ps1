# Khởi động DataHub local stack với cấu hình JVM Tuning tránh OOM trên VPS 16GB.
#
# Cách dùng:
#   .\metadata\scripts\start-datahub.ps1
#
# Yêu cầu:
#   - Docker & Docker Compose
#   - Python 3.10+
#   - Tối thiểu 8GB RAM trống
#
# Đăng nhập DataHub UI: datahub / datahub

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Root

try {
    # ------------------------------------------------------------------ #
    # 1. Cài đặt DataHub CLI nếu chưa có
    # ------------------------------------------------------------------ #
    if (-not (Get-Command datahub -ErrorAction SilentlyContinue)) {
        Write-Host "[INSTALL] Installing DataHub CLI (acryl-datahub)..." -ForegroundColor Yellow
        pip install 'acryl-datahub'
        if ($LASTEXITCODE -ne 0) { throw "pip install acryl-datahub failed." }
    }

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $dhVersion = datahub version 2>$null | Select-Object -First 1
    $ErrorActionPreference = $oldEap
    Write-Host "[INFO] DataHub CLI version: $dhVersion" -ForegroundColor Cyan

    # ------------------------------------------------------------------ #
    # 2. JVM Tuning — Giới hạn heap cho từng service tránh OOM
    # ------------------------------------------------------------------ #
    $env:DATAHUB_MAPPED_GMS_PORT = "28080"

    # Elasticsearch — 1 GB heap
    $env:ES_JAVA_OPTS = "-Xms1g -Xmx1g"

    # Kafka — 512 MB heap
    $env:KAFKA_HEAP_OPTS = "-Xms512m -Xmx512m"

    # Zookeeper — 256 MB heap
    $env:JVMFLAGS = "-Xms256m -Xmx256m"

    # GMS (DataHub backend) — 1 GB heap
    $env:DATAHUB_GMS_JAVA_OPTS = "-Xms1g -Xmx1g"

    Write-Host "[START] Starting DataHub with JVM constraints..." -ForegroundColor Cyan
    Write-Host "  ES heap:  1g  |  Kafka heap: 512m  |  ZK heap: 256m  |  GMS heap: 1g" -ForegroundColor DarkGray

    # ------------------------------------------------------------------ #
    # 3. Khởi động DataHub Docker Quickstart
    # ------------------------------------------------------------------ #
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    datahub docker quickstart
    $errCode = $LASTEXITCODE
    $ErrorActionPreference = $oldEap
    if ($errCode -ne 0) { throw "datahub docker quickstart failed with exit code $errCode." }

    # ------------------------------------------------------------------ #
    # 4. Detect DataHub Docker network (có thể là datahub_default hoặc datahub_network)
    # ------------------------------------------------------------------ #
    Write-Host "[DETECT] Looking for DataHub docker network..." -ForegroundColor Cyan
    $datahubNet = docker network ls --format '{{.Name}}' |
        Where-Object { $_ -match "datahub" } |
        Select-Object -First 1

    if ($datahubNet) {
        Write-Host "[OK] Detected DataHub network: $datahubNet" -ForegroundColor Green
    } else {
        $datahubNet = "datahub_default"
        Write-Host "[WARN] Could not auto-detect DataHub network. Fallback: $datahubNet" -ForegroundColor Yellow
    }

    # Lưu tên network vào biến môi trường để connect-networks.ps1 sử dụng
    $env:DATAHUB_NETWORK_NAME = $datahubNet

    # ------------------------------------------------------------------ #
    # 5. Tự động kết nối Airflow containers vào DataHub network (nếu đang chạy)
    # ------------------------------------------------------------------ #
    $airflowContainers = docker ps --format '{{.Names}}' |
        Where-Object { $_ -match "^agentic-bi_.*-(scheduler|api-server|triggerer|dag-processor)-1$" }

    if ($airflowContainers) {
        Write-Host "[BRIDGE] Connecting Airflow containers to $datahubNet..." -ForegroundColor Cyan
        foreach ($c in $airflowContainers) {
            docker network connect $datahubNet $c 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  Connected $c to $datahubNet" -ForegroundColor Green
            } else {
                Write-Host "  $c already on $datahubNet (or error)" -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "[INFO] No Airflow containers running. Bridge skipped." -ForegroundColor DarkGray
        Write-Host "       Run .\scripts\connect-networks.ps1 after 'astro dev start'." -ForegroundColor DarkGray
    }

    # ------------------------------------------------------------------ #
    # 6. Báo cáo trạng thái
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " DataHub started successfully!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  UI:      http://localhost:9002   (datahub/datahub)" -ForegroundColor Cyan
    Write-Host "  GMS API: http://localhost:28080" -ForegroundColor Cyan
    Write-Host "  Network: $datahubNet" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Run .\metadata\scripts\run-ingestion.ps1  to ingest dbt + ClickHouse metadata" -ForegroundColor Yellow
    Write-Host "  2. Open http://localhost:9002 to verify lineage graph" -ForegroundColor Yellow
}
finally {
    Pop-Location
}
