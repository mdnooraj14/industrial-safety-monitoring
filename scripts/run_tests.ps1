# Run the whole automated test suite (needs PostgreSQL and the safety_test database)
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
& "$root\.venv\Scripts\python.exe" -m pytest -q @args
