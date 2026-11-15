# Architecture, Decisions and Roadmap

Status: **all ten milestones complete.** Sections 1-6 below record the design decisions made in Milestone 1; section 7 records what was built and where it differs from the plan.

## 1. Environment findings

| Item | Finding |
|---|---|
| Workspace | `C:\CandidateWorkspace` was empty when the project started; nothing was overwritten. |
| Python | 3.11.6 (meets 3.11+). No project packages installed yet. |
| PostgreSQL | Windows services `postgresql-x64-15` and `postgresql-x64-18` both running; `psql` is not on PATH. |
| Git | 2.33 available; workspace is not yet a repository. |

## 2. Three-tier architecture

```
┌───────────────────────────┐   HTTP/JSON    ┌──────────────────────────────────┐   SQLAlchemy   ┌───────────────┐
│ Tier 1  Streamlit         │ ─────────────▶ │ Tier 2  FastAPI                  │ ─────────────▶ │ Tier 3        │
│ pages/ components/        │  (requests)    │ api → services → repositories    │   (psycopg)    │ PostgreSQL    │
│ api_client.py (only HTTP) │ ◀───────────── │        ↘ business_rules (pure)   │ ◀───────────── │ schema + gt   │
└───────────────────────────┘                │        ↘ analytics / validation  │                └───────────────┘
                                             └──────────────────────────────────┘
        data_generator/ (offline, no DB needed) ──▶ data/exports (CSV/JSON/SQL) ──▶ scripts/load_data.py ──▶ PostgreSQL
```

Layer rules (enforced by import direction, checked in tests):

| Layer | May import | Must not |
|---|---|---|
| `frontend/` | `requests`, `pandas`, `plotly` | import `backend.*`, touch the DB, compute KPIs |
| `api/` (routes) | `schemas`, `services` | contain SQL or rules |
| `services/` | `repositories`, `business_rules`, `analytics`, `validation` | build HTTP responses |
| `repositories/` | `models` (SQLAlchemy) | contain business rules |
| `business_rules/` | stdlib only (dataclasses, enums) | access DB or I/O – so rules are unit-testable in isolation |
| `analytics/`, `validation/` | pandas over DataFrames supplied by repositories | read ground truth during detection |

Key decisions:

1. **Rules engine is pure Python** (`business_rules/`), fed by config (`rules_config.py`/env) so thresholds are configurable and the generator, validator and API all use the *same* rule code. This is what prevents "random" outcomes: the generator calls the rules to derive severities, assessments, incidents and statuses.
2. **Generator is independent of the database.** It writes CSV/JSON/SQL, so it runs without Postgres; a loader script ingests the same files. Starter and full datasets differ only by `--scale` parameters.
3. **Ground truth is physically separated.** Feature tables carry no `issue_type` column. Labels live in `ground_truth_label` in a separate Postgres schema `gt` (and in `data/ground_truth/ground_truth.csv|json`). Detection code reads only the `public` schema; only the evaluation service reads `gt`. This satisfies "include issue_type/ground_truth labels" and "never leak labels into features" together.
4. **History is append-only.** State changes are new rows in `*_event` tables. Current `status` columns on `incident` / `corrective_action` are projections of the latest event row and are reconciled by the validator. DB triggers (Milestone 2) reject UPDATE/DELETE on event tables.
5. **Anomalies are injected by appending** new labelled records (or new event rows) after the baseline is finalised and checksummed, never by mutating baseline rows. IDs for injected rows continue the counters, so baseline IDs are unchanged between "clean" and "with anomalies" builds.
6. **All timestamps are `TIMESTAMPTZ` stored in UTC**; plant timezone is used only for shift windows and display.
7. **Natural, deterministic text primary keys** (as the ID conventions require) so every exported file joins by readable IDs. Bridge tables use composite keys.

## 3. ID scheme (all immutable, zero-padded)

| Entity | Pattern | Example | Counter scope |
|---|---|---|---|
| Plant | `P<nn>` | `P01` | global |
| Machine | `<plant_id>-M<nn>` | `P01-M07` | per plant |
| SafetyZone | `SAFE-<plant_id>-<nnn>` | `SAFE-P01-003` | per plant |
| Interlock | `ILK-<machine_id>-<nn>` | `ILK-P01-M07-02` | per machine |
| AlarmDefinition | `ADEF-<nnnn>` | `ADEF-0042` | global |
| OperatorShift | `SHF-<plant_id>-<yyyymmdd>-<A/B/C>-<nn>` | `SHF-P01-20261003-B-02` | per plant/day/shift |
| MachineStateEvent | `MSE-<machine_id>-<nnnnnn>` | `MSE-P01-M07-000015` | per machine, by event time |
| InterlockStateEvent | `ISE-<interlock_id>-<nnnnnn>` | `ISE-ILK-P01-M07-02-000003` | per interlock |
| AlarmEvent | `ALM-<machine_id>-<nnnnnn>` | `ALM-P01-M07-000001` | per machine, by raised_at |
| Acknowledgement | `ACK-<alarm_event_id>-<nn>` | `ACK-ALM-P01-M07-000001-01` | per alarm |
| Incident | `INC-<year>-<nnnnnn>` | `INC-2026-000001` | global per year, by opened_at |
| CorrectiveAction | `ACT-<incident_id>-<nn>` | `ACT-INC-2026-000001-01` | per incident |
| Status-event tables | `ISTAT-<incident_id>-<nn>`, `ASTAT-<action_id>-<nn>` | | per parent |

