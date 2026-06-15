# Generate dbt artifacts, ingest dbt + ClickHouse metadata, and publish freshness to DataHub.

$ErrorActionPreference = "Stop"

function Get-AgenticAirflowContainer {
    param([string]$Role)
    $container = docker ps --format "{{.Names}}" |
        Where-Object { $_ -match "^agentic-bi_.*-$Role-1$" } |
        Select-Object -First 1
    if (-not $container) { throw "Could not find Agentic BI Airflow $Role container." }
    return $container
}

function Get-DataHubCommand {
    $localVenv = Join-Path $PWD ".venv\Scripts\datahub.exe"
    if (Test-Path $localVenv) { return $localVenv }
    if (Get-Command datahub -ErrorAction SilentlyContinue) { return "datahub" }
    Write-Host "[INSTALL] Installing acryl-datahub CLI..." -ForegroundColor Yellow
    pip install 'acryl-datahub'
    if ($LASTEXITCODE -ne 0) { throw "pip install acryl-datahub failed." }
    return "datahub"
}

function Get-PythonCommand {
    $winVenv = Join-Path $PWD ".venv\Scripts\python.exe"
    $unixVenv = Join-Path $PWD ".venv/bin/python"
    if (Test-Path $winVenv) { return $winVenv }
    if (Test-Path $unixVenv) { return $unixVenv }
    return "python"
}

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Root

try {
    $DbtDir = "include/dbt_project"
    $DatahubCmd = Get-DataHubCommand
    $PythonCmd = Get-PythonCommand
    $scheduler = Get-AgenticAirflowContainer "scheduler"

    Write-Host "[CHECK] Verifying DataHub GMS at http://localhost:28080..." -ForegroundColor Cyan
    $health = Invoke-WebRequest -Uri "http://localhost:28080/health" -UseBasicParsing -TimeoutSec 10
    if ($health.StatusCode -lt 200 -or $health.StatusCode -ge 300) { throw "DataHub GMS returned HTTP $($health.StatusCode)" }

    Write-Host "[DBT] Running dbt source freshness..." -ForegroundColor Yellow
    docker exec $scheduler dbt source freshness --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project
    if ($LASTEXITCODE -ne 0) { throw "dbt source freshness failed." }

    Write-Host "[DBT] Running dbt build..." -ForegroundColor Yellow
    docker exec $scheduler dbt build --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project
    if ($LASTEXITCODE -ne 0) { throw "dbt build failed." }

    Write-Host "[DBT] Running dbt docs generate..." -ForegroundColor Yellow
    docker exec $scheduler dbt docs generate --project-dir /usr/local/airflow/include/dbt_project --profiles-dir /usr/local/airflow/include/dbt_project
    if ($LASTEXITCODE -ne 0) { throw "dbt docs generate failed." }

    $requiredArtifacts = @(
        "$DbtDir/target/manifest.json",
        "$DbtDir/target/catalog.json",
        "$DbtDir/target/run_results.json",
        "$DbtDir/target/sources.json"
    )
    foreach ($artifact in $requiredArtifacts) {
        if (-not (Test-Path $artifact)) { throw "Missing required dbt artifact: $artifact" }
        Write-Host "[OK] Found $artifact" -ForegroundColor Green
    }

    Write-Host "[DATAHUB] Ingesting dbt metadata..." -ForegroundColor Cyan
    & $DatahubCmd ingest -c metadata/recipes/dbt_recipe.yml
    if ($LASTEXITCODE -ne 0) { throw "dbt ingestion failed." }

    Write-Host "[DATAHUB] Ingesting ClickHouse metadata..." -ForegroundColor Cyan
    & $DatahubCmd ingest -c metadata/recipes/clickhouse_recipe.yml
    if ($LASTEXITCODE -ne 0) { throw "ClickHouse ingestion failed." }

    Write-Host "[DATAHUB] Publishing mart freshness properties..." -ForegroundColor Cyan
    & $PythonCmd metadata/scripts/publish_freshness.py
    if ($LASTEXITCODE -ne 0) { throw "freshness publishing failed." }

    Write-Host "Metadata ingestion completed." -ForegroundColor Green
    Write-Host "Open http://localhost:9002 and search for gold_app_daily_cost or fct_llm_interactions." -ForegroundColor Cyan
}
finally {
    Pop-Location
}
