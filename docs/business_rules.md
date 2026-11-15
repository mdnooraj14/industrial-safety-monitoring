# Business Rules

All thresholds below are **defaults** in `backend/app/business_rules/config.py` (overridable via `.env`). The generator, API and validator all call the same functions, so no outcome is assigned independently of these rules.

## 1. Machine safety assessment (R-U / R-S / R-I)

Inputs: machine state, door state, status of each **required** interlock.
`ENGAGED` means the safety function is armed (door closed and locked, e-stop circuit healthy).

| Rule | Condition | Assessment |
|---|---|---|
| R-U1 DOOR_OPEN_WHILE_RUNNING | state=RUNNING and door_state=OPEN | UNSAFE |
| R-U2 REQUIRED_INTERLOCK_INACTIVE | state=RUNNING and any required interlock DISENGAGED or FAULT | UNSAFE |
| R-U3 REQUIRED_INTERLOCK_BYPASSED | state=RUNNING and any required interlock BYPASSED | UNSAFE |
| R-S1 NOT_RUNNING | state in STOPPED, IDLE, E_STOPPED, FAULT, MAINTENANCE (door may be open for access) and no R-I* match | SAFE (normal) |
| R-S2 RUNNING_ENGAGED | state=RUNNING, door CLOSED/N-A, all required ENGAGED | SAFE |
| R-I1 DOOR_ENGAGED_BUT_OPEN | a door/guard-lock interlock is ENGAGED while door_state_observed=OPEN | INVALID (impossible) |
| R-I2 SUMMARY_MISMATCH | stored `interlock_status_summary` ≠ summary derived from interlock events | INVALID |
| R-I3 ILLEGAL_TRANSITION | `previous_state → machine_state` not in the allowed graph (e.g. E_STOPPED→RUNNING). A transition to the *same* state is allowed: it records a door/interlock context change | INVALID |
| R-I4 BYPASS_NOT_ALLOWED | interlock BYPASSED while `bypass_allowed=false` | INVALID |

Precedence: INVALID (R-I*) > UNSAFE (R-U*) > SAFE. A state event stores the first matching rule code.
An **unsafe episode** is a maximal span of consecutive state events with assessment UNSAFE on one machine, from the first such event to the next event that is not UNSAFE.

## 2. Alarm rules (AL)

**AL-1 Severity classification** (`effective severity`):
1. Start with `alarm_definition.default_severity`.
2. If the alarm is raised while the machine's current state is UNSAFE, escalate one level (cap CRITICAL).
3. If the definition is safety-related and the zone `risk_level=HIGH`, severity is at least HIGH.

**AL-2 Acknowledgement deadlines** (`alarm_severity_policy`, configurable):

| Severity | Priority rank | Ack deadline | Escalation after |
|---|---|---|---|
| CRITICAL | 1 | 120 s | 300 s |
| HIGH | 2 | 600 s | 1 800 s |
| MEDIUM | 3 | 1 800 s | 5 400 s |
| LOW | 4 | 7 200 s | 14 400 s |

**AL-3 Prioritisation**: alarm lists sort by `priority_rank`, then `raised_at`; CRITICAL always first.

**AL-4 Ack delay** = `acknowledged_at − raised_at` (seconds, from timestamps). `ON_TIME` iff `acknowledged_at ≤ ack_deadline_at`, else `LATE`. An alarm with no acknowledgement whose deadline has passed before the data cut-off is **MISSED**. Negative delay is invalid (anomaly 1).

**AL-5 Alarm flood**: ≥ 10 alarms on one machine within any rolling 10 minutes (ISA-18.2-style). A flood episode runs from the first alarm of the first qualifying window until the rolling count drops below 5. Reported as (machine, start, end, alarm count).

**AL-6 Nuisance alarms**:
- **N1 chattering**: same (machine, alarm definition) raised ≥ 5 times within 30 minutes with every instance cleared within 120 s.
- **N2 repeating low-value**: same (machine, definition) raised ≥ 20 times in one day at severity ≤ MEDIUM.
A **nuisance cluster** = one (machine, definition) pair meeting N1 or N2, with its time span and count.

