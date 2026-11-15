# Data Dictionary

Conventions
- **Key**: PK = primary key, FK = foreign key, UQ = unique, CK = check constraint.
- **Req**: Y = NOT NULL, N = nullable.
- `ts` = `TIMESTAMPTZ`, stored in UTC. `date` = calendar date. Durations are in **seconds** unless stated.
- `effective_to` is NULL while the row is current. Rule **V-EFF**: `effective_to >= effective_from` when present.
- All `*_at` / `*_ts` columns must lie within the dataset window plus the lifecycle rules in `business_rules.md`.
- "Derived" columns are computed by the rules engine and re-derived by the validator (rule **V-DER**).
- Every table also has `created_at ts Y` (load time, default now); omitted below for brevity. Event tables are append-only.

## 1. plant
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| plant_id | text | Y | `^P[0-9]{2}$` | PK | immutable |
| plant_name | text | Y | ≤80 chars | UQ | non-blank |
| city | text | Y | | | |
| country_code | char(2) | Y | ISO-3166 alpha-2 | | uppercase |
| timezone | text | Y | IANA name | | valid zone |
| industry_type | enum | Y | AUTOMOTIVE, FOOD_PROCESSING, METAL_FABRICATION, CHEMICAL | | |
| lifecycle_status | enum | Y | ACTIVE, COMMISSIONING, DECOMMISSIONED | | |
| effective_from | date | Y | | | V-EFF |
| effective_to | date | N | | | V-EFF |

## 2. machine
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| machine_id | text | Y | `^P[0-9]{2}-M[0-9]{2,3}$` | PK | prefix equals `plant_id` |
| plant_id | text | Y | | FK→plant | |
| machine_name | text | Y | | UQ(plant_id, machine_name) | |
| machine_type | enum | Y | CNC_MILL, HYDRAULIC_PRESS, CONVEYOR, ROBOT_CELL, PACKAGING_LINE, INJECTION_MOLDER, WELDING_STATION | | |
| criticality_class | enum | Y | A (high), B, C | | |
| lifecycle_status | enum | Y | ACTIVE, MAINTENANCE, RETIRED | | |
| commissioned_on | date | Y | | | ≤ effective_from |
| effective_from | date | Y | | | V-EFF |
| effective_to | date | N | | | V-EFF |

## 3. safety_zone
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| safety_zone_id | text | Y | `^SAFE-P[0-9]{2}-[0-9]{3}$` | PK | embedded plant equals `plant_id` |
| plant_id | text | Y | | FK→plant | |
| zone_name | text | Y | | UQ(plant_id, zone_name) | |
| zone_type | enum | Y | GUARDED_CELL, LIGHT_CURTAIN_AREA, PERIMETER, PRESSURE_MAT_AREA | | |
| risk_level | enum | Y | HIGH, MEDIUM, LOW | | |
| lifecycle_status | enum | Y | ACTIVE, SUSPENDED, RETIRED | | |
| effective_from / effective_to | date | Y / N | | | V-EFF |

## 4. machine_safety_zone (bridge, N:M)
| Field | Type | Req | Allowed | Key | Validation |
|---|---|---|---|---|---|
| machine_id | text | Y | | PK, FK→machine | machine and zone same plant |
| safety_zone_id | text | Y | | PK, FK→safety_zone | |
| effective_from | date | Y | | PK | |
| effective_to | date | N | | | V-EFF; no overlap per (machine, zone) |
| is_primary | boolean | Y | | | ≤1 primary per machine at any date |

## 5. interlock
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| interlock_id | text | Y | `^ILK-P[0-9]{2}-M[0-9]{2,3}-[0-9]{2}$` | PK | embedded machine equals `machine_id` |
| machine_id | text | Y | | FK→machine | |
| safety_zone_id | text | N | | FK→safety_zone | if set, zone linked to machine in bridge |
| interlock_type | enum | Y | DOOR_SWITCH, GUARD_LOCK, E_STOP, LIGHT_CURTAIN, PRESSURE_MAT | | |
| is_required | boolean | Y | | | every machine has ≥1 required interlock |
| bypass_allowed | boolean | Y | | | E_STOP must be false |
| lifecycle_status | enum | Y | ACTIVE, DISABLED, RETIRED | | |
| effective_from / effective_to | date | Y / N | | | V-EFF |

