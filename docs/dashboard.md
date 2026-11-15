# Streamlit dashboard (Tier 1)

The dashboard is a pure client of the REST API: `frontend/api_client.py` is the only module that does HTTP, no frontend file
imports the backend, SQLAlchemy or the generator (a test enforces this), and every number on screen comes from an API response.
It has no rules or KPI formulas of its own; it only lays results out. Responses are cached for 20 seconds per request.

Run (API first, then dashboard):

```powershell
cd C:\CandidateWorkspace\industrial-safety-monitoring\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# second terminal
cd C:\CandidateWorkspace\industrial-safety-monitoring
$env:API_BASE_URL = "http://localhost:8000"
.\.venv\Scripts\python.exe -m streamlit run frontend\streamlit_app.py --server.address localhost
```

| Page | Content | API used |
|---|---|---|
| 1 Executive Dashboard | tiles: plants, machines, alarm events, critical incidents, open incidents, delayed acknowledgements, repeated unsafe conditions; MTTR, floods, nuisance clusters, action rates; alarms per day / plant / machine, incidents by machine, open incidents | `/analytics/kpis` |
| 2 Plants and Machines | plant overview, machine operational status (latest state, door, interlock summary, assessment), machine detail: state-history chart, unsafe events, interlock status, safety zones | `/plants`, `/machines/status`, `/machines/{id}/...`, `/interlocks/{id}/state-events` |
| 3 Alarm Monitoring | alarm timeline, severity distribution and policy, acknowledgement delays (histogram, by severity, slowest), floods, nuisance clusters, repeated patterns, alarm list (priority or time order) with drill-down trace | `/alarm-events`, `/alarm-events/{id}/trace`, `/acknowledgements`, `/analytics/kpis` |
| 4 Incident Management | incident list, details, machine-state context, triggering alarms, operator, lifecycle timeline, corrective actions; warns when trigger context is missing | `/incidents`, `/incidents/{id}` |
| 5 Corrective Actions | engineer, description, status, completion timeline (Gantt), verification, overdue; flags actions closed without verification | `/corrective-actions`, `/analytics/kpis` |
| 6 Data Quality and Anomalies | validation report (17 checks, errors, affected ids), anomaly categories and records, ground-truth comparison with precision / recall / F1, TP / FP / FN and confusion matrices | `/validation/report`, `/anomalies*` |

Filters (sidebar, shared across pages): plant, machine (limited to the chosen plant), severity, date range (UTC), incident status; the API
applies them. Pages show only the filters that make sense for them (page 6 covers the whole database).
The business KPIs (pages 1-5) and the detector evaluation (page 6, clearly labelled) are never mixed.
