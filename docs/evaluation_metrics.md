# Evaluation metrics report

This report keeps two different kinds of numbers apart.

* **Part A - business KPIs** describe the plant: alarms, acknowledgement delays, incidents, MTTR, corrective actions.
  They never use ground truth. Replay duplicates and acknowledgements dated before their alarm are excluded from the
  maths and counted separately, so data-quality defects do not distort plant performance.
* **Part B - detection evaluation** describes the detectors: precision, recall, F1, TP / FP / FN and confusion matrices
  against the *separate* ground truth. Detectors never see the labels; only the evaluation step combines them.

Precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2PR/(P+R); undefined values are shown as n/a. The five anomaly
detectors are exact rule checks on deliberately violated rules, so perfect scores there are expected by construction;
the pattern detectors (flood, nuisance, repeated unsafe) are the informative ones.


Source: generated full dataset (seed 20261022), baseline + injected anomalies.

## Part A - Business KPIs

### Plant performance

Cut-off (snapshot) time: `2026-10-31T00:00:00+00:00`. Replay duplicates and invalid acknowledgements are excluded from the maths and counted separately.

**Overview**

| KPI | Value |
|---|---|
| Plants / machines | 4 / 60 |
| Alarm events stored | 50,219 |
| Alarm events analysed (replay duplicates excluded) | 50,025 |
| Replay duplicates excluded | 194 |
| Critical / high alarms | 143 / 7071 |
| Incidents (critical) | 146 (140) |
| Open incidents (not closed) / unresolved | 43 / 37 |
| Delayed acknowledgements (late + missed) | 11826 (23.65 %) |
| Alarm floods | 25 |
| Nuisance alarm clusters | 33 |
| Repeated unsafe conditions | 8 |

**Alarms by plant**

| Plant | Alarms | Critical | High | Medium | Low |
|---|---|---|---|---|---|
| P01 Pune Works | 12,174 | 47 | 1588 | 3542 | 6997 |
| P02 Stuttgart Plant | 15,008 | 41 | 2224 | 3760 | 8983 |
| P03 Monterrey Plant | 11,439 | 26 | 1532 | 3073 | 6808 |
| P04 Columbus Plant | 11,404 | 29 | 1727 | 3040 | 6608 |

**Top 10 machines by alarms**

| Machine | Alarms | Critical | High | Share |
|---|---|---|---|---|
| P04-M09 | 2,774 | 3 | 214 | 5.55 % |
| P02-M14 | 2,281 | 7 | 436 | 4.56 % |
| P01-M01 | 1,877 | 0 | 258 | 3.75 % |
| P02-M04 | 1,697 | 3 | 409 | 3.39 % |
| P02-M13 | 1,688 | 2 | 200 | 3.37 % |
| P02-M01 | 1,542 | 0 | 224 | 3.08 % |
| P02-M02 | 1,497 | 1 | 179 | 2.99 % |
| P01-M08 | 1,389 | 1 | 73 | 2.78 % |
| P03-M08 | 1,312 | 1 | 173 | 2.62 % |
| P03-M07 | 1,261 | 0 | 362 | 2.52 % |

**Acknowledgement**

| KPI | Value |
|---|---|
| Acknowledged / on time / late / missed / pending | 43513 / 38177 / 5336 / 6490 / 22 |
| Average / median / p90 acknowledgement time (s) | 2,460.76 / 1,221.00 / 5,668.00 |
| Delayed acknowledgement % | 23.65 % |
| Invalid (pre-alarm) acknowledgements excluded | 25 |

*delayed % = (late + missed) / (on-time + late + missed); pending alarms (deadline not yet reached at the cut-off) are excluded*

| Severity | Alarms | Acknowledged | Avg ack (s) | Late | Missed | Delayed % |
|---|---|---|---|---|---|---|
| CRITICAL | 143 | 143 | 66.03 | 20 | 0 | 13.99 % |
| HIGH | 7071 | 6875 | 316.96 | 772 | 196 | 13.69 % |
| MEDIUM | 13415 | 12231 | 1,094.69 | 1725 | 1183 | 21.68 % |
| LOW | 29396 | 24264 | 3,770.91 | 2819 | 5111 | 27.00 % |

**Alarm floods, nuisance and repeated unsafe conditions**

| KPI | Value |
|---|---|
| Flood episodes (alarms in floods, per day) | 25 (1137, 0.833/day) |
| Nuisance clusters (alarms in clusters) | 33 (932, 1.86 %) |
| Unsafe episodes by rule | R-U1=86, R-U2=43, R-U3=6 |
| Repeated unsafe conditions (episodes involved) | 8 (45) |

**Incidents**

| KPI | Value |
|---|---|
| Total / critical / open / unresolved | 146 / 140 / 43 / 37 |
| By status | ACTION_IN_PROGRESS=23, CLOSED=103, INVESTIGATING=12, OPEN=2, RESOLVED=6 |
| MTTR (hours, mean over resolved) / resolved count | 123.73 / 109 |
| Mean time to start investigation (h) | 1.40 |

Incidents by machine (top 10):

