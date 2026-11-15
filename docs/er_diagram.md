# ER Diagram and Relationships

Render with any Mermaid viewer (VS Code "Markdown Preview Mermaid Support", GitHub, mermaid.live).

```mermaid
erDiagram
    PLANT ||--o{ MACHINE : "has"
    PLANT ||--o{ SAFETY_ZONE : "defines"
    PLANT ||--o{ OPERATOR_SHIFT : "schedules"
    MACHINE ||--o{ MACHINE_SAFETY_ZONE : ""
    SAFETY_ZONE ||--o{ MACHINE_SAFETY_ZONE : ""
    MACHINE ||--o{ INTERLOCK : "is protected by"
    SAFETY_ZONE |o--o{ INTERLOCK : "guards (optional)"
    INTERLOCK ||--o{ INTERLOCK_STATE_EVENT : "history"
    MACHINE ||--o{ MACHINE_STATE_EVENT : "history"
    MACHINE_STATE_EVENT |o--o{ INTERLOCK_STATE_EVENT : "caused (optional)"
    OPERATOR_SHIFT ||--o{ SHIFT_MACHINE_ASSIGNMENT : ""
    MACHINE ||--o{ SHIFT_MACHINE_ASSIGNMENT : ""
    OPERATOR_SHIFT |o--o{ MACHINE_STATE_EVENT : "on duty (optional)"
    ALARM_SEVERITY_POLICY ||--o{ ALARM_DEFINITION : "default severity"
    ALARM_SEVERITY_POLICY ||--o{ ALARM_EVENT : "severity snapshot"
    ALARM_DEFINITION ||--o{ ALARM_EVENT : "instantiated as"
    MACHINE ||--o{ ALARM_EVENT : "raises"
    INTERLOCK |o--o{ ALARM_EVENT : "source (optional)"
    SAFETY_ZONE |o--o{ ALARM_EVENT : "location (optional)"
    MACHINE_STATE_EVENT |o--o{ ALARM_EVENT : "state in effect (optional)"
    OPERATOR_SHIFT |o--o{ ALARM_EVENT : "on duty (optional)"
    ALARM_EVENT ||--o| ACKNOWLEDGEMENT : "acknowledged by (0..1)"
    OPERATOR_SHIFT ||--o{ ACKNOWLEDGEMENT : "acknowledges"
    MACHINE ||--o{ INCIDENT : "involved in"
    SAFETY_ZONE |o--o{ INCIDENT : "location (optional)"
    MACHINE_STATE_EVENT |o--o{ INCIDENT : "trigger context (optional)"
    OPERATOR_SHIFT |o--o{ INCIDENT : "on duty (optional)"
    INCIDENT ||--o{ INCIDENT_ALARM_LINK : ""
    ALARM_EVENT ||--o{ INCIDENT_ALARM_LINK : ""
    INCIDENT ||--|{ INCIDENT_STATUS_EVENT : "lifecycle"
    INCIDENT ||--o{ CORRECTIVE_ACTION : "remediated by"
    CORRECTIVE_ACTION ||--|{ CORRECTIVE_ACTION_STATUS_EVENT : "lifecycle"
    GROUND_TRUTH_LABEL }o..o{ ALARM_EVENT : "soft reference, schema gt, never joined by detectors"
```

`GROUND_TRUTH_LABEL` lives in Postgres schema `gt` and references records by `(record_table, record_id)` text with **no foreign key**, so it cannot influence features or constraints.

## Cardinality and optionality

| Relationship | Cardinality | Optional side / notes |
|---|---|---|
| Plant → Machine | 1:N | Machine must have a plant |
| Plant → SafetyZone | 1:N | |
| Machine ↔ SafetyZone | **N:M** via `machine_safety_zone` | Effective-dated; both must belong to same plant; no overlapping periods for the same pair |
| Machine → Interlock | 1:N | |
| SafetyZone → Interlock | 1:N | `interlock.safety_zone_id` optional (e.g. machine-wide E-stop); if set, zone must be linked to the machine |
| Interlock → InterlockStateEvent | 1:N | append-only, ordered by `sequence_no` |
| Machine → MachineStateEvent | 1:N | append-only; first event has `previous_state` NULL |
| OperatorShift ↔ Machine | **N:M** via `shift_machine_assignment` | |
| AlarmDefinition → AlarmEvent | 1:N | |
| Machine → AlarmEvent | 1:N | |
| AlarmEvent → MachineStateEvent | N:1 optional | NULL for alarms not tied to a state (e.g. comms loss) |
| AlarmEvent → Acknowledgement | **1:0..1** (one-to-one, optional) | Missing row = unacknowledged |
| OperatorShift → Acknowledgement | 1:N | |
| Machine → Incident | 1:N | |
| Incident ↔ AlarmEvent | **N:M** via `incident_alarm_link` | Normal incident has ≥1 `TRIGGER`; zero links = anomaly 3 |
| Incident → MachineStateEvent | N:1 optional | NULL trigger context = anomaly 3 |
| Incident → IncidentStatusEvent | 1:N (≥1) | History; `incident.status` = latest event |
| Incident → CorrectiveAction | 1:N, optional (0 while open) | |
| CorrectiveAction → CorrectiveActionStatusEvent | 1:N (≥1) | History; `status` = latest event |

## Lifecycle states

| Entity | States | Allowed transitions |
|---|---|---|
| Plant / Machine / SafetyZone / Interlock / AlarmDefinition | `lifecycle_status` + `effective_from` / `effective_to` | ACTIVE → (SUSPENDED/MAINTENANCE/DISABLED/DEPRECATED) → RETIRED; effective ranges never overlap for the same logical row |
| Machine operating state | RUNNING, IDLE, STOPPED, E_STOPPED, FAULT, MAINTENANCE | STOPPED→IDLE,MAINTENANCE · IDLE→RUNNING,STOPPED · RUNNING→IDLE,STOPPED,E_STOPPED,FAULT · E_STOPPED→STOPPED · FAULT→STOPPED · MAINTENANCE→STOPPED |
| Alarm | ACTIVE → CLEARED; acknowledgement is a separate record | ack may occur while ACTIVE or after CLEARED |
| Incident | OPEN → INVESTIGATING → ACTION_IN_PROGRESS → RESOLVED → CLOSED | CLOSED requires all actions CLOSED and ≥1 action |
| Corrective action | PLANNED → IN_PROGRESS → COMPLETED → VERIFIED → CLOSED; PLANNED/IN_PROGRESS → CANCELLED | CLOSED requires verification PASSED; FAILED verification ends the action in VERIFIED and a new `-02` action is created |
