# Start the FastAPI backend on http://localhost:8000  (Swagger UI at /docs)
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
& "$root\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
