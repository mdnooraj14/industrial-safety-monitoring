# Start the Streamlit dashboard on http://localhost:8501 (the API must be running)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $env:API_BASE_URL) { $env:API_BASE_URL = "http://localhost:8000" }
& ".\.venv\Scripts\python.exe" -m streamlit run frontend\streamlit_app.py --server.address localhost --server.port 8501
