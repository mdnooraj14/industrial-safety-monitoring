# REST API

Generated from the FastAPI OpenAPI specification by `scripts/export_api_docs.py` (do not edit by hand).
Interactive documentation: start the server and open **http://localhost:8000/docs** (Swagger UI) or `/redoc`;
the raw spec is `docs/openapi.json` and `/openapi.json`.

Conventions
- Base path `/api/v1` (plus `/health`). JSON in, JSON out, timestamps ISO-8601 UTC.
- Lists are paginated: `?page=1&page_size=50` (max 500) -> `{items, total, page, page_size, pages}`; filters are query parameters.
- Errors use one envelope: `{"error": {"code": "...", "message": "...", "details": [...]}}`.

| Status | Meaning | Example codes |
|---|---|---|
| 200 / 201 | success / created | |
| 404 | record not found | `NOT_FOUND` |
| 405 | method not allowed (history tables have no PUT / PATCH / DELETE) | `HTTP_ERROR` |
| 409 | duplicate or illegal state change | `DUPLICATE`, `ALREADY_ACKNOWLEDGED`, `INCIDENT_EXISTS`, `INVALID_TRANSITION`, `DUPLICATE_ALARM`, `INCIDENT_CLOSED` |
| 422 | validation or business-rule violation | `VALIDATION_ERROR`, `ACK_BEFORE_ALARM`, `NOT_VERIFIED`, `NO_UNSAFE_STATE`, `EPISODE_TOO_SHORT`, `NO_QUALIFYING_ALARM`, `TIMESTAMP_ORDER`, `PLANT_MISMATCH` |
| 500 | unexpected error (no internals leaked) | `INTERNAL_ERROR` |


**53 operations across 42 paths.**

## plants

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/plants` | List plants | lifecycle_status, industry_type, q |  |
| POST | `/api/v1/plants` | Create a plant |  | PlantCreate |
| GET | `/api/v1/plants/{plant_id}` | Get a plant |  |  |

## machines

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/machines` | List machines | plant_id, machine_type, lifecycle_status, criticality_class, q |  |
| POST | `/api/v1/machines` | Create a machine |  | MachineCreate |
| GET | `/api/v1/machines/status` | Operational status of machines (latest machine-state event each) | plant_id, machine_id |  |
| GET | `/api/v1/machines/{machine_id}` | Get a machine |  |  |
| GET | `/api/v1/machines/{machine_id}/interlocks` | Interlocks protecting a machine |  |  |
| GET | `/api/v1/machines/{machine_id}/safety-zones` | Safety zones linked to a machine (bridge table) |  |  |
| GET | `/api/v1/machines/{machine_id}/state-events` | Machine-state history of one machine | safety_assessment, from_ts, to_ts, order |  |

## safety zones

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/safety-zones` | List safety zones | plant_id, risk_level, zone_type, machine_id |  |
| POST | `/api/v1/safety-zones` | Create a safety zone (optionally linking machines) |  | SafetyZoneCreate |
| GET | `/api/v1/safety-zones/{zone_id}` | Get a safety zone |  |  |
| GET | `/api/v1/safety-zones/{zone_id}/machines` | Machines linked to a zone (bridge rows) |  |  |

## interlocks

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/interlocks` | List interlocks | machine_id, plant_id, interlock_type, is_required, safety_zone_id |  |
| POST | `/api/v1/interlocks` | Create an interlock |  | InterlockCreate |
| GET | `/api/v1/interlocks/{interlock_id}` | Get an interlock |  |  |
| GET | `/api/v1/interlocks/{interlock_id}/state-events` | Status history of an interlock (append-only) | from_ts, to_ts, order |  |

## alarm definitions

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/alarm-definitions` | List alarm definitions | trigger_type, default_severity, lifecycle_status, machine_type, is_safety_related, q |  |
| POST | `/api/v1/alarm-definitions` | Create an alarm definition |  | AlarmDefinitionCreate |
| GET | `/api/v1/alarm-definitions/severity-policy` | Severity policy: priority rank and acknowledgement deadline per severity |  |  |
| GET | `/api/v1/alarm-definitions/{definition_id}` | Get an alarm definition |  |  |

## operator shifts

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/operator-shifts` | List operator shifts | plant_id, shift_date, shift_code, employee_id, machine_id, covering |  |
| POST | `/api/v1/operator-shifts` | Create an operator shift (optionally assigning machines) |  | OperatorShiftCreate |
| GET | `/api/v1/operator-shifts/{shift_id}` | Get a shift with the machines it covers |  |  |