## 6. alarm_severity_policy (configurable rule table)
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| severity | enum | Y | CRITICAL, HIGH, MEDIUM, LOW | PK | |
| priority_rank | smallint | Y | 1 (highest) … 4 | UQ | CRITICAL=1 … LOW=4 |
| ack_deadline_seconds | integer | Y | seconds, >0 | | strictly increasing with rank |
| escalation_after_seconds | integer | Y | seconds, ≥ ack_deadline_seconds | | |
| effective_from / effective_to | date | Y / N | | | V-EFF |

## 7. alarm_definition
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| alarm_definition_id | text | Y | `^ADEF-[0-9]{4}$` | PK | |
| alarm_code | text | Y | e.g. `DOOR-OPEN-RUN` | UQ | uppercase/digits/hyphen |
| alarm_name | text | Y | | | |
| description | text | N | | | |
| machine_type | enum | N | as machine.machine_type; NULL = any | | |
| trigger_type | enum | Y | SAFETY_STATE, INTERLOCK_FAULT, PROCESS_LIMIT, COMMUNICATION, MAINTENANCE_DUE | | |
| interlock_type | enum | N | as interlock.interlock_type | | required when trigger_type=INTERLOCK_FAULT |
| default_severity | enum | Y | CRITICAL, HIGH, MEDIUM, LOW | FK→alarm_severity_policy | |
| is_safety_related | boolean | Y | | | SAFETY_STATE ⇒ true |
| lifecycle_status | enum | Y | ACTIVE, DEPRECATED | | |
| effective_from / effective_to | date | Y / N | | | V-EFF |

## 8. operator_shift
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| shift_id | text | Y | `^SHF-P[0-9]{2}-[0-9]{8}-[ABC]-[0-9]{2}$` | PK | embedded plant/date/code match columns |
| plant_id | text | Y | | FK→plant | |
| shift_date | date | Y | | | |
| shift_code | enum | Y | A (06–14), B (14–22), C (22–06) plant-local | | |
| shift_start / shift_end | ts | Y | | | end > start; 8 h |
| employee_id | text | Y | `^EMP-[0-9]{5}$` synthetic | | UQ(plant_id, shift_date, shift_code, employee_id) |
| operator_name | text | Y | Faker (fictional) | | |
| operator_role | enum | Y | OPERATOR, SHIFT_SUPERVISOR | | |
| lifecycle_status | enum | Y | SCHEDULED, COMPLETED, CANCELLED | | |

## 9. shift_machine_assignment (bridge, N:M)
| Field | Type | Req | Key | Validation |
|---|---|---|---|---|
| shift_id | text | Y | PK, FK→operator_shift | machine in same plant as shift |
| machine_id | text | Y | PK, FK→machine | |

## 10. machine_state_event (append-only)
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| machine_state_event_id | text | Y | `^MSE-<machine_id>-[0-9]{6}$` | PK | |
| machine_id | text | Y | | FK→machine | |
| sequence_no | integer | Y | ≥1, gapless | UQ(machine_id, sequence_no) | event_ts non-decreasing in sequence |
| event_ts | ts | Y | | | |
| previous_state | enum | N | machine_state values | | NULL only for sequence_no=1; equals previous row's state; transition in allowed graph |
| machine_state | enum | Y | RUNNING, IDLE, STOPPED, E_STOPPED, FAULT, MAINTENANCE | | |
| door_state | enum | Y | OPEN, CLOSED, NOT_APPLICABLE | | |
| interlock_status_summary | enum | Y | ALL_ENGAGED, PARTIAL, NONE_ENGAGED | | Derived from interlock_state_event (required interlocks) |
| safety_assessment | enum | Y | SAFE, UNSAFE, INVALID | | Derived (rules R-U*, R-I*) |
| unsafe_rule_code | text | N | R-U1, R-U2, R-U3, R-I1…R-I4 | | NOT NULL iff assessment ≠ SAFE |
| shift_id | text | N | | FK→operator_shift | shift covers event_ts and machine |
| transition_reason | text | N | | | |

