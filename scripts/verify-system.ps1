# Strict Sprint 1 acceptance verification wrapper.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $Root
try {
    $PythonCmd = "python"
    $winVenv = Join-Path $Root ".venv\Scripts\python.exe"
    $unixVenv = Join-Path $Root ".venv/bin/python"
    if (Test-Path $winVenv) { $PythonCmd = $winVenv }
    elseif (Test-Path $unixVenv) { $PythonCmd = $unixVenv }

    & $PythonCmd scripts/verify_system.py @args
    if ($LASTEXITCODE -ne 0) { throw "Sprint 1 verification failed." }
}
finally {
    Pop-Location
}
