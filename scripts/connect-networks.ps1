# Connect Agentic BI Astro Airflow containers to the optional DataHub network.
# Run after `astro dev start` and after DataHub has been started when metadata ingestion needs Docker DNS.

$ErrorActionPreference = "Continue"

$datahubNet = docker network ls --format '{{.Name}}' |
    Where-Object { $_ -match "datahub" } |
    Select-Object -First 1

if (-not $datahubNet) {
    Write-Host "No DataHub network found - skipping optional DataHub bridge" -ForegroundColor Yellow
    exit 0
}

$containers = docker ps --format '{{.Names}}' | Where-Object {
    $_ -match "^agentic-bi_.*-(scheduler|api-server|triggerer|dag-processor)-1$"
}

if (-not $containers) {
    Write-Host "No Airflow containers found. Run 'astro dev start' first." -ForegroundColor Red
    exit 1
}

foreach ($c in $containers) {
    docker network connect $datahubNet $c 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Connected $c to $datahubNet" -ForegroundColor Green
    } else {
        Write-Host "$c already on $datahubNet (or error)" -ForegroundColor Yellow
    }
}

Write-Host "Done. DataHub bridge checked." -ForegroundColor Cyan