| Machine | Incidents | Critical | Open |
|---|---|---|---|
| P01-M02 | 16 | 15 | 6 |
| P01-M07 | 15 | 15 | 4 |
| P03-M03 | 15 | 14 | 5 |
| P02-M06 | 9 | 9 | 0 |
| P02-M14 | 6 | 6 | 4 |
| P02-M15 | 5 | 5 | 0 |
| P04-M01 | 5 | 4 | 2 |
| P02-M08 | 4 | 4 | 2 |
| P02-M10 | 4 | 4 | 1 |
| P04-M10 | 4 | 4 | 2 |

**Corrective actions**

| KPI | Value |
|---|---|
| Total (live) | 219 (193) |
| By status | CANCELLED=26, CLOSED=163, COMPLETED=6, IN_PROGRESS=20, PLANNED=2, VERIFIED=2 |
| Completion rate | 88.60 % |
| Verification rate | 89.47 % |
| Failed verifications / closed without verification | 27 / 12 |
| Overdue | 7 |


## Part B - Detection evaluation

### Injected anomalies (five rule detectors)

Scope: injected anomalies (5 detectors).

| Issue type | TP | FP | FN | Precision | Recall | F1 | Support |
|---|---|---|---|---|---|---|---|
| ACK_BEFORE_ALARM | 25 | 0 | 0 | 1.00 | 1.00 | 1.00 | 25 |
| IMPOSSIBLE_INTERLOCK_STATE | 20 | 0 | 0 | 1.00 | 1.00 | 1.00 | 20 |
| INCIDENT_NO_CONTEXT | 12 | 0 | 0 | 1.00 | 1.00 | 1.00 | 12 |
| REPLAY_DUPLICATE_FLOOD | 194 | 0 | 0 | 1.00 | 1.00 | 1.00 | 194 |
| ACTION_CLOSED_UNVERIFIED | 12 | 0 | 0 | 1.00 | 1.00 | 1.00 | 12 |
| **micro (all)** | 263 | 0 | 0 | 1.00 | 1.00 | 1.00 |  |
| **macro** |  |  |  | 1.00 | 1.00 | 1.00 |  |

Confusion matrix (rows = actual from ground truth, columns = predicted by the detectors):

| actual \ predicted | ACK_BEFORE_ALARM | IMPOSSIBLE_INTERLOCK_STATE | INCIDENT_NO_CONTEXT | REPLAY_DUPLICATE_FLOOD | ACTION_CLOSED_UNVERIFIED | NONE |
|---|---|---|---|---|---|---|
| ACK_BEFORE_ALARM | 25 | 0 | 0 | 0 | 0 | 0 |
| IMPOSSIBLE_INTERLOCK_STATE | 0 | 20 | 0 | 0 | 0 | 0 |
| INCIDENT_NO_CONTEXT | 0 | 0 | 12 | 0 | 0 | 0 |
| REPLAY_DUPLICATE_FLOOD | 0 | 0 | 0 | 194 | 0 | 0 |
| ACTION_CLOSED_UNVERIFIED | 0 | 0 | 0 | 0 | 12 | 0 |
| NONE | 0 | 0 | 0 | 0 | 0 | 167,339 |


### Baseline scenarios (flood, nuisance, repeated unsafe condition)

Scope: baseline scenarios (pattern detectors).

| Issue type | TP | FP | FN | Precision | Recall | F1 | Support |
|---|---|---|---|---|---|---|---|
| ALARM_FLOOD | 1095 | 42 | 0 | 0.96 | 1.00 | 0.98 | 1095 |
| NUISANCE_CLUSTER | 863 | 69 | 8 | 0.93 | 0.99 | 0.96 | 871 |
| REPEATED_UNSAFE_CONDITION | 45 | 0 | 0 | 1.00 | 1.00 | 1.00 | 45 |
| **micro (all)** | 2003 | 111 | 8 | 0.95 | 1.00 | 0.97 |  |
| **macro** |  |  |  | 0.96 | 1.00 | 0.98 |  |

Confusion matrix (rows = actual from ground truth, columns = predicted by the detectors):

| actual \ predicted | ALARM_FLOOD | NUISANCE_CLUSTER | REPEATED_UNSAFE_CONDITION | NONE |
|---|---|---|---|---|
| ALARM_FLOOD | 1,095 | 0 | 0 | 0 |
| NUISANCE_CLUSTER | 0 | 863 | 0 | 8 |
| REPEATED_UNSAFE_CONDITION | 0 | 0 | 45 | 0 |
| NONE | 42 | 6 | 0 | 121,640 |

- **ALARM_FLOOD** false positives: ALM-P01-M01-001671, ALM-P01-M04-000148, ALM-P01-M04-000177, ALM-P01-M05-000230, ALM-P01-M06-000088, ALM-P01-M06-000092, ALM-P01-M10-000495, ALM-P01-M15-000494 ...; false negatives: none
- **NUISANCE_CLUSTER** false positives: ALM-P02-M06-000353, ALM-P02-M06-000364, ALM-P02-M09-000655, ALM-P03-M01-000159, ALM-P03-M01-000161, ALM-P03-M01-000163, ALM-P03-M01-000170, ALM-P03-M01-000171 ...; false negatives: ALM-P02-M02-001379, ALM-P02-M02-001380, ALM-P02-M02-001381, ALM-P02-M02-001382, ALM-P02-M02-001384, ALM-P02-M02-001385, ALM-P02-M02-001386, ALM-P02-M02-001387
