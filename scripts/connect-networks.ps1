# Connect Agentic BI Astro CLI Airflow containers to external Docker networks.
# Run AFTER `astro dev start` completes successfully.

$ErrorActionPreference = "Continue"

# Base networks
$networks = @("dwh_network", "signoz-net")

# Auto-detect DataHub network (created by datahub docker quickstart)
$datahubNet = docker network ls --format '{{.Name}}' |
    Where-Object { $_ -match "datahub" } |
    Select-Object -First 1

if ($datahubNet) {
    $networks += $datahubNet
    Write-Host "Auto-detected DataHub network: $datahubNet" -ForegroundColor Green
} else {
    Write-Host "No DataHub network found — skipping DataHub bridge" -ForegroundColor Yellow
}

# Find Airflow containers created by this Astro project.
$containers = docker ps --format '{{.Names}}' | Where-Object {
    $_ -match "^agentic-bi_.*-(scheduler|api-server|triggerer|dag-processor)-1$"
}

if (-not $containers) {
    Write-Host "No Airflow containers found. Run 'astro dev start' first." -ForegroundColor Red
    exit 1
}

foreach ($net in $networks) {
    $exists = docker network ls --format '{{.Name}}' | Where-Object { $_ -eq $net }
    if (-not $exists) {
        Write-Host "Network '$net' does not exist - skipping" -ForegroundColor Yellow
        continue
    }
    foreach ($c in $containers) {
        docker network connect $net $c 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Connected $c to $net" -ForegroundColor Green
        } else {
            Write-Host "$c already on $net (or error)" -ForegroundColor Yellow
        }
    }
}

Write-Host "Done. Agentic BI Airflow DAGs can now reach external services by container name." -ForegroundColor Cyan
