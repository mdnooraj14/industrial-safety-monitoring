# Fixed-seed reproducibility report

Scale: **full**. Seed: **20261022**. Result: **PASSED - identical**.

The complete pipeline (generate baseline, inject anomalies, export CSV / JSON / SQL, write ground truth, validate, evaluate detectors) was run twice in **separate Python processes** with different `PYTHONHASHSEED` values, so any dependence on dictionary / set ordering or on process state would show up as a difference. Command: `python scripts/reproducibility_check.py --scale full`.

| Run | PYTHONHASHSEED | Python | Wall time |
|---|---|---|---|
| 1 | 1 | 3.11.6 | 45.2 s |
| 2 | 2 | 3.11.6 | 48.3 s |

## What was compared

| Comparison | Items | Result |
|---|---|---|
| Row counts (18 tables x baseline and baseline+anomalies, ground-truth rows) | 37 | identical |
| Deterministic IDs (digest of every primary key of every table) | 18 tables, 210,299 IDs | identical |
| SHA-256 of every exported file (CSV, JSON, SQL, checksums, counts, ground truth) | 78 files | identical |
| Validation outcomes (counts, failed validations, affected record ids) | baseline + injected | identical |
| Detector evaluation (TP / FP / FN, precision, recall, F1) | 2 scopes | identical |
| Reproduced files vs the files delivered in `data/exports/full` and `full_baseline` | 76 files | identical |

## Reference numbers (identical in both runs)

* Alarm events: baseline **50,000**, with injected anomalies **50,219**.
* Machine-state events 73,480; acknowledgements 43,538; incidents 146; corrective actions 219.
* Validations on the clean baseline: 17/17 passed.
* Validations with injected anomalies: 12/17 passed; failed: V07, V08, V11, V13, V14; anomalies detected 263.
* Ground-truth rows: 2,274.

## Sample checksums (final dataset)

| File | SHA-256 |
|---|---|
| `csv/alarm_event.csv` | `ba9dc98c51b47723e51cb2eaa367830645cac4765eb23cc6a925ee3f0e9503c8` |
| `csv/machine_state_event.csv` | `91b1d914446c8ffd55534bd644737050c7a8e21cf0d9aa8d5e6a48e45c903187` |
| `csv/incident.csv` | `7ac0aa36fe0c54ff02b77fd43bc6c295b8ffbf6b8dab3614a2d326047cca5b88` |
| `sql/seed.sql` | `5578b8d1154b5ff47c5bf0eea4b87ed96eb427c9d6c79fe7f8e22b982f5c3e30` |

Reproducibility holds for a fixed software environment (Python 3.11, the library versions in `requirements-lock.txt`): Faker's name generator is deterministic for a given Faker version, so upgrading Faker may change the fictional names (never the structure, IDs or counts).
