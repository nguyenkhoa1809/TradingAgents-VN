$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path $root "web\dist"
$py   = Join-Path $root ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "============================================================"
Write-Host "  MarketWire Preview  ->  http://localhost:8001"
Write-Host "============================================================"
Write-Host ""

Set-Location $dist
& $py -m http.server 8001
