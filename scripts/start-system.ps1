# Start the local Agentic BI Airflow stack and wire external service networks.

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $Root

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

function Get-AgenticAirflowWebUrl {
    $apiServer = Get-AgenticAirflowContainer "api-server"
    $published = docker port $apiServer 8080/tcp | Select-Object -First 1
    if (-not $published) {
        throw "Could not find published Airflow API server port for $apiServer."
    }

    $port = ($published -split ":")[-1]
    return "http://localhost:$port"
}

try {
    astro dev start
    if ($LASTEXITCODE -ne 0) {
        throw "astro dev start failed with exit code $LASTEXITCODE"
    }

    & (Join-Path $PSScriptRoot "connect-networks.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "connect-networks.ps1 failed with exit code $LASTEXITCODE"
    }

    $airflowUrl = Get-AgenticAirflowWebUrl
    Write-Host "Agentic BI local stack started." -ForegroundColor Green
    Write-Host "Airflow UI: $airflowUrl" -ForegroundColor Cyan
    Write-Host "Agentic ClickHouse HTTP: http://localhost:18123/ping" -ForegroundColor Cyan
    Write-Host "Run verification: .\scripts\verify-system.ps1" -ForegroundColor Cyan
}
finally {
    Pop-Location
}
