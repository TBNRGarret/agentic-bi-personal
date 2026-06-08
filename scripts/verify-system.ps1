# Verify the local Agentic BI stack and trigger the smoke DAG.

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

try {
    $scheduler = Get-AgenticAirflowContainer "scheduler"
    $airflowUrl = Get-AgenticAirflowWebUrl

    Invoke-HealthCheck "Airflow API server" $airflowUrl
    Invoke-HealthCheck "Agentic ClickHouse" "http://localhost:18123/ping"
    Invoke-HealthCheck "Langfuse API" "http://localhost:3000/api/public/health"

    $runId = "manual__verify_$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
    docker exec $scheduler airflow dags unpause smoke_dag | Out-Null
    docker exec $scheduler airflow dags trigger smoke_dag --run-id $runId
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to trigger smoke_dag"
    }

    $state = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $runsOutput = docker exec $scheduler airflow dags list-runs smoke_dag -o json
        $runs = Convert-AirflowCliJson $runsOutput
        $run = $runs | Where-Object { $_.run_id -eq $runId } | Select-Object -First 1
        if ($run) {
            $state = $run.state
            if ($state -in @("success", "failed")) {
                break
            }
        }
    }

    if ($state -ne "success") {
        docker exec $scheduler airflow tasks states-for-dag-run smoke_dag $runId -o table
        throw "smoke_dag finished with state '$state'"
    }

    docker exec $scheduler airflow tasks states-for-dag-run smoke_dag $runId -o table
    Write-Host "[OK] smoke_dag completed successfully." -ForegroundColor Green
}
finally {
    Pop-Location
}