**AL-7 Repeated alarm pattern**: same (machine, definition) ≥ 3 times in 24 h.

**AL-8 Replay duplicate (anomaly detector D1)**: two or more alarm events with identical (machine, definition, `raised_at` truncated to the second) and **different** `ingest_batch_id`; the later-batch copies are flagged.

## 3. Incident rules (IN)

**IN-1 Creation**: for each unsafe episode (R-U1/R-U2/R-U3) lasting ≥ 10 s that has at least one HIGH or CRITICAL alarm on the same machine raised within `[episode_start − 60 s, episode_end]`, create exactly one incident.
- Opened at the first qualifying alarm's `raised_at`.
- `trigger_machine_state_event_id` = first UNSAFE state event of the episode; `trigger_rule_code` = its rule.
- Link the earliest qualifying alarm as `TRIGGER`; all other alarms in the window as `CONTRIBUTING`.
- `shift_id` = shift on duty at `opened_at`.

**IN-2 Severity**: CRITICAL if any linked alarm is CRITICAL, otherwise HIGH. (MEDIUM exists in the schema but the API never produces it: every API-created incident goes through IN-1 / IN-2.)

**IN-3 Lifecycle** (history in `incident_status_event`): OPEN → INVESTIGATING → ACTION_IN_PROGRESS → RESOLVED → CLOSED.
Timings are seeded draws from the ranges below for each milestone (investigation start, action planning/start/work/verification/closure). The incident's resolved/closed dates therefore *emerge* from its corrective actions, and the *state at the data cut-off* follows from which timestamps are already in the past, not from random labels. Resolved = all action chains verified PASSED; closed = after the last action is CLOSED (IN-4).

| Severity | Investigation starts after opening | Action planned after | Action start after planning | Work duration | Verification after completion | Close after verification | Incident closed after last action | Action SLA |
|---|---|---|---|---|---|---|---|---|
| CRITICAL | 0.25–2 h | 1–4 h | 1–12 h | 1–4 d | 0.5–2 d | 2–12 h | 0.5–2 d | 3 d |
| HIGH | 1–8 h | 2–8 h | 2–24 h | 2–8 d | 1–3 d | 4–24 h | 1–4 d | 7 d |

1 action per incident (CRITICAL 40 % / HIGH 20 % get 2 parallel actions).

If the cut-off falls before a milestone, the incident stays in the preceding state (hence open/unresolved incidents exist naturally).

**IN-4 Closure guard**: an incident may be CLOSED only if it has ≥ 1 corrective action and all its non-cancelled actions are CLOSED (verified).

**IN-5 Repeated unsafe condition**: ≥ 3 unsafe episodes of the same (machine, rule code) within a rolling 7 days.

## 4. Corrective action rules (CA)

- **CA-1** Every action references an existing incident; one or more actions per incident; ID suffix increments.
- **CA-2** Lifecycle: PLANNED → IN_PROGRESS → COMPLETED → VERIFIED → CLOSED (or CANCELLED before completion). Each transition appends a `corrective_action_status_event`.
- **CA-3** CLOSED requires `verification_result=PASSED`, `verified_at` set, `verified_by ≠ assigned engineer`, and `completed_at ≤ verified_at ≤ closed_at`.
- **CA-4** FAILED verification leaves the action in VERIFIED with its verification record preserved. A follow-up action (next `-NN` suffix) is planned 4–24 h later and the failed action becomes CANCELLED (superseded; the status event says so). Failure probability by action type is a documented parameter (OPERATOR_TRAINING 25 %, ADJUST_ALARM_LIMIT / REVISE_PROCEDURE 15 %, others 5–8 %); at most 2 follow-ups per chain. Because CANCELLED actions are excluded, IN-4 and CA-5 count only live actions.
- **CA-5** Completion rate = actions with status ≥ COMPLETED (not cancelled) / non-cancelled actions. Verification rate = actions with `verification_result` set / completed actions. Both computed from rows.

