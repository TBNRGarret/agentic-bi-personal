# Chạy DataHub ingestion pipeline: sinh dbt artifacts rồi ingest dbt + ClickHouse metadata.
#
# Cách dùng:
#   .\metadata\scripts\run-ingestion.ps1
#
# Yêu cầu:
#   - DataHub đang chạy (.\metadata\scripts\start-datahub.ps1)
#   - dbt CLI đã cài đặt
#   - ClickHouse đang chạy tại localhost:18123

function Get-AgenticAirflowContainer {
    param([string]$Role)

    $container = docker ps --format "{{.Names}}" |
        Where-Object { $_ -match "^agentic-bi_.*-$Role-1$" } |
        Select-Object -First 1

    if (-not $container) {
        throw "Could not find Agentic BI Airflow $Role container."
    }
    return $container
}

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Root

$DbtDir = "include/dbt_project"

$DatahubCmd = "datahub"
$VenvDatahub = Join-Path $Root "..\sprint-1\venv\Scripts\datahub.exe"
if (Test-Path $VenvDatahub) {
    $DatahubCmd = $VenvDatahub
    Write-Host "[INFO] Using virtual environment datahub: $DatahubCmd" -ForegroundColor Cyan
} else {
    Write-Host "[WARN] Virtual environment datahub not found at $VenvDatahub, falling back to global 'datahub'" -ForegroundColor Yellow
}

try {
    # ------------------------------------------------------------------ #
    # 0. Check & Tự động cài đặt các CLI Tools nếu thiếu trên Host
    # ------------------------------------------------------------------ #
    if (($DatahubCmd -eq "datahub") -and -not (Get-Command datahub -ErrorAction SilentlyContinue)) {
        Write-Host "[INSTALL] datahub CLI không tìm thấy. Tiến hành cài đặt acryl-datahub..." -ForegroundColor Yellow
        pip install 'acryl-datahub'
        if ($LASTEXITCODE -ne 0) { throw "Cài đặt acryl-datahub thất bại." }
        Write-Host "[OK] datahub CLI đã được cài đặt thành công." -ForegroundColor Green
    }

    $scheduler = Get-AgenticAirflowContainer "scheduler"

    # ------------------------------------------------------------------ #
    # 1. Verify DataHub GMS đang chạy
    # ------------------------------------------------------------------ #
    Write-Host "[CHECK] Verifying DataHub GMS at http://localhost:28080..." -ForegroundColor Cyan
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:28080/health" -UseBasicParsing -TimeoutSec 5
        Write-Host "[OK] DataHub GMS is reachable (HTTP $($health.StatusCode))" -ForegroundColor Green
    } catch {
        throw "DataHub GMS is not reachable at http://localhost:28080. Run .\metadata\scripts\start-datahub.ps1 first."
    }

    # ------------------------------------------------------------------ #
    # 2. Sinh đầy đủ dbt artifacts (Chạy trong Docker để tránh lỗi đường dẫn trên Windows)
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "=== Step 1: Generating dbt artifacts ===" -ForegroundColor Cyan

    Write-Host "[DBT] Running dbt source freshness in Airflow container..." -ForegroundColor Yellow
    docker exec $scheduler dbt source freshness --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project 2>&1 | Write-Host
    # source freshness có thể trả exit code != 0 nếu chưa có freshness config, không fatal
    Write-Host "[INFO] dbt source freshness completed (exit: $LASTEXITCODE)" -ForegroundColor DarkGray

    Write-Host "[DBT] Running dbt build in Airflow container..." -ForegroundColor Yellow
    docker exec $scheduler dbt build --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project
    if ($LASTEXITCODE -ne 0) { throw "dbt build failed." }
    Write-Host "[OK] dbt build succeeded." -ForegroundColor Green

    Write-Host "[DBT] Running dbt docs generate in Airflow container..." -ForegroundColor Yellow
    docker exec $scheduler dbt docs generate --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project
    if ($LASTEXITCODE -ne 0) { throw "dbt docs generate failed." }
    Write-Host "[OK] dbt docs generate succeeded." -ForegroundColor Green

    # Kiểm tra artifacts đã sinh
    $requiredArtifacts = @(
        "$DbtDir/target/manifest.json",
        "$DbtDir/target/catalog.json",
        "$DbtDir/target/run_results.json"
    )
    foreach ($artifact in $requiredArtifacts) {
        if (-not (Test-Path $artifact)) {
            throw "Missing required dbt artifact: $artifact"
        }
        Write-Host "[OK] Found $artifact" -ForegroundColor Green
    }

    # sources.json là optional (chỉ sinh nếu có freshness config)
    if (Test-Path "$DbtDir/target/sources.json") {
        Write-Host "[OK] Found $DbtDir/target/sources.json" -ForegroundColor Green
    } else {
        Write-Host "[WARN] sources.json not found - source freshness metadata will be skipped" -ForegroundColor Yellow
    }

    # ------------------------------------------------------------------ #
    # 3. Ingest dbt metadata vào DataHub
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "=== Step 2: Ingesting dbt metadata ===" -ForegroundColor Cyan
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $DatahubCmd ingest -c metadata/recipes/dbt_recipe.yml
    $errCode = $LASTEXITCODE
    $ErrorActionPreference = $oldEap
    if ($errCode -ne 0) { throw "dbt ingestion failed." }
    Write-Host "[OK] dbt metadata ingested." -ForegroundColor Green

    # ------------------------------------------------------------------ #
    # 4. Ingest ClickHouse warehouse metadata vào DataHub
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "=== Step 3: Ingesting ClickHouse warehouse metadata ===" -ForegroundColor Cyan
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $DatahubCmd ingest -c metadata/recipes/clickhouse_recipe.yml
    $errCode = $LASTEXITCODE
    $ErrorActionPreference = $oldEap
    if ($errCode -ne 0) { throw "ClickHouse ingestion failed." }
    Write-Host "[OK] ClickHouse metadata ingested." -ForegroundColor Green

    # ------------------------------------------------------------------ #
    # 5. Báo cáo
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " Metadata ingestion completed!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Open http://localhost:9002 to verify lineage." -ForegroundColor Cyan
    Write-Host "  Search for 'gold_app_daily_cost' or 'fct_llm_interactions' and check the Lineage tab." -ForegroundColor Cyan
}
finally {
    Pop-Location
}
