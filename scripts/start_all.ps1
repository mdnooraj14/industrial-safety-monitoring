# One command to start the whole system: API (port 8000) + dashboard (port 8501), then open the browser.
#   .\scripts\start_all.ps1              start everything
#   .\scripts\start_all.ps1 -Stop        stop both servers
#   .\scripts\start_all.ps1 -NoBrowser   start without opening the browser
param([switch]$Stop, [switch]$NoBrowser)

$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

function Stop-Port([int]$port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

if ($Stop) {
    Stop-Port 8000; Stop-Port 8501
    Write-Host "Stopped the API (8000) and the dashboard (8501)."
    return
}

if (-not (Test-Path $python))              { Write-Error "Virtual environment not found. Run the setup steps in README.md first."; return }
if (-not (Test-Path (Join-Path $root ".env"))) { Write-Error ".env not found. Copy .env.example to .env and fill it in."; return }

Stop-Port 8000; Stop-Port 8501                      # make sure the ports are free

# --- API ---
Start-Process -FilePath $python -WorkingDirectory (Join-Path $root "backend") -WindowStyle Minimized `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
Write-Host "Starting the API ..." -NoNewline
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try { if ((Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2).status -eq "ok") { $ok = $true; break } } catch { Write-Host "." -NoNewline }
}
if (-not $ok) { Write-Error "`nThe API did not start. Check PostgreSQL and the settings in .env (run: cd backend; ..\.venv\Scripts\python.exe -m uvicorn app.main:app)."; return }
Write-Host " up  (http://localhost:8000/docs)"

# --- Dashboard ---
$env:API_BASE_URL = "http://localhost:8000"
Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Minimized `
    -ArgumentList "-m", "streamlit", "run", "frontend\streamlit_app.py", "--server.address", "localhost", "--server.port", "8501", "--server.headless", "true"
Write-Host "Starting the dashboard ..." -NoNewline
$ok = $false
for ($i = 0; $i -lt 120; $i++) {                    # Streamlit can take up to a minute on the first start
    Start-Sleep -Milliseconds 500
    try { if ((Invoke-RestMethod "http://127.0.0.1:8501/_stcore/health" -TimeoutSec 5) -eq "ok") { $ok = $true; break } } catch { Write-Host "." -NoNewline }
}
if (-not $ok) { Write-Error "`nThe dashboard did not start."; return }
Write-Host " up  (http://localhost:8501)"

if (-not $NoBrowser) { Start-Process "http://localhost:8501" }
Write-Host "`nRunning. Stop both with:  .\scripts\start_all.ps1 -Stop"