Baseline counters are assigned after sorting by event time; injected records take the next counter values.

## 4. Assumptions I made (please confirm or change)

| # | Assumption | Why |
|---|---|---|
| A1 | Project root is `C:\CandidateWorkspace\industrial-safety-monitoring\` | Matches your structure; keeps the workspace clean. |
| A2 | Generation window: **2026-10-01 00:00Z → 2026-10-31 00:00Z** (30 days), configurable | Keeps `INC-2026-…` valid; seed 20261022 unchanged. |
| A3 | Acknowledgement is 0..1 per alarm (suffix `-01` reserved for future re-acknowledgement) | Simplest model that honours `ACK-<alarm-id>-01`. |
| A4 | SafetyZone belongs to a Plant; Machine↔SafetyZone is N:M via a bridge | Needed because the zone ID embeds the plant, not the machine. |
| A5 | 19 tables in total: your 10 + `machine_state_event`, plus 8 extras: 2 bridges (`machine_safety_zone`, `shift_machine_assignment`) and the `incident_alarm_link` bridge, `interlock_state_event`, `incident_status_event`, `corrective_action_status_event`, `alarm_severity_policy`, and `gt.ground_truth_label` (see ER doc) | Required by "bridge tables", "retain history", "configurable severity deadlines", "separate ground truth". |
| A6 | Derived columns (e.g. `ack_delay_seconds`, `safety_assessment`) are stored at write time by the rules engine and re-derived by the validator | Fast dashboards, yet verifiable ("not independently generated"). |
| A7 | Operator and engineer names come from Faker (en_IN/en_US mix) and are purely fictional | No personal data. |
| A9 | **Decision change (M2):** PostgreSQL **15** on port 5432 is used instead of 18 (the 18 superuser password was lost and the machine is organisation-managed). The code uses nothing version-specific, so it runs on 15 or 18 by changing `DB_PORT`. | Unblocked Milestone 2 without admin rights. |
| A8 | Tests run against a dedicated Postgres database `safety_test`, not SQLite | Constraints, `TIMESTAMPTZ` and schemas must behave as in production. |

## 5. Open questions for you

1. **Which PostgreSQL to use – 15 or 18?** Both services are running. I recommend **18** (newest) unless you already use 15 for other work. I need host/port and a superuser password to create `safety_db` and `safety_test`; I'll read these from `.env` and never commit them.
2. Confirm assumptions A1–A8, especially A1 (subfolder) and A2 (dates).

## 6. Roadmap (all done)

| M | Scope | Result |
|---|---|---|
| 1 | Architecture, ER diagram, schema, data dictionary, business rules | `docs/` design documents |
| 2 | venv, SQLAlchemy models, Alembic migration 0001, append-only triggers, indexes | 19 tables, 163 constraints, 44 tests |
| 3 | Generator at starter scale, CSV / JSON / SQL export, loader | deterministic IDs, FK-valid data |
| 4 | Business rules and machine-state event processing | `business_rules/`, state service, rule unit tests |
| 5 | Five anomalies, separate ground truth, detectors | `anomaly_injector.py`, `anomaly_detectors.py` |
| 6 | Validation module and report | 17 validations |
| 7 | Business KPIs and detection evaluation | `analytics/` |
| 8 | FastAPI backend | 53 operations, Swagger |
| 9 | Streamlit dashboard | 6 pages, REST only |
| 10 | Full 50,000-event dataset, reproducibility, documentation, acceptance | `docs/reproducibility_report.md`, `docs/acceptance_report.md`, README |

## 7. As built - differences from the plan

* **Database:** PostgreSQL **15** on port 5432 (not 18): the PostgreSQL 18 superuser password was unavailable on the organisation-managed machine (assumption A9). Nothing in the code is version-specific.
* **Business rules were built in Milestone 3** (the generator needs them), then completed and unit-tested in Milestone 4.
* **State-event semantics:** a transition to the *same* state is allowed (records a door / interlock change); a machine that is not running is SAFE even with the door open; a failed-verification action becomes CANCELLED (superseded) when its follow-up is planned (`business_rules.md` sections 1, 4).
* **Incident and action timelines** emerge from per-severity timing ranges truncated at the data cut-off.
* **Alarm total:** 50,000 is the *baseline* alarm count; injected anomalies add 219 labelled alarms at full scale.
* **API cache:** analytics and validation results are cached in memory keyed by a fingerprint of the data, so repeated dashboard requests are fast and writes show up immediately.
* **Not introduced (per the instructions):** Docker, cloud services, paid APIs, AI models, microservices.
