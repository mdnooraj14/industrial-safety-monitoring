# Industrial Safety Monitoring & Incident Analysis System

Analyses safety events, machine states, alarms, operator acknowledgements, incidents and corrective actions to find
**repeated unsafe conditions**, with full traceability from an alarm to its machine state, operator acknowledgement, incident and
corrective action. Built as a three-tier application on free and open-source tools.

```
 Streamlit dashboard  --REST-->  FastAPI + business rules  --SQLAlchemy-->  PostgreSQL
 (frontend/)                     (backend/app)                               (schema + gt schema)
        data_generator/ (offline, no DB needed) --> CSV / JSON / SQL exports --> scripts/load_data.py --> PostgreSQL
```

| Tier | Technology | Where |
|---|---|---|
| Presentation | Streamlit, Plotly (talks to the API only) | `frontend/` |
| Application | Python 3.11, FastAPI, Pydantic; rules, analytics, validation | `backend/app/` |
| Data | PostgreSQL, SQLAlchemy, Alembic | `backend/app/models`, `backend/migrations` |
| Synthetic data | Python, Faker, NumPy (seed 20261022) | `data_generator/` |

## Start everything with one command

Once the one-time setup below has been done (and a dataset loaded), this starts the API and the dashboard and opens the browser:

```powershell
cd C:\CandidateWorkspace\industrial-safety-monitoring
.\scripts\start_all.ps1            # add -NoBrowser to skip opening the browser
.\scripts\start_all.ps1 -Stop      # stop both servers
```

(If PowerShell blocks scripts: `powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1`.)

## Quick start (Windows, PowerShell)

Prerequisites: Python 3.11+, PostgreSQL (15 or newer), PowerShell. No Docker is needed.

```powershell
cd C:\CandidateWorkspace\industrial-safety-monitoring

# 1. Python environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt          # or requirements-lock.txt for the exact versions used

# 2. Configuration: copy the template and fill in the PostgreSQL port and passwords
Copy-Item .env.example .env
notepad .env                    # DB_PORT (5432 for PostgreSQL 15), DB_PASSWORD, PG_ADMIN_PASSWORD

# 3. Create the role and the two databases (safety_db, safety_test) - needs the postgres superuser password
.\.venv\Scripts\python.exe scripts\create_databases.py
#    (alternatively create role safety_app and the databases in pgAdmin, owner safety_app)

# 4. Create the schema (migrations) in both databases
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m alembic -x db=safety_test upgrade head
cd ..

# 5. Generate the data and load it (starter = 2 plants / 8 machines / 2,000 alarms; full = 4 / 60 / 50,000)
.\.venv\Scripts\python.exe data_generator\generate.py --scale starter
.\.venv\Scripts\python.exe scripts\load_data.py --scale starter --truncate
#    full dataset: replace "starter" with "full" in both commands (about 40 s to generate, 17 s to load)

# 6. Start the API (terminal 1)  ->  http://localhost:8000/docs
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# 7. Start the dashboard (terminal 2)  ->  http://localhost:8501
cd C:\CandidateWorkspace\industrial-safety-monitoring
$env:API_BASE_URL = "http://localhost:8000"
.\.venv\Scripts\python.exe -m streamlit run frontend\streamlit_app.py --server.address localhost
```

The same steps are wrapped in `scripts\setup_database.ps1 -Scale full`, `scripts\run_api.ps1` and `scripts\run_dashboard.ps1`
(if PowerShell blocks scripts: `powershell -ExecutionPolicy Bypass -File scripts\run_api.ps1`).

### Tests

```powershell
cd C:\CandidateWorkspace\industrial-safety-monitoring\backend
..\.venv\Scripts\python.exe -m pytest -q          # 289 tests, about 2 minutes; needs PostgreSQL and the safety_test database
```

### Reports and proofs (regenerate any time)

```powershell
cd C:\CandidateWorkspace\industrial-safety-monitoring
.\.venv\Scripts\python.exe scripts\validate.py --source generate --scale full         # docs\validation_report.md
.\.venv\Scripts\python.exe scripts\evaluate.py --source generate --scale full         # docs\evaluation_metrics.md
.\.venv\Scripts\python.exe scripts\reproducibility_check.py --scale full              # docs\reproducibility_report.md (~100 s)
.\.venv\Scripts\python.exe scripts\build_manifest.py                                  # docs\source_manifest.md
.\.venv\Scripts\python.exe scripts\export_api_docs.py                                 # docs\api.md, docs\openapi.json
.\.venv\Scripts\python.exe scripts\acceptance.py --run-tests                          # docs\acceptance_report.md
```