## 5. Deliberate anomalies (implemented in Milestone 5)

`data_generator/anomaly_injector.py` works on a **copy** of the valid baseline. It only **appends** records (injected IDs continue the existing counters, so baseline IDs are identical with or without anomalies) and never edits, deletes or reorders a baseline row. Injected rows carry no marker text or columns: derived values on them (assessment, delay, status) come from the same rules as the baseline. The baseline is exported separately (`data/exports/<scale>_baseline/`), the baseline + anomalies dataset is `data/exports/<scale>/`, and labels are in `data/ground_truth/<scale>/` and database schema `gt`.

| # | issue_type | What is appended | Detector (all in `app/validation/anomaly_detectors.py`) |
|---|---|---|---|
| 1 | ACK_BEFORE_ALARM | a new alarm plus its acknowledgement, with `acknowledged_at` 5-600 s *before* `raised_at` (delay is stored negative by rule AL-4) | AL-4: acknowledgement earlier than the alarm |
| 2 | IMPOSSIBLE_INTERLOCK_STATE | one new event at the **end** of a machine's timeline (after a SAFE state, so no baseline episode changes): either a door interlock still ENGAGED while the door is OPEN (R-I1) or BYPASSED on an interlock that forbids bypass (R-I4). Its assessment is derived by the state-processing rules | replays each machine's interlock history and re-derives R-I1..R-I4, ignoring the stored assessment |
| 3 | INCIDENT_NO_CONTEXT | a new incident (coherent lifecycle history) with no `incident_alarm_link` row and NULL `trigger_machine_state_event_id` | no TRIGGER link or no trigger state |
| 4 | REPLAY_DUPLICATE_FLOOD | a complete baseline flood re-ingested: new alarm IDs, identical machine / definition / timestamps / severity, ordinary-looking later batch id (`BATCH-<date>-02`), no acknowledgements | AL-8: same (machine, definition, second) under different batches; later-batch copies are flagged |
| 5 | ACTION_CLOSED_UNVERIFIED | a new action on a still-open incident, status CLOSED, no verification, history PLANNED, IN_PROGRESS, COMPLETED, CLOSED (illegal COMPLETED to CLOSED jump) | CA-3: CLOSED without a PASSED, independent, correctly ordered verification |

Volumes are parameters (`n_ack_before_alarm`, ...). Full defaults: 25 / 20 / 12 / 4 floods / 12; starter: 4 / 4 / 3 / 1 flood / 3. Scenario 2 is limited to one event per machine and scenario 5 to open incidents that have time left before the cut-off, so a very small dataset may inject fewer than requested.

**Alarm total:** the 50,000 target is the **baseline** alarm count. Replayed flood copies and the ack-before-alarm alarms are extra, labelled rows on top (about 220 at full scale), so the analysed dataset has slightly more than 50,000 alarm events.

**Detector quality:** these five detectors are exact rule checks on the defined violations, so on the injected data they reach precision = recall = 1.0 and report nothing on the clean baseline. That is expected by construction; it shows the rules and the labels agree, not that the detectors generalise. The pattern detectors (flood, nuisance, section 8) are the ones with imperfect scores. Detectors never import or query ground truth, and recompute facts from raw events rather than trusting stored derived columns (both enforced by tests).

## 6. Detection evaluation vs business KPIs

- **Business KPIs** (analytics): alarms by plant/machine, critical/high counts, average ack time, delayed-ack %, flood frequency, nuisance clusters, repeated unsafe frequency, incidents by machine, MTTR, action completion/verification rate, open incidents.
- **Detection metrics** (evaluation): per issue_type and overall, matched on `(record_table, record_id)`: TP, FP, FN, precision = TP/(TP+FP), recall = TP/(TP+FN), F1, plus a confusion matrix over issue types (+ `NONE`). Detectors never read schema `gt`.

## 7. Machine-state event processing (Milestone 4)

`business_rules/state_processing.py` (pure) + `services/state_event_service.py` (database) record every incoming reading.

