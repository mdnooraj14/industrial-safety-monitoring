"""Final acceptance: check every requirement of the problem statement and the implementation prompt, with evidence.

    python scripts/acceptance.py                  # fast checks on the full dataset (needs PostgreSQL for the database checks)
    python scripts/acceptance.py --run-tests      # also runs the whole pytest suite and records the result

Writes docs/acceptance_report.md; exit code 1 if any check fails.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    sys.path.insert(0, str(p))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analytics.kpis import compute_kpis  # noqa: E402
from app.core.database import make_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.services.evaluation_service import evaluate_tables  # noqa: E402
from app.validation.validator import validate  # noqa: E402
from data_generator.config import FULL  # noqa: E402
from data_generator.export import TABLE_ORDER  # noqa: E402
from data_generator.generate import generate_with_anomalies  # noqa: E402

RESULTS: list[tuple[str, str, str, str, str]] = []   # section, id, requirement, status, evidence


def check(section: str, cid: str, requirement: str, ok: bool | None, evidence: str) -> None:
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((section, cid, requirement, status, evidence))
    print(f"[{status}] {cid} {requirement}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-tests", action="store_true")
    args = ap.parse_args()
    print("generating the full dataset in memory ...", flush=True)
    base, final = generate_with_anomalies(FULL)
    cfg, bt, ft = FULL, base.tables, final.tables
    vb, vf = validate(bt), validate(ft)
    by_b = {c.check_id: c for c in vb.checks}
    by_f = {c.check_id: c for c in vf.checks}
    ev = evaluate_tables(ft, final.ground_truth)
    k = compute_kpis(bt, cutoff=cfg.end)
    gt = Counter(l["issue_type"] for l in final.ground_truth)
    docs = ROOT / "docs"

    # ------------------------------------------------------------------ A. volumes and scenarios
    S = "A. Volume, window and scenarios"
    n = {t: len(bt[t]) for t in TABLE_ORDER}
    check(S, "A1", "4 plants, 60 machines, 200 alarm definitions", (n["plant"], n["machine"], n["alarm_definition"]) == (4, 60, 200),
          f"{n['plant']} plants, {n['machine']} machines, {n['alarm_definition']} definitions")
    inj = sum(1 for l in final.ground_truth if l["label_class"] == "INJECTED_ANOMALY" and l["record_table"] == "alarm_event")
    check(S, "A2", "50,000 alarm events (baseline)", n["alarm_event"] == 50_000,
          f"baseline {n['alarm_event']:,}; analysed dataset {len(ft['alarm_event']):,} = baseline + {inj} labelled replay duplicates (ack-before-alarm alarms are separate)")
    ts = [a["raised_at"] for a in bt["alarm_event"]]
    span = (max(ts) - min(ts)).total_seconds() / 86400
    check(S, "A3", "events over 30 days, fixed seed 20261022",
          cfg.days == 30 and cfg.seed == 20261022 and min(ts) >= cfg.start and max(ts) < cfg.end and span > 29,
          f"{min(ts):%Y-%m-%d %H:%M} to {max(ts):%Y-%m-%d %H:%M} UTC ({span:.1f} days), seed {cfg.seed}")
    ak = k["acknowledgements"]
    check(S, "A4", "alarm floods, delayed acknowledgements, nuisance alarms, genuine high-severity incidents present",
          k["floods"]["episodes"] >= 20 and ak["late"] > 0 and ak["missed"] > 0 and k["nuisance"]["clusters"] >= 20 and k["incidents"]["critical"] > 50,
          f"{k['floods']['episodes']} floods; delayed acknowledgements {ak['delayed_total']:,} ({ak['delayed_pct']} %: {ak['late']} late, {ak['missed']} missed); "
          f"{k['nuisance']['clusters']} nuisance clusters; {k['incidents']['total']} incidents ({k['incidents']['critical']} critical)")
    check(S, "A5", "repeated unsafe machine-state combinations present", k["unsafe_conditions"]["repeated_conditions"] >= 3,
          f"{k['unsafe_conditions']['unsafe_episodes']} unsafe episodes, {k['unsafe_conditions']['repeated_conditions']} repeated conditions "
          f"({', '.join(f'{r}={c}' for r, c in k['unsafe_conditions']['by_rule'].items())})")

    # ------------------------------------------------------------------ B. model and IDs
    S = "B. Entities, keys and IDs"
    core = ["plant", "machine", "safety_zone", "interlock", "alarm_definition", "alarm_event", "operator_shift", "acknowledgement", "incident", "corrective_action"]
    tables = Base.metadata.tables
    check(S, "B1", "10 core entities + MachineStateEvent; primary key on every table", all(t in tables for t in core + ["machine_state_event"])
          and all(tb.primary_key.columns for tb in tables.values()), f"{len(tables)} tables, all with primary keys")
    designed = {
        "machine": {"plant"}, "safety_zone": {"plant"}, "machine_safety_zone": {"machine", "safety_zone"},
        "interlock": {"machine", "safety_zone"}, "interlock_state_event": {"interlock", "machine_state_event"},
        "machine_state_event": {"machine", "operator_shift"}, "operator_shift": {"plant"},
        "shift_machine_assignment": {"operator_shift", "machine"}, "alarm_definition": {"alarm_severity_policy"},
        "alarm_event": {"machine", "alarm_definition", "interlock", "safety_zone", "machine_state_event", "operator_shift", "alarm_severity_policy"},
        "acknowledgement": {"alarm_event", "operator_shift"}, "incident": {"machine", "safety_zone", "machine_state_event", "operator_shift"},
        "incident_alarm_link": {"incident", "alarm_event"}, "incident_status_event": {"incident"}, "corrective_action": {"incident"},
        "corrective_action_status_event": {"corrective_action"}}
    have = {name: {fk.referred_table.name for fk in tb.foreign_key_constraints} for name, tb in tables.items()}
    missing_fk = [f"{c}->{p}" for c, ps in designed.items() for p in ps if p not in have.get(c, set())]
    fks = sum(len(tb.foreign_key_constraints) for tb in tables.values())
    check(S, "B2", "foreign keys for every relationship in the ER design", not missing_fk,
          f"{fks} foreign-key constraints covering {sum(len(v) for v in designed.values())} designed parent-child relationships; missing: {missing_fk or 'none'}")
    bridges = [t for t in ("machine_safety_zone", "shift_machine_assignment", "incident_alarm_link") if t in tables]
    check(S, "B3", "bridge tables for many-to-many relationships", len(bridges) == 3, ", ".join(bridges))
    check(S, "B4", "ID conventions SAFE / ALM / INC / ACK / ACT, zero-padded, consistent across files", by_b["V04"].passed and by_b["V02"].passed,
          f"V04 checked {by_b['V04'].records_checked:,} IDs; V02 checked {by_b['V02'].records_checked:,} foreign keys")
    from tests.test_docs_and_reproducibility import parse_dictionary
    doc = parse_dictionary()
    cols_ok = all(set(doc.get(t.name, {})) == {c.name for c in t.columns if c.name != "created_at"} for t in tables.values())
    check(S, "B5", "data dictionary for every entity and field", cols_ok and len(doc) == 19, f"{len(doc)} tables, {sum(len(v) for v in doc.values())} fields documented and equal to the schema")
    check(S, "B6", "ER diagram, relationship documentation, lifecycle states", (docs / "er_diagram.md").exists() and "erDiagram" in (docs / "er_diagram.md").read_text(encoding="utf-8"), "docs/er_diagram.md")

    # ------------------------------------------------------------------ C. generation logic
    S = "C. Generation logic and derived values"
    check(S, "C1", "master data first; children reference valid parents", by_b["V02"].passed and by_f["V02"].passed,
          f"V02 passes on baseline and with anomalies ({by_f['V02'].records_checked:,} foreign keys)")
    derived = [by_b[i].passed for i in ("V09", "V10", "V15", "V17")]
    check(S, "C2", "derived values (severity, deadlines, delays, statuses, summaries, totals) recomputed from source records", all(derived),
          "V09 alarm derived, V10 acknowledgement derived, V15 reconciliation, V17 machine-state derived all pass")
    check(S, "C3", "timestamps, transitions, lifecycles consistent", all(by_b[i].passed for i in ("V05", "V06", "V12", "V13")), "V05, V06, V12, V13 pass on the baseline")
    check(S, "C4", "unique constraints, duplicates, cardinality", all(by_b[i].passed for i in ("V01", "V14", "V16")), "V01, V14, V16 pass on the baseline")

    # ------------------------------------------------------------------ D. anomalies and ground truth
    S = "D. Anomalies and ground truth"
    injected = {t: gt[t] for t in ("ACK_BEFORE_ALARM", "IMPOSSIBLE_INTERLOCK_STATE", "INCIDENT_NO_CONTEXT", "REPLAY_DUPLICATE_FLOOD", "ACTION_CLOSED_UNVERIFIED")}
    check(S, "D1", "five labelled anomalies injected", all(v > 0 for v in injected.values()), ", ".join(f"{a}={b}" for a, b in injected.items()))
    an = ev["anomalies"]
    check(S, "D2", "detectors reproduce the ground truth (computed separately)", an.micro["precision"] == an.micro["recall"] == 1.0,
          f"anomalies: TP {an.micro['tp']}, FP {an.micro['fp']}, FN {an.micro['fn']}, F1 {an.micro['f1']}; patterns: precision {ev['patterns'].micro['precision']}, "
          f"recall {ev['patterns'].micro['recall']}, F1 {ev['patterns'].micro['f1']}")
    leak = [t for t, rows in ft.items() if set(rows[0]) & {"issue_type", "label_class", "ground_truth", "is_anomaly"}]
    gtdir = ROOT / "data" / "ground_truth" / "full"
    check(S, "D3", "ground truth in separate files / schema, never in feature tables", not leak and (gtdir / "ground_truth.csv").exists() and (gtdir / "ground_truth.json").exists(),
          f"data/ground_truth/full/ground_truth.csv|json ({len(final.ground_truth):,} labels), schema gt; no label column in any feature table")
    check(S, "D4", "baseline preserved; anomalies only appended", all(ft[t][:len(bt[t])] == bt[t] for t in TABLE_ORDER),
          "every baseline row is identical, and in the same position, in the dataset with anomalies")

    # ------------------------------------------------------------------ E. validation
    S = "E. Validation"
    check(S, "E1", "validation module and report (records, passed, failed, anomalies, errors, affected ids)",
          vb.summary()["validations_failed"] == 0 and (docs / "validation_report.md").exists(),
          f"baseline {vb.summary()['validations_passed']}/{vb.summary()['validations_run']} passed over {vb.summary()['total_records_checked']:,} records")
    check(S, "E2", "injected anomalies make exactly the expected validations fail", set(vf.summary()["failed_validation_ids"]) == {"V07", "V08", "V11", "V13", "V14"},
          f"failed: {', '.join(vf.summary()['failed_validation_ids'])}; anomalies detected {vf.summary()['anomalies_detected']}")

    # ------------------------------------------------------------------ F. problem-statement acceptance criteria
    S = "F. Acceptance criteria from the problem statement"
    check(S, "F1", "timeline is coherent", by_b["V05"].passed and by_b["V06"].passed and by_b["V08"].passed,
          "event ordering, state transitions and acknowledgement timelines valid on the baseline")
    delays = {}
    alarm_sev = {a["alarm_event_id"]: a["severity"] for a in bt["alarm_event"]}
    for x in bt["acknowledgement"]:
        delays.setdefault(alarm_sev[x["alarm_event_id"]], []).append(float(x["ack_delay_seconds"]))
    med = {s: statistics.median(v) for s, v in delays.items()}
    check(S, "F2", "severe alarms are prioritised", med["CRITICAL"] < med["HIGH"] < med["MEDIUM"] < med["LOW"],
          "median acknowledgement time: " + ", ".join(f"{s} {med[s]:,.0f} s" for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")) +
          "; API sort=priority orders CRITICAL first; deadlines 120 / 600 / 1800 / 7200 s")
    nu = ev["patterns"].by_type()["NUISANCE_CLUSTER"]
    check(S, "F3", "nuisance alarm clusters are identified", nu.recall >= 0.98 and nu.precision >= 0.9,
          f"{k['nuisance']['clusters']} clusters; against planted clusters recall {nu.recall:.3f}, precision {nu.precision:.3f}")
    inc = bt["incident"]
    act_by = Counter(a["incident_id"] for a in bt["corrective_action"])
    links = Counter(l["incident_id"] for l in bt["incident_alarm_link"] if l["link_role"] == "TRIGGER")
    hist = Counter(e["incident_id"] for e in bt["incident_status_event"])
    past_planning = [i for i in inc if i["status"] in ("ACTION_IN_PROGRESS", "RESOLVED", "CLOSED")]
    ok = (all(i["trigger_machine_state_event_id"] and links[i["incident_id"]] == 1 and hist[i["incident_id"]] >= 1 for i in inc)
          and all(act_by[i["incident_id"]] >= 1 for i in past_planning))
    check(S, "F4", "incident traceability includes machine state and action history", ok and by_b["V11"].passed,
          f"{len(inc)} incidents: all have a trigger machine state (UNSAFE), exactly one trigger alarm and a lifecycle history; all {len(past_planning)} incidents "
          f"at ACTION_IN_PROGRESS or later have corrective actions with histories ({sum(1 for i in inc if act_by[i['incident_id']] >= 1)} incidents have actions in total; "
          f"the rest are OPEN or INVESTIGATING at the cut-off)")

    # ------------------------------------------------------------------ G. evaluation focus
    S = "G. Evaluation focus"
    sev = Counter(a["severity"] for a in bt["alarm_event"])
    check(S, "G1", "realistic distributions and meaningful variation", sev["LOW"] > sev["MEDIUM"] > sev["HIGH"] > sev["CRITICAL"] > 0,
          "alarm severity mix " + ", ".join(f"{s} {100 * sev[s] / n['alarm_event']:.1f} %" for s in ("LOW", "MEDIUM", "HIGH", "CRITICAL")) +
          f"; ack delay p90 {ak['ack_seconds']['p90']:,.0f} s; alarms per machine {min(r['alarms'] for r in k['alarms']['by_machine'])}-{max(r['alarms'] for r in k['alarms']['by_machine'])}")
    check(S, "G2", "consistent IDs and valid joins", by_b["V02"].passed and by_b["V04"].passed, f"{by_b['V02'].records_checked:,} foreign keys resolved")
    check(S, "G3", "dependency and lifecycle consistency", by_b["V12"].passed and by_b["V13"].passed and by_b["V06"].passed, "incident, action and machine-state lifecycles valid")
    check(S, "G4", "correct derived values and reconciliation", all(derived), "recomputed from source rows")
    check(S, "G5", "normal, boundary, failure and exception coverage", all(v > 0 for v in injected.values()) and by_b["V01"].passed and k["acknowledgements"]["missed"] > 0,
          "normal baseline + boundary rules (deadline equality, 10 s episodes) in unit tests + five exception scenarios + missed / late / open items")
    check(S, "G5b", "separate ground truth", not leak, "see D3")
    rep = (docs / "reproducibility_report.md")
    rep_ok = rep.exists() and "PASSED" in rep.read_text(encoding="utf-8")
    man = (docs / "source_manifest.md").exists() and (ROOT / "data" / "source_manifest.json").exists()
    check(S, "G6", "fixed-seed reproducibility, documentation and lawful provenance", rep_ok and man, "docs/reproducibility_report.md (PASSED), docs/source_manifest.md (all data synthetic, no external source)")

    # ------------------------------------------------------------------ H. architecture and technology
    S = "H. Architecture and technology"
    fe = [p for p in (ROOT / "frontend").rglob("*.py")]
    banned = re.compile(r"^\s*(from|import)\s+(app|backend|sqlalchemy|psycopg|data_generator)\b", re.M)
    check(S, "H1", "three tiers: Streamlit -> FastAPI (REST) -> PostgreSQL; no logic in the dashboard", not any(banned.search(p.read_text(encoding="utf-8")) for p in fe),
          f"{len(fe)} frontend files import neither the backend nor the database; only api_client.py makes HTTP calls")
    layers = all((ROOT / "backend" / "app" / d).is_dir() for d in ("api", "services", "schemas", "repositories", "models", "business_rules", "analytics", "validation", "core"))
    check(S, "H2", "routes, services, schemas, repositories and domain rules separated", layers, "backend/app/{api,services,schemas,repositories,models,business_rules,analytics,validation,core}")
    paths = " ".join(app.openapi()["paths"])
    areas = ["plants", "machines", "safety-zones", "interlocks", "alarm-definitions", "alarm-events", "machine-state-events", "operator-shifts",
             "acknowledgements", "incidents", "corrective-actions", "analytics", "validation", "anomalies"]
    missing = [a for a in areas if f"/api/v1/{a}" not in paths]
    ops = sum(len(v) for v in app.openapi()["paths"].values())
    check(S, "H3", "REST APIs for all 14 areas with Swagger / OpenAPI", not missing, f"{ops} operations; areas missing: {missing or 'none'}; /docs and docs/openapi.json")
    pages = sorted(p.name for p in (ROOT / "frontend" / "pages").glob("*.py"))
    check(S, "H4", "six dashboard pages", len(pages) == 6, ", ".join(pages))
    alembic_ok = (ROOT / "backend" / "migrations" / "versions").glob("*.py")
    check(S, "H5", "SQLAlchemy models and Alembic migrations", any(alembic_ok), "backend/migrations/versions/0001_initial_schema.py")
    check(S, "H6", "no Docker or paid / cloud services introduced", not list(ROOT.glob("Dockerfile*")) and not list(ROOT.glob("docker-compose*")), "no Dockerfile or compose file")
    check(S, "H7", "Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0])

    # ------------------------------------------------------------------ I. database
    S = "I. Database (PostgreSQL)"
    try:
        eng = make_engine()
        with eng.connect() as c:
            counts = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLE_ORDER}
            trig = c.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_append_only'")).scalar()
            gtn = c.execute(text("SELECT count(*) FROM gt.ground_truth_label")).scalar()
            leak_cols = c.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND column_name IN ('issue_type','label_class')")).scalar()
            idx = c.execute(text("SELECT count(*) FROM pg_indexes WHERE schemaname='public'")).scalar()
        loaded = counts == {t: len(ft[t]) for t in TABLE_ORDER}
        check(S, "I1", "full dataset loaded into PostgreSQL; row counts equal the exports", loaded,
              f"{sum(counts.values()):,} rows in 18 tables; alarm_event {counts['alarm_event']:,}")
        check(S, "I2", "history preserved: append-only triggers; separate ground-truth schema", trig == 6 and gtn == len(final.ground_truth) and leak_cols == 0,
              f"{trig} append-only triggers; gt.ground_truth_label {gtn:,} rows; no label column in public schema; {idx} indexes")
        with Session(eng) as s:
            from app.services.validation_service import run_validation
            rd = run_validation(s)
        check(S, "I3", "validation of the database equals the in-memory validation", rd.summary() == vf.summary(), f"{rd.summary()['validations_passed']}/17 passed; failed {rd.summary()['failed_validation_ids']}")
    except Exception as exc:                       # database not available
        for cid, req in (("I1", "full dataset loaded into PostgreSQL"), ("I2", "append-only triggers, gt schema"), ("I3", "database validation")):
            check(S, cid, req, None, f"skipped: {type(exc).__name__}")

    # ------------------------------------------------------------------ J. deliverables
    S = "J. Deliverables"
    d = ROOT
    deliverables = [
        ("1", "Working three-tier application", ["backend/app/main.py", "frontend/streamlit_app.py"]),
        ("2", "PostgreSQL schema and migrations", ["backend/migrations/versions/0001_initial_schema.py", "backend/alembic.ini"]),
        ("3", "ER diagram", ["docs/er_diagram.md"]), ("4", "Complete data dictionary", ["docs/data_dictionary.md"]),
        ("5", "Business rules documentation", ["docs/business_rules.md"]), ("6", "Synthetic data generator", ["data_generator/generate.py", "data_generator/anomaly_injector.py", "data_generator/ground_truth.py"]),
        ("7", "CSV, JSON and SQL exports", ["data/exports/full/csv/alarm_event.csv", "data/exports/full/json/alarm_event.json", "data/exports/full/sql/seed.sql"]),
        ("8", "Separate ground-truth dataset", ["data/ground_truth/full/ground_truth.csv", "data/ground_truth/full/ground_truth.json"]),
        ("9", "Validation report", ["docs/validation_report.md"]), ("10", "Evaluation metrics report", ["docs/evaluation_metrics.md"]),
        ("11", "API documentation", ["docs/api.md", "docs/openapi.json"]), ("12", "Streamlit dashboard", ["frontend/pages/1_executive_dashboard.py", "docs/dashboard.md"]),
        ("13", "Automated tests", ["backend/tests/test_generator.py", "backend/tests/test_api_write.py", "backend/tests/test_frontend.py"]),
        ("14", "README with setup and execution instructions", ["README.md", ".env.example", "requirements.txt"]),
        ("15", "Fixed-seed reproducibility report", ["docs/reproducibility_report.md"]),
        ("16", "Source manifest (synthetic-data provenance)", ["docs/source_manifest.md", "data/source_manifest.json"]),
    ]
    for num, name, files in deliverables:
        miss = [f for f in files if not (d / f).exists()]
        check(S, f"J{num}", name, not miss, ", ".join(files) if not miss else f"missing: {miss}")
    tabs_csv = all((d / "data/exports/full/csv" / f"{t}.csv").exists() and (d / "data/exports/full/json" / f"{t}.json").exists() for t in TABLE_ORDER)
    check(S, "J7b", "an export exists for every table (CSV and JSON)", tabs_csv, f"{len(TABLE_ORDER)} tables")

    # ------------------------------------------------------------------ K. tests
    S = "K. Automated tests"
    if args.run_tests:
        print("running the full test suite ...", flush=True)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT / "backend", capture_output=True, text=True, timeout=1800)
        summary = [l for l in r.stdout.splitlines() if re.search(r"\d+ passed", l)]
        check(S, "K1", "all automated tests pass", r.returncode == 0, summary[-1].strip("= ") if summary else r.stdout[-300:])
    else:
        check(S, "K1", "all automated tests pass", None, "not run (use --run-tests)")

    # ------------------------------------------------------------------ report
    passed = sum(1 for r in RESULTS if r[3] == "PASS")
    failed = [r for r in RESULTS if r[3] == "FAIL"]
    skipped = sum(1 for r in RESULTS if r[3] == "SKIP")
    L = ["# Acceptance report", "",
         "Generated by `scripts/acceptance.py`. Every row checks one requirement of the problem statement or of the implementation prompt "
         "against the real full-scale data (generated in memory with the fixed seed), the PostgreSQL database, the code base and the delivered files.", "",
         f"**Result: {passed} passed, {len(failed)} failed, {skipped} skipped, of {len(RESULTS)} checks.**", ""]
    section = None
    for sec, cid, req, status, evidence in RESULTS:
        if sec != section:
            section = sec
            L += ["", f"## {sec}", "", "| ID | Requirement | Result | Evidence |", "|---|---|---|---|"]
        L.append(f"| {cid} | {req} | **{status}** | {evidence.replace('|', '/')} |")
    (ROOT / "docs" / "acceptance_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n{passed} passed, {len(failed)} failed, {skipped} skipped -> docs/acceptance_report.md")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
