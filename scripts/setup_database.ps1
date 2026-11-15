# Apply migrations to the dev and test databases, then load the chosen dataset.
#   .\scripts\setup_database.ps1                 # starter dataset
#   .\scripts\setup_database.ps1 -Scale full     # 50,000-event dataset
param([ValidateSet("starter", "full")][string]$Scale = "starter")
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
& "$root\.venv\Scripts\python.exe" -m alembic upgrade head
& "$root\.venv\Scripts\python.exe" -m alembic -x db=safety_test upgrade head
Set-Location $root
if (-not (Test-Path "data\exports\$Scale\csv")) { & ".\.venv\Scripts\python.exe" data_generator\generate.py --scale $Scale }
& ".\.venv\Scripts\python.exe" scripts\load_data.py --scale $Scale --truncate
