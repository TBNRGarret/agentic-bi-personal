# Start the pinned local DataHub stack for Sprint 1 metadata.

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Root

try {
    if (-not (Test-Path ".env.datahub-vps")) {
        Copy-Item ".env.datahub-vps.example" ".env.datahub-vps"
        Write-Host "[INFO] Created .env.datahub-vps from example for local DataHub defaults." -ForegroundColor Yellow
    }

    docker compose --env-file .env.datahub-vps -f docker-compose-datahub.yml -p datahub-vps up -d
    if ($LASTEXITCODE -ne 0) { throw "DataHub docker compose start failed." }

    Write-Host "[WAIT] Waiting for DataHub GMS at http://localhost:28080/health..." -ForegroundColor Cyan
    $healthy = $false
    for ($i = 0; $i -lt 90; $i++) {
        try {
            $health = Invoke-WebRequest -Uri "http://localhost:28080/health" -UseBasicParsing -TimeoutSec 5
            if ($health.StatusCode -ge 200 -and $health.StatusCode -lt 300) {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 5
        }
    }
    if (-not $healthy) { throw "DataHub GMS did not become healthy at http://localhost:28080/health" }

    & (Join-Path $Root "scripts\connect-networks.ps1")

    Write-Host "DataHub started successfully." -ForegroundColor Green
    Write-Host "UI:      http://localhost:9002   (datahub/datahub)" -ForegroundColor Cyan
    Write-Host "GMS API: http://localhost:28080" -ForegroundColor Cyan
    Write-Host "Run metadata ingestion: .\metadata\scripts\run-ingestion.ps1" -ForegroundColor Cyan
}
finally {
    Pop-Location
}