| Outcome | Condition | Result |
|---|---|---|
| Accepted, SAFE / UNSAFE | normal reading; assessment from R-U* / R-S* | appended as `machine_state_event` (+ `interlock_state_event` for each interlock whose status changed) |
| Accepted and **flagged** INVALID | R-I1 door interlock engaged while door open · R-I3 illegal transition · R-I4 bypass not allowed | stored with `safety_assessment=INVALID` and the rule code, never dropped, so impossible states stay visible |
| **Rejected** (HTTP 422, nothing written) | `BAD_MACHINE_STATE`, `BAD_DOOR_STATE`, `BAD_INTERLOCK_STATUS`, `UNKNOWN_INTERLOCK`, `DOOR_STATE_MISMATCH`, `TIMESTAMP_ORDER` (event not later than the previous one), `NO_CHANGE` | `RuleViolationError` with the code |
| Rejected (HTTP 404) | unknown machine | `NotFoundError` |

Partial interlock updates are merged onto the current interlock statuses; the first event defaults every interlock to ENGAGED. IDs and sequence numbers are assigned from history; existing rows are never modified (database triggers also forbid it).

## 8. Alarm-pattern rules as implemented (`business_rules/alarm_patterns.py`)

- **Flood (AL-5)**: trailing 10-minute count per machine; an episode starts at the first alarm of the first window with ≥ 10 alarms and ends once the trailing count falls below 5.
- **Nuisance (AL-6)**: N1 needs a contiguous run of quick-cleared raises (≤ 120 s) with ≥ 5 raises inside 30 minutes; any slow raise breaks the run. N2 needs ≥ 20 raises of severity LOW/MEDIUM in one UTC day. HIGH/CRITICAL alarms are never nuisance.
- **Repeated alarm pattern (AL-7)**: ≥ 3 raises of the same (machine, definition) inside 24 h.
- To keep ground truth unambiguous, the generator caps background alarms at 12 per (machine, definition, day), so only deliberately planted clusters can satisfy N2.

## 9. Configuration

Every threshold is a field of `RulesConfig` and can be overridden with environment variables / `.env`:
`RULE_FLOOD_THRESHOLD=12`, `RULE_REPEATED_UNSAFE_WINDOW_DAYS=14`, `RULE_ACK_DEADLINE_CRITICAL=90`, `RULE_ESCALATION_HIGH=2400`, …
Inconsistent configurations (deadlines not increasing as severity decreases, escalation before deadline, flood end threshold above start threshold) are rejected at load time.

## 10. Analytics definitions (Milestone 7)

**Business KPIs** (`app/analytics/kpis.py`) describe the plant. **Detection metrics** (`app/analytics/evaluation.py`) describe the detectors. They are separate modules, separate API/dashboard sections and separate parts of `docs/evaluation_metrics.md`; KPI code never sees ground truth.

Analysis view: replay duplicates (AL-8) are removed from the alarm set, and acknowledgements dated before their alarm are excluded, then reported separately (`replay_duplicates_excluded`, `invalid_acknowledgements_excluded`), so a data-quality defect cannot distort plant performance. The snapshot ("cut-off") time defaults to the latest event time rounded up to the next whole hour in UTC; pass an explicit cut-off when it is known. All day grouping uses UTC days.

| KPI | Definition |
|---|---|
| Alarms by plant / machine; critical and high counts | counts of analysed alarms by severity (AL-1 classification) |
| Acknowledgement outcome | ON_TIME: acknowledged at or before the deadline · LATE: after it · MISSED: no valid acknowledgement and deadline < cut-off · PENDING: no acknowledgement, deadline not reached |
| Average acknowledgement time | mean (also median, p90) of acknowledged_at - raised_at over valid acknowledgements, seconds |
| Delayed acknowledgement % | (LATE + MISSED) / (ON_TIME + LATE + MISSED); pending alarms are excluded from the denominator |
| Alarm flood frequency | AL-5 episodes: count, alarms in floods, floods per day, by machine and plant |
| Nuisance clusters | AL-6 clusters (N1 chattering, N2 repeating), alarms in them, share of all alarms |
| Repeated unsafe condition frequency | unsafe episodes by rule and machine; IN-5 repeated conditions (>= 3 of the same rule on a machine within 7 days) |
| Incidents by machine | counts with severity and open split |
| Open / unresolved incidents | open = status not CLOSED; unresolved = OPEN, INVESTIGATING or ACTION_IN_PROGRESS |
| MTTR | mean (resolved_at - opened_at) in hours over incidents that have a resolved_at; also by severity, plus mean time to start investigation |
| Corrective-action completion / verification rate | CA-5: COMPLETED/VERIFIED/CLOSED among non-cancelled actions; verification result recorded among completed actions |

