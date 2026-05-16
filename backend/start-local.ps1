# Local development startup script for Windows
# Usage: cd backend; .\start-local.ps1

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot

# Allow all CORS origins in local dev (Expo Go, emulator, physical device)
$env:ALLOW_ALL_CORS = "true"
$env:APP_ENV       = "development"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Predictrix API — LOCAL DEV MODE" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " .env  : $scriptDir\config\.env  (auto-loaded)"
Write-Host " CORS  : ALL ORIGINS ALLOWED"
Write-Host " Port  : 5000"
Write-Host ""

# Activate virtual environment if it exists
$venvPaths = @(
    "$scriptDir\.venv\Scripts\Activate.ps1",
    "$scriptDir\venv\Scripts\Activate.ps1",
    (Join-Path (Split-Path $scriptDir) ".venv\Scripts\Activate.ps1"),
    (Join-Path (Split-Path $scriptDir) "venv\Scripts\Activate.ps1")
)
foreach ($venv in $venvPaths) {
    if (Test-Path $venv) {
        Write-Host "[+] Activating venv: $venv" -ForegroundColor Green
        & $venv
        break
    }
}

Set-Location $scriptDir
python app.py
