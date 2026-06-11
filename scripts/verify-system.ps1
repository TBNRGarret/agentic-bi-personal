# Verify the local Agentic BI stack and trigger the smoke + central DWH DAGs.

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $Root

function Invoke-HealthCheck {
    param(
        [string]$Name,
        [string]$Uri
    )

    $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "$Name returned HTTP $($response.StatusCode)"
    }
    Write-Host "[OK] $Name reachable at $Uri" -ForegroundColor Green
}

function Get-AgenticAirflowContainer {
    param([string]$Role)

    $container = docker ps --format "{{.Names}}" |
        Where-Object { $_ -match "^agentic-bi_.*-$Role-1$" } |
        Select-Object -First 1

    if (-not $container) {
        throw "Could not find Agentic BI Airflow $Role container. Run .\scripts\start-system.ps1 first."
    }
    return $container
}

function Get-AgenticAirflowWebUrl {
    $apiServer = Get-AgenticAirflowContainer "api-server"
    $published = docker port $apiServer 8080/tcp | Select-Object -First 1
    if (-not $published) {
        throw "Could not find published Airflow API server port for $apiServer."
    }

    $port = ($published -split ":")[-1]
    return "http://localhost:$port"
}

function Convert-AirflowCliJson {
    param([string[]]$Output)

    $jsonLines = @()
    $capturing = $false
    foreach ($line in $Output) {
        $trimmed = $line.TrimStart()
        if (-not $capturing -and ($trimmed.StartsWith("[") -or $trimmed.StartsWith("{"))) {
            $capturing = $true
        }
        if ($capturing) {
            $jsonLines += $line
        }
    }

    if (-not $jsonLines) {
        throw "Airflow CLI did not return JSON. Raw output:`n$($Output -join "`n")"
    }

    return ($jsonLines -join "`n") | ConvertFrom-Json
}

function Invoke-AirflowDagAndWait {
    param(
        [string]$DagId,
        [string]$RunIdPrefix
    )

    $runId = "$RunIdPrefix$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
    docker exec $scheduler airflow dags unpause $DagId | Out-Null
    docker exec $scheduler airflow dags trigger $DagId --run-id $runId
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to trigger $DagId"
    }

    $state = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $runsOutput = docker exec $scheduler airflow dags list-runs $DagId -o json
        $runs = Convert-AirflowCliJson $runsOutput
        $run = $runs | Where-Object { $_.run_id -eq $runId } | Select-Object -First 1
        if ($run) {
            $state = $run.state
            if ($state -in @("success", "failed")) {
                break
            }
        }
    }

    docker exec $scheduler airflow tasks states-for-dag-run $DagId $runId -o table
    if ($state -ne "success") {
        throw "$DagId finished with state '$state'"
    }

    Write-Host "[OK] $DagId completed successfully ($runId)." -ForegroundColor Green
    return $runId
}

function Invoke-ClickHouseQuery {
    param([string]$Query)

    $password = $env:AGENTIC_CLICKHOUSE_PASSWORD
    if (-not $password) {
        $password = "securepassword"
    }

    $output = docker exec agentic_clickhouse clickhouse-client --password $password --query $Query
    if ($LASTEXITCODE -ne 0) {
        throw "ClickHouse query failed: $Query"
    }
    return $output
}

try {
    $scheduler = Get-AgenticAirflowContainer "scheduler"
    $airflowUrl = Get-AgenticAirflowWebUrl

    Invoke-HealthCheck "Airflow API server" $airflowUrl
    Invoke-HealthCheck "Agentic ClickHouse" "http://localhost:18123/ping"
    Invoke-HealthCheck "MinIO API" "http://localhost:19001/minio/health/live"
    Invoke-HealthCheck "Langfuse API" "http://localhost:3000/api/public/health"

    Invoke-AirflowDagAndWait "smoke_dag" "manual__smoke_verify_"
    Invoke-AirflowDagAndWait "agentic_bi_pipeline" "manual__agentic_bi_verify_"

    $batchCount = [int](Invoke-ClickHouseQuery "SELECT count() FROM vsf_raw.raw_ingestion_batches WHERE status = 'success'")
    if ($batchCount -lt 1) {
        throw "No successful raw ingestion batches found in vsf_raw.raw_ingestion_batches"
    }
    Write-Host "[OK] Successful raw ingestion batches: $batchCount" -ForegroundColor Green

    $goldCount = [int](Invoke-ClickHouseQuery "SELECT count() FROM vsf_mart.gold_app_daily_cost")
    if ($goldCount -lt 1) {
        throw "No rows found in vsf_mart.gold_app_daily_cost"
    }
    Write-Host "[OK] vsf_mart.gold_app_daily_cost rows: $goldCount" -ForegroundColor Green
}
finally {
    Pop-Location
}