**Detection evaluation**: matching key `(record_table, record_id, issue_type)`; TP flagged and labelled, FP flagged only, FN labelled only; precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2PR/(P+R); a metric with a zero denominator is `None` (n/a), never a misleading 0 or 1, and macro averages use the defined values only. The confusion matrix is per record over the issue types plus NONE (rows actual, columns predicted; NONE/NONE = records neither labelled nor flagged). Two scopes: the five injected anomalies, and the three baseline scenarios (flood, nuisance cluster, repeated unsafe condition). The repeated-unsafe label is derived from the same IN-5 function the detector uses, so its perfect score is by construction; flood and nuisance are the independent, informative scores.


## 11. API behaviour of the rules (Milestone 8)

The REST layer enforces the same rules (`docs/api.md` lists every endpoint):

| Endpoint | Rule enforced | Rejection |
|---|---|---|
| `POST /machine-state-events` | R-U / R-S derivation; impossible readings stored flagged INVALID | 422 `TIMESTAMP_ORDER`, `UNKNOWN_INTERLOCK`, `DOOR_STATE_MISMATCH`, `NO_CHANGE` |
| `POST /alarm-events` | AL-1 severity classification, AL-2 deadline from the severity-policy table; SAFETY_STATE alarms only while UNSAFE | 422 `NO_UNSAFE_STATE`, `DEFINITION_INACTIVE`, `DEFINITION_NOT_APPLICABLE`, `NO_MATCHING_INTERLOCK`; 409 `DUPLICATE_ALARM` |
| `POST /acknowledgements` | AL-4 delay and status from timestamps; at most one per alarm | 422 `ACK_BEFORE_ALARM`, `NO_SHIFT`; 409 `ALREADY_ACKNOWLEDGED` |
| `POST /incidents` | IN-1 / IN-2 from an UNSAFE episode of >= 10 s with a HIGH/CRITICAL alarm; one incident per episode | 422 `NOT_UNSAFE`, `EPISODE_TOO_SHORT`, `NO_QUALIFYING_ALARM`; 409 `INCIDENT_EXISTS` |
| `POST /incidents/{id}/transitions` | IN-3 order; ACTION_IN_PROGRESS needs an action; RESOLVED needs every live action verified PASSED; CLOSED needs every live action CLOSED (IN-4) | 409 `INVALID_TRANSITION`; 422 `NO_ACTION`, `ACTIONS_NOT_VERIFIED`, `ACTIONS_NOT_CLOSED`, `TIMESTAMP_ORDER` |
| `POST /incidents/{id}/corrective-actions` | CA-1 valid incident, next `-NN` suffix, not on a CLOSED incident | 409 `INCIDENT_CLOSED`; 422 `TIMESTAMP_ORDER` |
| `POST /corrective-actions/{id}/transitions` | CA-2 order; VERIFIED needs a result and an independent verifier; CLOSED needs PASSED (CA-3); CANCELLED after VERIFIED only when the verification FAILED (CA-4) | 409 `INVALID_TRANSITION`; 422 `VERIFICATION_REQUIRED`, `SELF_VERIFICATION`, `NOT_VERIFIED`, `NOT_SUPERSEDABLE` |

Every state change appends a history row (state, status and acknowledgement rows are never updated or deleted; those resources have no PUT / PATCH / DELETE). The `incident` and `corrective_action` rows hold the current state and are updated together with the new history row in one transaction.