## 11. interlock_state_event (append-only)
| Field | Type | Req | Allowed | Key | Validation |
|---|---|---|---|---|---|
| interlock_state_event_id | text | Y | `^ISE-<interlock_id>-[0-9]{6}$` | PK | |
| interlock_id | text | Y | | FK→interlock | |
| sequence_no | integer | Y | ≥1, gapless | UQ(interlock_id, sequence_no) | |
| event_ts | ts | Y | | | non-decreasing |
| interlock_status | enum | Y | ENGAGED, DISENGAGED, BYPASSED, FAULT | | BYPASSED only if bypass_allowed |
| door_state_observed | enum | N | OPEN, CLOSED | | door-type interlocks only; ENGAGED ⇒ CLOSED (R-I1) |
| machine_state_event_id | text | N | | FK→machine_state_event | same machine |

## 12. alarm_event (write-once)
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| alarm_event_id | text | Y | `^ALM-<machine_id>-[0-9]{6}$` | PK | |
| machine_id | text | Y | | FK→machine | |
| alarm_definition_id | text | Y | | FK→alarm_definition | definition active at raised_at |
| interlock_id | text | N | | FK→interlock | belongs to machine |
| safety_zone_id | text | N | | FK→safety_zone | linked to machine |
| machine_state_event_id | text | N | | FK→machine_state_event | same machine; event_ts ≤ raised_at; latest such event |
| shift_id | text | N | | FK→operator_shift | shift covers raised_at, machine assigned |
| severity | enum | Y | CRITICAL, HIGH, MEDIUM, LOW | FK→alarm_severity_policy | Derived by classification rule AL-1 |
| raised_at | ts | Y | | | within dataset window |
| cleared_at | ts | N | | | ≥ raised_at |
| ack_deadline_at | ts | Y | | | Derived = raised_at + deadline(severity) |
| lifecycle_status | enum | Y | ACTIVE, CLEARED | | CLEARED iff cleared_at not null |
| ingest_batch_id | text | Y | e.g. `BATCH-20261003-01` | | |

## 13. acknowledgement
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| acknowledgement_id | text | Y | `^ACK-<alarm_event_id>-[0-9]{2}$` | PK | |
| alarm_event_id | text | Y | | FK→alarm_event, UQ (0..1) | |
| acknowledged_by_shift_id | text | Y | | FK→operator_shift | |
| acknowledged_at | ts | Y | | | ≥ alarm.raised_at (else anomaly 1) |
| ack_method | enum | Y | HMI, SCADA, MOBILE | | |
| ack_delay_seconds | numeric(10,1) | Y | seconds | | Derived = acknowledged_at − raised_at |
| ack_status | enum | Y | ON_TIME, LATE | | Derived: ON_TIME iff acknowledged_at ≤ ack_deadline_at |
| comment | text | N | | | |

## 14. incident
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| incident_id | text | Y | `^INC-[0-9]{4}-[0-9]{6}$` | PK | year = year(opened_at) |
| machine_id | text | Y | | FK→machine | |
| safety_zone_id | text | N | | FK→safety_zone | |
| trigger_machine_state_event_id | text | N | | FK→machine_state_event | same machine; NULL = anomaly 3 |
| trigger_rule_code | text | N | R-U1, R-U2, R-U3 | | |
| shift_id | text | N | | FK→operator_shift | |
| severity | enum | Y | CRITICAL, HIGH, MEDIUM | | Derived by rule IN-2 |
| status | enum | Y | OPEN, INVESTIGATING, ACTION_IN_PROGRESS, RESOLVED, CLOSED | | = latest incident_status_event |
| title | text | Y | | | |
| description | text | N | | | |
| opened_at | ts | Y | | | |
| investigation_started_at | ts | N | | | ≥ opened_at |
| resolved_at | ts | N | | | ≥ investigation_started_at |
| closed_at | ts | N | | | ≥ resolved_at |

Derived (not stored, computed by analytics): `resolution_seconds = resolved_at − opened_at`.

