# Chạy DataHub ingestion pipeline: sinh dbt artifacts rồi ingest dbt + ClickHouse metadata.
#
# Cách dùng:
#   .\metadata\scripts\run-ingestion.ps1
#
# Yêu cầu:
#   - DataHub đang chạy (.\metadata\scripts\start-datahub.ps1)
#   - dbt CLI đã cài đặt
#   - ClickHouse đang chạy tại localhost:18123

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Root

$DbtDir = "include/dbt_project"

try {
    # ------------------------------------------------------------------ #
    # 1. Verify DataHub GMS đang chạy
    # ------------------------------------------------------------------ #
    Write-Host "[CHECK] Verifying DataHub GMS at http://localhost:18080..." -ForegroundColor Cyan
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:18080/health" -UseBasicParsing -TimeoutSec 5
        Write-Host "[OK] DataHub GMS is reachable (HTTP $($health.StatusCode))" -ForegroundColor Green
    } catch {
        throw "DataHub GMS is not reachable at http://localhost:18080. Run .\metadata\scripts\start-datahub.ps1 first."
    }

    # ------------------------------------------------------------------ #
    # 2. Sinh đầy đủ dbt artifacts
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "=== Step 1: Generating dbt artifacts ===" -ForegroundColor Cyan

    Write-Host "[DBT] Running dbt source freshness (generates sources.json)..." -ForegroundColor Yellow
    dbt source freshness --project-dir $DbtDir --profiles-dir $DbtDir 2>&1 | Write-Host
    # source freshness có thể trả exit code != 0 nếu chưa có freshness config, không fatal
    Write-Host "[INFO] dbt source freshness completed (exit: $LASTEXITCODE)" -ForegroundColor DarkGray

    Write-Host "[DBT] Running dbt build (generates manifest.json + run_results.json)..." -ForegroundColor Yellow
    dbt build --project-dir $DbtDir --profiles-dir $DbtDir
    if ($LASTEXITCODE -ne 0) { throw "dbt build failed." }
    Write-Host "[OK] dbt build succeeded." -ForegroundColor Green

    Write-Host "[DBT] Running dbt docs generate (generates catalog.json)..." -ForegroundColor Yellow
    dbt docs generate --project-dir $DbtDir --profiles-dir $DbtDir
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
        Write-Host "[WARN] sources.json not found — source freshness metadata will be skipped" -ForegroundColor Yellow
    }

    # ------------------------------------------------------------------ #
    # 3. Ingest dbt metadata vào DataHub
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "=== Step 2: Ingesting dbt metadata ===" -ForegroundColor Cyan
    datahub ingest -c metadata/recipes/dbt_recipe.yml
    if ($LASTEXITCODE -ne 0) { throw "dbt ingestion failed." }
    Write-Host "[OK] dbt metadata ingested." -ForegroundColor Green

    # ------------------------------------------------------------------ #
    # 4. Ingest ClickHouse warehouse metadata vào DataHub
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "=== Step 3: Ingesting ClickHouse warehouse metadata ===" -ForegroundColor Cyan
    datahub ingest -c metadata/recipes/clickhouse_recipe.yml
    if ($LASTEXITCODE -ne 0) { throw "ClickHouse ingestion failed." }
    Write-Host "[OK] ClickHouse metadata ingested." -ForegroundColor Green

    # ------------------------------------------------------------------ #
    # 5. Báo cáo
    # ------------------------------------------------------------------ #
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " Metadata ingestion completed!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Open http://localhost:9002 to verify lineage." -ForegroundColor Cyan
    Write-Host "  Search for 'fct_llm_interactions' and check the Lineage tab." -ForegroundColor Cyan
}
finally {
    Pop-Location
}
