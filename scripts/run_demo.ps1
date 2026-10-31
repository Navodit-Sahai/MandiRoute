# scripts/run_demo.ps1
# MandiGuard Demo Launcher (Stream C)
# Clears stale API key from session, sets environment, and starts API + UI services.

Remove-Item env:DATAGOV_KEY -ErrorAction SilentlyContinue

$env:PYTHONPATH = "."
$env:MG_WATCHDOG = "1"
$env:MG_DEMO = "1"
$env:MG_TELEGRAM = "1"
$env:MG_MODE = "live"

Write-Host "Starting MandiGuard FastAPI backend on port 8000..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$env:PYTHONPATH='.'; `$env:MG_WATCHDOG='1'; `$env:MG_DEMO='1'; `$env:MG_TELEGRAM='1'; `$env:MG_MODE='live'; python -m uvicorn app.api.main:app --port 8000"

Start-Sleep -Seconds 2

Write-Host "Starting MandiGuard Streamlit frontend on port 8501..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$env:PYTHONPATH='.'; `$env:MG_WATCHDOG='1'; `$env:MG_DEMO='1'; `$env:MG_TELEGRAM='1'; `$env:MG_MODE='live'; python -m streamlit run app/ui/home.py --server.port 8501"

Write-Host "MandiGuard services launched in separate windows." -ForegroundColor Yellow