## 15. incident_alarm_link (bridge, N:M)
| Field | Type | Req | Allowed | Key | Validation |
|---|---|---|---|---|---|
| incident_id | text | Y | | PK, FK→incident | |
| alarm_event_id | text | Y | | PK, FK→alarm_event | same machine as incident; raised within incident window |
| link_role | enum | Y | TRIGGER, CONTRIBUTING | | exactly one TRIGGER per normal incident |
| linked_at | ts | Y | | | |

## 16. incident_status_event (append-only)
| Field | Type | Req | Allowed | Key | Validation |
|---|---|---|---|---|---|
| incident_status_event_id | text | Y | `^ISTAT-<incident_id>-[0-9]{2}$` | PK | |
| incident_id | text | Y | | FK→incident, UQ(incident_id, sequence_no) | |
| sequence_no | integer | Y | ≥1 gapless | | |
| from_status | enum | N | incident statuses | | NULL iff sequence_no=1; transition allowed |
| to_status | enum | Y | | | |
| changed_at | ts | Y | | | non-decreasing; seq 1 = incident.opened_at |
| changed_by_role | enum | Y | OPERATOR, ENGINEER, SUPERVISOR, SYSTEM | | |
| note | text | N | | | |

## 17. corrective_action
| Field | Type | Req | Allowed values / units | Key | Validation |
|---|---|---|---|---|---|
| corrective_action_id | text | Y | `^ACT-<incident_id>-[0-9]{2}$` | PK | |
| incident_id | text | Y | | FK→incident | |
| assigned_engineer_id | text | Y | `^EMP-[0-9]{5}$` | | |
| assigned_engineer_name | text | Y | Faker (fictional) | | |
| action_type | enum | Y | REPLACE_INTERLOCK, REPAIR_DOOR_SWITCH, ADJUST_ALARM_LIMIT, OPERATOR_TRAINING, REVISE_PROCEDURE, INSTALL_GUARD | | |
| action_description | text | Y | | | |
| status | enum | Y | PLANNED, IN_PROGRESS, COMPLETED, VERIFIED, CLOSED, CANCELLED | | = latest status event |
| planned_at | ts | Y | | | ≥ incident.opened_at |
| due_at | ts | Y | | | > planned_at |
| started_at | ts | N | | | ≥ planned_at |
| completed_at | ts | N | | | ≥ started_at |
| verified_at | ts | N | | | ≥ completed_at |
| verified_by_id | text | N | `^EMP-[0-9]{5}$` | | ≠ assigned_engineer_id |
| verification_result | enum | N | PASSED, FAILED | | both verified_* set together |
| closed_at | ts | N | | | ≥ verified_at |

Rule **CA-3**: `status=CLOSED` ⇒ `verification_result=PASSED` and `verified_at` not null.

## 18. corrective_action_status_event (append-only)
| Field | Type | Req | Allowed | Key | Validation |
|---|---|---|---|---|---|
| action_status_event_id | text | Y | `^ASTAT-<corrective_action_id>-[0-9]{2}$` | PK | |
| corrective_action_id | text | Y | | FK→corrective_action, UQ(id, sequence_no) | |
| sequence_no | integer | Y | ≥1 gapless | | |
| from_status / to_status | enum | N / Y | action statuses | | allowed transition |
| changed_at | ts | Y | | | non-decreasing |
| note | text | N | | | |

## 19. gt.ground_truth_label (separate schema; never read by detectors)
| Field | Type | Req | Allowed | Key | Validation |
|---|---|---|---|---|---|
| label_id | text | Y | `GT-<nnnnnn>` | PK | |
| label_class | enum | Y | INJECTED_ANOMALY, BASELINE_SCENARIO | | |
| issue_type | enum | Y | ACK_BEFORE_ALARM, IMPOSSIBLE_INTERLOCK_STATE, INCIDENT_NO_CONTEXT, REPLAY_DUPLICATE_FLOOD, ACTION_CLOSED_UNVERIFIED, ALARM_FLOOD, NUISANCE_CLUSTER, REPEATED_UNSAFE_CONDITION | | class matches issue_type |
| record_table | text | Y | table name | | exists |
| record_id | text | Y | | | soft reference, **no FK** |
| group_id | text | N | groups records of one scenario | | |
| expected_decision | text | Y | e.g. `FLAG`, `OPEN_INCIDENT` | | |
| description | text | Y | | | |