## What it does

* **Data model:** 19 tables - Plant, Machine, SafetyZone, Interlock, AlarmDefinition, AlarmEvent, OperatorShift, Acknowledgement,
  Incident, CorrectiveAction, plus MachineStateEvent, three bridge tables, interlock / incident / action history tables, the severity
  policy and the separate ground-truth table. History is append-only (database triggers). Deterministic IDs: `SAFE-P01-001`,
  `ALM-P01-M01-000001`, `ACK-<alarm>-01`, `INC-2026-000001`, `ACT-<incident>-01`.
* **Business rules** (configurable, `docs/business_rules.md`): unsafe machine states, alarm severity classification and acknowledgement
  deadlines, flood and nuisance detection, incident creation, incident and corrective-action lifecycles (no closing without verification).
* **Synthetic data:** 4 plants, 60 machines, 200 alarm definitions, 50,000 baseline alarm events over 30 days, with floods, delayed
  acknowledgements, nuisance alarms and genuine high-severity incidents; five labelled anomalies injected on top; ground truth kept apart.
* **Validation:** 17 validations (keys, joins, enums, IDs, time order, transitions, interlock combinations, acknowledgement timelines,
  derived values, incident and action lifecycles, duplicates, reconciliation, cardinality).
* **Analytics:** business KPIs (alarms, acknowledgement delay, floods, nuisance clusters, repeated unsafe conditions, MTTR, action
  completion and verification) are kept separate from detector evaluation (precision, recall, F1, confusion matrices).
* **API:** 53 operations with filters, pagination, Swagger; **dashboard:** 6 pages with shared filters and drill-downs.

## Documentation

| Document | Content |
|---|---|
| `docs/architecture.md` | architecture, decisions, assumptions, roadmap, as-built differences |
| `docs/er_diagram.md` | ER diagram (Mermaid), cardinalities, lifecycles |
| `docs/data_dictionary.md` | every field: type, required, allowed values, units, keys, validation (checked against the schema by a test) |
| `docs/business_rules.md` | all rules, thresholds, anomaly definitions, KPI and metric definitions, API behaviour |
| `docs/api.md`, `docs/openapi.json` | endpoint reference (generated) |
| `docs/dashboard.md` | dashboard pages and the API each uses |
| `docs/validation_report.md` | validation results on the clean baseline and with anomalies |
| `docs/evaluation_metrics.md` | business KPIs (Part A) and detector evaluation (Part B) |
| `docs/reproducibility_report.md` | fixed-seed reproducibility proof |
| `docs/source_manifest.md` | data provenance, files and checksums, software and licences |
| `docs/acceptance_report.md` | requirement-by-requirement acceptance results |

## Project layout

```
backend/app/   api/ (routes, deps)  schemas/  services/  repositories/  models/  business_rules/  analytics/  validation/  core/  main.py
backend/migrations/  backend/tests/
frontend/      streamlit_app.py  pages/  components/  api_client.py
data_generator/  generate.py  generators/  anomaly_injector.py  ground_truth.py  export.py  loader.py  config.py
data/          exports/<scale>[_baseline]/{csv,json,sql}  ground_truth/<scale>/  processed/  raw/
docs/  scripts/  requirements.txt  requirements-lock.txt  .env.example
```

## Notes and troubleshooting

* **Which PostgreSQL / port?** Check `DB_PORT` in `.env`. On the development machine PostgreSQL 15 listens on 5432 and PostgreSQL 18 on 5433.
* **Lost the `postgres` password?** Use pgAdmin (if it is already connected) or ask your administrator; the application itself only needs the
  `safety_app` role created in step 3.
* **Large files:** `data/exports/full` and `full_baseline` are about 135 MB each (CSV, JSON and SQL). They are regenerated identically by
  `generate.py --scale full`, so they can be deleted and recreated.
* **Ground truth** is stored in `data/ground_truth/<scale>/` and the database schema `gt`. Only the evaluation endpoint and report read it.
* **Passwords** live only in `.env`, which is excluded from version control.