## machine state events

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/machine-state-events` | List machine-state events (append-only history) | machine_id, plant_id, safety_assessment, unsafe_rule_code, from_ts, to_ts, order |  |
| POST | `/api/v1/machine-state-events` | Record a machine-state reading |  | MachineStateCreate |
| GET | `/api/v1/machine-state-events/{event_id}` | Get a machine-state event with its interlock events |  |  |

## alarm events

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/alarm-events` | List alarm events | machine_id, plant_id, severity, alarm_definition_id, lifecycle_status, acknowledged, overdue_before, from_ts, to_ts, sort, order |  |
| POST | `/api/v1/alarm-events` | Raise an alarm |  | AlarmCreate |
| GET | `/api/v1/alarm-events/{alarm_event_id}` | Get an alarm event with its acknowledgement |  |  |
| GET | `/api/v1/alarm-events/{alarm_event_id}/trace` | Trace an alarm to its machine state, operator, acknowledgement, incident and corrective actions | cutoff |  |

## acknowledgements

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/acknowledgements` | List acknowledgements | alarm_event_id, machine_id, ack_status, shift_id, min_delay_seconds, from_ts, to_ts, order |  |
| POST | `/api/v1/acknowledgements` | Acknowledge an alarm |  | AcknowledgementCreate |
| GET | `/api/v1/acknowledgements/{acknowledgement_id}` | Get an acknowledgement |  |  |

## incidents

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/incidents` | List incidents | machine_id, plant_id, severity, status, trigger_rule_code, open, from_ts, to_ts, order |  |
| POST | `/api/v1/incidents` | Create an incident from an unsafe machine state (rule IN-1) |  | IncidentCreate |
| GET | `/api/v1/incidents/{incident_id}` | Incident with full traceability |  |  |
| GET | `/api/v1/incidents/{incident_id}/status-history` | Incident lifecycle history (append-only) |  |  |
| POST | `/api/v1/incidents/{incident_id}/transitions` | Move an incident along its lifecycle (IN-3 / IN-4) |  | IncidentTransition |

## corrective actions

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/corrective-actions` | List corrective actions | incident_id, machine_id, plant_id, status, assigned_engineer_id, verification_result, overdue, order |  |
| GET | `/api/v1/corrective-actions/{action_id}` | Get a corrective action with its history |  |  |
| POST | `/api/v1/corrective-actions/{action_id}/transitions` | Move an action along its lifecycle (CA-2 / CA-3 / CA-4) |  | ActionTransition |
| GET | `/api/v1/incidents/{incident_id}/corrective-actions` | Corrective actions of an incident, with histories |  |  |
| POST | `/api/v1/incidents/{incident_id}/corrective-actions` | Plan a corrective action for an incident (CA-1) |  | CorrectiveActionCreate |

## analytics

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/analytics/kpis` | Business KPIs | plant_id, machine_id, severity, date_from, date_to, incident_status, cutoff |  |
| GET | `/api/v1/analytics/kpis/{section}` | One section of the business KPIs | plant_id, machine_id, severity, date_from, date_to, incident_status, cutoff |  |

## validation

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/validation/checks` | List the validation checks |  |  |
| GET | `/api/v1/validation/report` | Run all validations on the database | full, max_errors |  |

## anomaly detection

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/api/v1/anomalies` | Anomalies and patterns found by the detectors | kind, issue_type, record_table |  |
| GET | `/api/v1/anomalies/evaluation` | DETECTION EVALUATION: detectors vs the separate ground truth |  |  |
| GET | `/api/v1/anomalies/summary` | Counts of findings by type |  |  |

## system

| Method | Path | Summary | Filters | Request body |
|---|---|---|---|---|
| GET | `/health` | Liveness and database connectivity |  |  |
