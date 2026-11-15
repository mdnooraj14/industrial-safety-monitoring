"""Milestone 7 tests: business KPIs, filters, and detection-evaluation metrics (kept separate)."""
import importlib.util
import inspect
import pkgutil
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy.orm import Session

import app
from app.analytics import evaluation as evaluation_module
from app.analytics import kpis as kpis_module
from app.analytics.evaluation import NONE, evaluate
from app.analytics.filters import Filters, apply_filters
from app.analytics.kpis import compute_kpis
from app.analytics.prep import infer_cutoff
from app.services.analytics_service import business_kpis
from app.services.evaluation_service import evaluate_database, evaluate_tables
from app.validation.anomaly_detectors import Finding
from data_generator.config import STARTER
from data_generator.export import TABLE_ORDER, write_exports, write_ground_truth
from data_generator.generate import generate_with_anomalies
from data_generator.loader import load_dataset, load_ground_truth

UTC = timezone.utc
T0 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 10, 10, tzinfo=UTC)
DEADLINE = {"CRITICAL": 120, "HIGH": 600, "MEDIUM": 1800, "LOW": 7200}


# ======================================================================== hand-computed fixture
def A(n, mid, sev, raised, batch="BATCH-20261003-01", dfn="ADEF-0001"):
    return dict(alarm_event_id=f"ALM-{mid}-{n:06d}", machine_id=mid, alarm_definition_id=dfn, severity=sev,
                raised_at=raised, cleared_at=None, ack_deadline_at=raised + timedelta(seconds=DEADLINE[sev]),
                ingest_batch_id=batch)


def K(alarm, delay):
    return dict(acknowledgement_id=f"ACK-{alarm['alarm_event_id']}-01", alarm_event_id=alarm["alarm_event_id"],
                acknowledged_at=alarm["raised_at"] + timedelta(seconds=delay))


def INC(n, mid, sev, status, opened, inv=None, resolved=None):
    return dict(incident_id=f"INC-2026-{n:06d}", machine_id=mid, severity=sev, status=status, trigger_rule_code="R-U1",
                opened_at=opened, investigation_started_at=inv, resolved_at=resolved, closed_at=None)


def ACT(n, status, verification=None, due=CUTOFF + timedelta(days=5), closed=None):
    return dict(corrective_action_id=f"ACT-INC-2026-000001-{n:02d}", incident_id="INC-2026-000001", status=status,
                verification_result=verification, due_at=due, planned_at=T0, closed_at=closed)


@pytest.fixture(scope="module")
def mini():
    m1, m2, m3 = "P01-M01", "P01-M02", "P02-M01"
    a1, a2 = A(1, m1, "CRITICAL", T0), A(2, m1, "CRITICAL", T0 + timedelta(hours=1))
    a3, a4 = A(3, m1, "HIGH", T0 + timedelta(hours=2)), A(1, m2, "HIGH", T0)
    a5, a6 = A(2, m2, "MEDIUM", T0 + timedelta(hours=1)), A(3, m2, "LOW", T0 + timedelta(hours=2))
    a7, a8 = A(1, m3, "LOW", CUTOFF - timedelta(seconds=60)), A(2, m3, "MEDIUM", T0)
    a9 = A(4, m1, "LOW", T0 + timedelta(hours=3))
    dup = dict(A(9, m1, "CRITICAL", T0, batch="BATCH-20261003-02"))      # replay of a1
    t = {name: [] for name in TABLE_ORDER}
    t["plant"] = [dict(plant_id="P01", plant_name="Pune"), dict(plant_id="P02", plant_name="Stuttgart")]
    t["machine"] = [dict(machine_id=m1, plant_id="P01", machine_name="Press 1"),
                    dict(machine_id=m2, plant_id="P01", machine_name="Mill 2"),
                    dict(machine_id=m3, plant_id="P02", machine_name="Robot 3")]
    t["alarm_event"] = [a1, a2, a3, a4, a5, a6, a7, a8, a9, dup]
    t["acknowledgement"] = [K(a1, 60), K(a2, 300), K(a4, 100), K(a5, 2000), K(a8, 10), K(a9, -45)]   # last one invalid
    t["incident"] = [
        INC(1, m1, "CRITICAL", "CLOSED", T0, T0 + timedelta(hours=1), T0 + timedelta(hours=10)),
        INC(2, m1, "HIGH", "ACTION_IN_PROGRESS", T0, T0 + timedelta(hours=2)),
        INC(3, m2, "CRITICAL", "RESOLVED", T0, T0 + timedelta(hours=3), T0 + timedelta(hours=20)),
        INC(4, m3, "HIGH", "OPEN", T0)]
    t["corrective_action"] = [
        ACT(1, "CLOSED", "PASSED", closed=T0 + timedelta(days=1)), ACT(2, "CLOSED", "PASSED", closed=T0 + timedelta(days=2)),
        ACT(3, "COMPLETED", due=CUTOFF - timedelta(days=1)), ACT(4, "IN_PROGRESS"), ACT(5, "CANCELLED"),
        ACT(6, "VERIFIED", "PASSED"), ACT(7, "CLOSED", closed=T0 + timedelta(days=3))]            # closed, unverified
    return t


def test_alarm_counts_by_plant_machine_and_severity(mini):
    k = compute_kpis(mini, cutoff=CUTOFF)
    a = k["alarms"]
    assert a["total"] == 9 and k["overview"]["alarm_events_stored"] == 10 and k["overview"]["replay_duplicates_excluded"] == 1
    assert a["by_severity"] == {"CRITICAL": 2, "HIGH": 2, "MEDIUM": 2, "LOW": 3}
    assert (a["critical"], a["high"], a["critical_and_high"]) == (2, 2, 4)
    assert {r["plant_id"]: r["alarms"] for r in a["by_plant"]} == {"P01": 7, "P02": 2}
    assert {r["machine_id"]: r["alarms"] for r in a["by_machine"]} == {"P01-M01": 4, "P01-M02": 3, "P02-M01": 2}
    assert a["by_machine"][0]["machine_id"] == "P01-M01" and a["by_machine"][0]["share_pct"] == round(100 * 4 / 9, 2)


def test_acknowledgement_kpis(mini):
    ak = compute_kpis(mini, cutoff=CUTOFF)["acknowledgements"]
    assert (ak["on_time"], ak["late"], ak["missed"], ak["pending"], ak["acknowledged"]) == (3, 2, 3, 1, 5)
    assert ak["ack_seconds"]["mean"] == 494.0 and ak["ack_seconds"]["median"] == 100.0       # (60+300+100+2000+10)/5
    assert ak["delayed_total"] == 5 and ak["delayed_pct"] == 62.5                              # 5 / (3+2+3)
    assert ak["late_pct_of_acknowledged"] == 40.0 and ak["invalid_acknowledgements_excluded"] == 1
    crit = ak["by_severity"]["CRITICAL"]
    assert (crit["alarms"], crit["acknowledged"], crit["avg_ack_seconds"], crit["late"], crit["delayed_pct"]) == (2, 2, 180.0, 1, 50.0)
    assert sum(b["count"] for b in ak["delay_buckets"]) == ak["acknowledged"]
    assert [b["count"] for b in ak["delay_buckets"]] == [2, 2, 0, 1, 0]                        # delays 60,10 | 300,100 | - | 2000 | -


def test_pending_depends_on_the_cutoff(mini):
    early = compute_kpis(mini, cutoff=T0 + timedelta(minutes=30))["acknowledgements"]
    assert early["missed"] < 3 and early["pending"] > 1                                        # nothing is "missed" before its deadline


def test_incident_kpis(mini):
    i = compute_kpis(mini, cutoff=CUTOFF)["incidents"]
    assert (i["total"], i["critical"], i["open"], i["unresolved"], i["resolved_count"]) == (4, 2, 3, 2, 2)
    assert i["mttr_hours"] == 15.0 and i["mttr_by_severity_hours"]["CRITICAL"] == 15.0         # (10 + 20) / 2
    assert i["mean_time_to_investigate_hours"] == 2.0                                          # (1 + 2 + 3) / 3
    assert {r["machine_id"]: r["incidents"] for r in i["by_machine"]} == {"P01-M01": 2, "P01-M02": 1, "P02-M01": 1}
    assert {o["incident_id"] for o in i["open_incidents"]} == {"INC-2026-000002", "INC-2026-000003", "INC-2026-000004"}
    assert i["by_status"] == {"CLOSED": 1, "ACTION_IN_PROGRESS": 1, "RESOLVED": 1, "OPEN": 1}


def test_corrective_action_kpis(mini):
    c = compute_kpis(mini, cutoff=CUTOFF)["corrective_actions"]
    assert (c["total"], c["live"]) == (7, 6)
    assert c["completion_rate_pct"] == 83.33 and c["verification_rate_pct"] == 60.0            # 5/6 and 3/5
    assert c["closed_without_verification"] == 1 and c["overdue"] == 1 and c["failed_verifications"] == 0


def test_kpi_payload_is_labelled_business_and_has_no_detection_metrics(mini):
    k = compute_kpis(mini, cutoff=CUTOFF)
    assert k["meta"]["kpi_type"] == "business"
    flat = str(k).lower()
    assert "precision" not in flat and "recall" not in flat and "f1" not in flat


# ======================================================================== independent recomputation (pandas)
@pytest.fixture(scope="module")
def pair():
    return generate_with_anomalies(STARTER)


def test_kpis_match_independent_pandas_recomputation(pair):
    final = pair[1]
    k = compute_kpis(final.tables, cutoff=STARTER.end)
    al = pd.DataFrame(final.tables["alarm_event"])
    al["key"] = list(zip(al.machine_id, al.alarm_definition_id, al.raised_at.dt.floor("s")))
    g = al.groupby("key").ingest_batch_id
    al["dup"] = (g.transform("nunique") > 1) & (al.ingest_batch_id != g.transform("min"))
    ok = al[~al.dup].copy()
    assert k["alarms"]["total"] == len(ok) and k["overview"]["replay_duplicates_excluded"] == int(al.dup.sum())
    assert {r["machine_id"]: r["alarms"] for r in k["alarms"]["by_machine"]} == ok.machine_id.value_counts().to_dict()
    assert k["alarms"]["by_severity"] == {s: int((ok.severity == s).sum()) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    ack = pd.DataFrame(final.tables["acknowledgement"]).merge(ok, on="alarm_event_id")
    ack["delay"] = (ack.acknowledged_at - ack.raised_at).dt.total_seconds()
    valid = ack[ack.delay >= 0]
    assert k["acknowledgements"]["acknowledged"] == len(valid)
    assert k["acknowledgements"]["ack_seconds"]["mean"] == round(valid.delay.mean(), 2)
    assert k["acknowledgements"]["ack_seconds"]["median"] == round(valid.delay.median(), 2)
    late = int((valid.acknowledged_at > valid.ack_deadline_at).sum())
    acked = set(valid.alarm_event_id)
    missed = int(((~ok.alarm_event_id.isin(acked)) & (ok.ack_deadline_at < pd.Timestamp(STARTER.end))).sum())
    due = len(valid) + missed
    assert (k["acknowledgements"]["late"], k["acknowledgements"]["missed"]) == (late, missed)
    assert k["acknowledgements"]["delayed_pct"] == round(100 * (late + missed) / due, 2)
    inc = pd.DataFrame(final.tables["incident"])
    assert {r["machine_id"]: r["incidents"] for r in k["incidents"]["by_machine"]} == inc.machine_id.value_counts().to_dict()
    res = inc.dropna(subset=["resolved_at"])
    assert k["incidents"]["mttr_hours"] == round(((res.resolved_at - res.opened_at).dt.total_seconds() / 3600).mean(), 2)
    assert k["incidents"]["open"] == int((inc.status != "CLOSED").sum())
    act = pd.DataFrame(final.tables["corrective_action"])
    live = act[act.status != "CANCELLED"]
    done = live[live.status.isin(["COMPLETED", "VERIFIED", "CLOSED"])]
    assert k["corrective_actions"]["completion_rate_pct"] == round(100 * len(done) / len(live), 2)
    assert k["corrective_actions"]["verification_rate_pct"] == round(100 * done.verification_result.notna().sum() / len(done), 2)


def test_data_quality_defects_do_not_distort_business_kpis(pair):
    base, final = pair
    kb, kf = compute_kpis(base.tables, cutoff=STARTER.end), compute_kpis(final.tables, cutoff=STARTER.end)
    assert kf["overview"]["replay_duplicates_excluded"] == STARTER_REPLAYS(final)
    assert kf["acknowledgements"]["invalid_acknowledgements_excluded"] == STARTER.n_ack_before_alarm
    assert kf["acknowledgements"]["acknowledged"] == kb["acknowledgements"]["acknowledged"]     # invalid acks not counted
    assert kf["floods"]["episodes"] == kb["floods"]["episodes"] == STARTER.n_floods              # replay copies do not add floods
    assert kf["nuisance"]["clusters"] == kb["nuisance"]["clusters"]
    assert kf["alarms"]["total"] == kb["alarms"]["total"] + STARTER.n_ack_before_alarm           # only the genuine extra alarms
    assert kb["overview"]["replay_duplicates_excluded"] == 0 and kb["acknowledgements"]["invalid_acknowledgements_excluded"] == 0


def STARTER_REPLAYS(final):
    return sum(1 for l in final.ground_truth if l["issue_type"] == "REPLAY_DUPLICATE_FLOOD")


def test_planted_scenarios_show_up_in_kpis(pair):
    k = compute_kpis(pair[0].tables, cutoff=STARTER.end)
    assert k["floods"]["episodes"] == STARTER.n_floods and k["nuisance"]["clusters"] >= STARTER.n_nuisance_clusters
    assert k["unsafe_conditions"]["repeated_conditions"] >= 1 and k["unsafe_conditions"]["unsafe_episodes"] >= 5
    assert k["incidents"]["total"] == k["unsafe_conditions"]["unsafe_episodes"] > 0
    assert k["acknowledgements"]["delayed_pct"] > 0 and k["floods"]["floods_per_day"] > 0
    assert 0 < k["incidents"]["open"] <= k["incidents"]["total"]


def test_cutoff_inference_rounds_up_in_utc():
    tz = timezone(timedelta(hours=5, minutes=30))
    t = {"alarm_event": [dict(raised_at=datetime(2026, 10, 7, 23, 59, 12, tzinfo=UTC).astimezone(tz))]}
    assert infer_cutoff(t) == datetime(2026, 10, 8, tzinfo=UTC)
    t = {"alarm_event": [dict(raised_at=datetime(2026, 10, 8, 3, 0, 0, tzinfo=UTC))]}
    assert infer_cutoff(t) == datetime(2026, 10, 8, 3, tzinfo=UTC)


# ======================================================================== filters
def test_filters_partition_the_data(pair):
    t = pair[1].tables
    total = compute_kpis(t, cutoff=STARTER.end)["overview"]
    parts = [compute_kpis(t, Filters(plant_id=p["plant_id"]), STARTER.end)["overview"] for p in t["plant"]]
    for key in ("alarm_events_stored", "incidents", "critical_alarms", "high_alarms", "machines"):
        assert sum(p[key] for p in parts) == total[key], key
    assert all(p["plants"] == 1 for p in parts)


def test_machine_severity_date_and_status_filters(pair):
    t = pair[1].tables
    mid = t["machine"][0]["machine_id"]
    sub = apply_filters(t, Filters(machine_id=mid))
    assert {a["machine_id"] for a in sub["alarm_event"]} == {mid} and len(sub["machine"]) == 1 and len(sub["plant"]) == 1
    crit = apply_filters(t, Filters(severity="CRITICAL"))
    assert {a["severity"] for a in crit["alarm_event"]} == {"CRITICAL"} and {i["severity"] for i in crit["incident"]} <= {"CRITICAL"}
    day = date(2026, 10, 3)
    one = apply_filters(t, Filters(date_from=day, date_to=day))
    assert one["alarm_event"] and all(a["raised_at"].date() == day for a in one["alarm_event"])
    assert len(apply_filters(t, Filters(date_from=date(2026, 10, 1), date_to=date(2026, 10, 7)))["alarm_event"]) == len(t["alarm_event"])
    opened = apply_filters(t, Filters(incident_status="ACTION_IN_PROGRESS"))
    assert opened["incident"] and {i["status"] for i in opened["incident"]} == {"ACTION_IN_PROGRESS"}
    # children follow their parents
    assert {l["incident_id"] for l in opened["incident_alarm_link"]} <= {i["incident_id"] for i in opened["incident"]}
    assert {a["incident_id"] for a in opened["corrective_action"]} <= {i["incident_id"] for i in opened["incident"]}
    assert Filters().is_empty() and apply_filters(t, Filters()) == {k: list(v) for k, v in t.items()}


def test_filtered_kpis_use_the_global_cutoff(pair):
    t = pair[1].tables
    f = compute_kpis(t, Filters(plant_id="P01"))
    assert f["meta"]["cutoff"] == compute_kpis(t)["meta"]["cutoff"] and f["meta"]["filters"] == {"plant_id": "P01"}


# ======================================================================== detection evaluation (unit)
def F(t, rid, table="alarm_event"):
    return Finding(t, table, rid, "x")


def L(t, rid, table="alarm_event"):
    return dict(issue_type=t, record_table=table, record_id=rid, label_class="INJECTED_ANOMALY")


def test_precision_recall_f1_hand_computed():
    labels = [L("A", "1"), L("A", "2"), L("A", "3"), L("A", "4"), L("B", "9")]
    findings = [F("A", "1"), F("A", "2"), F("A", "3"), F("A", "7"), F("A", "8"), F("B", "9")]
    r = evaluate(findings, labels, ["A", "B"], "unit")
    a, b = r.by_type()["A"], r.by_type()["B"]
    assert (a.tp, a.fp, a.fn) == (3, 2, 1) and a.precision == 0.6 and a.recall == 0.75
    assert round(a.f1, 4) == round(2 * 0.6 * 0.75 / 1.35, 4)
    assert (b.tp, b.fp, b.fn, b.precision, b.recall, b.f1) == (1, 0, 0, 1.0, 1.0, 1.0)
    assert (r.micro["tp"], r.micro["fp"], r.micro["fn"]) == (4, 2, 1)
    assert r.micro["precision"] == round(4 / 6, 4) and r.micro["recall"] == 0.8
    assert r.macro["precision"] == round((0.6 + 1.0) / 2, 4) and r.macro["recall"] == round((0.75 + 1.0) / 2, 4)
    assert a.false_positive_ids == ["7", "8"] and a.false_negative_ids == ["4"]


def test_undefined_metrics_are_none_not_zero():
    r = evaluate([F("A", "1")], [L("B", "2")], ["A", "B", "C"], "unit")
    t = r.by_type()
    assert t["A"].precision == 0.0 and t["A"].recall is None and t["A"].f1 is None      # flagged, nothing to find
    assert t["B"].precision is None and t["B"].recall == 0.0 and t["B"].f1 is None      # missed, nothing flagged
    assert t["C"].precision is None and t["C"].recall is None and t["C"].f1 is None     # absent class
    assert r.macro["f1"] is None and r.macro["classes_averaged"] == 0
    assert evaluate([], [], ["A"], "empty").micro == {"tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None, "f1": None}


def test_confusion_matrix():
    labels = [L("A", "1"), L("A", "2"), L("B", "3"), L("B", "4")]
    findings = [F("A", "1"), F("B", "2"), F("B", "3"), F("A", "5")]          # 2 misclassified as B, 4 missed, 5 spurious
    r = evaluate(findings, labels, ["A", "B"], "unit", universe={"alarm_event": 20})
    m = r.confusion
    assert r.labels == ["A", "B", NONE]
    assert m["A"] == {"A": 1, "B": 1, NONE: 0} and m["B"] == {"A": 0, "B": 1, NONE: 1}
    assert m[NONE] == {"A": 1, "B": 0, NONE: 20 - 5}                          # 5 distinct records are labelled or flagged
    assert sum(sum(row.values()) for row in m.values()) == 20
    # same record id in a different table is a different record
    r2 = evaluate([F("A", "1", "acknowledgement")], [L("A", "1", "alarm_event")], ["A"], "unit")
    assert (r2.classes[0].tp, r2.classes[0].fp, r2.classes[0].fn) == (0, 1, 1)


def test_labels_and_findings_outside_the_scope_are_ignored():
    r = evaluate([F("A", "1"), F("Z", "2")], [L("A", "1"), L("Y", "3")], ["A"], "unit")
    assert (r.micro["tp"], r.micro["fp"], r.micro["fn"]) == (1, 0, 0)


# ======================================================================== detection evaluation on generated data
def test_detectors_scored_against_ground_truth(pair):
    res = evaluate_tables(pair[1].tables, pair[1].ground_truth)
    an = res["anomalies"]
    assert an.micro["f1"] == 1.0 and all(c.support > 0 and c.f1 == 1.0 for c in an.classes)
    universe = sum(len(pair[1].tables[t]) for t in ("alarm_event", "acknowledgement", "machine_state_event", "incident", "corrective_action"))
    assert an.confusion[NONE][NONE] == universe - 43 and sum(an.confusion[a][p] for a in an.labels for p in an.labels if a != NONE and p != NONE) == 43
    pat = res["patterns"]
    for c in pat.classes:
        assert c.recall >= 0.98 and c.precision >= 0.9, (c.issue_type, c.precision, c.recall)
    assert 0.9 < pat.micro["f1"] < 1.0                                        # informative: not trivially perfect


def test_baseline_alone_has_no_false_positives_for_anomalies(pair):
    res = evaluate_tables(pair[0].tables, [l for l in pair[0].ground_truth])
    an = res["anomalies"]
    assert an.micro["fp"] == 0 and an.micro["tp"] == 0 and an.micro["precision"] is None   # nothing flagged, nothing labelled


# ======================================================================== separation of KPIs and ground truth
def test_only_the_evaluation_service_reads_ground_truth():
    users = set()
    for mod in pkgutil.walk_packages(app.__path__, "app."):
        origin = importlib.util.find_spec(mod.name).origin
        src = Path(origin).read_text(encoding="utf-8")
        if "ground_truth_repository" in src or "gt.ground_truth_label" in src:
            users.add(mod.name)
    assert users == {"app.repositories.ground_truth_repository", "app.services.evaluation_service"}


def test_kpi_and_detector_code_never_touch_labels():
    for mod in (kpis_module, evaluation_module):
        src = inspect.getsource(mod).lower()
        assert "gt." not in src and "ground_truth_repository" not in src
    assert "detect_" not in inspect.getsource(evaluation_module)                # evaluation scores findings, never runs detectors


# ======================================================================== against PostgreSQL
def test_database_kpis_and_evaluation_match_in_memory(pair, engine, tmp_path):
    final = pair[1]
    write_exports(final.tables, tmp_path / "out")
    write_ground_truth(final.ground_truth, tmp_path / "gt")
    load_dataset(engine, tmp_path / "out" / "csv", truncate=True)
    load_ground_truth(engine, tmp_path / "gt" / "ground_truth.csv", truncate=True)
    mem = compute_kpis(final.tables, cutoff=STARTER.end)
    with Session(engine) as db:
        got = business_kpis(db, cutoff=STARTER.end)
        inferred = business_kpis(db)
        ev = evaluate_database(db)
    for section in ("overview", "alarms", "acknowledgements", "floods", "nuisance", "unsafe_conditions", "corrective_actions"):
        assert got[section] == mem[section], section
    assert inferred["meta"]["cutoff"].startswith("2026-10-08T00:00:00")
    assert {k: v.micro for k, v in ev.items()} == {k: v.micro for k, v in evaluate_tables(final.tables, final.ground_truth).items()}
    assert got["incidents"]["total"] == mem["incidents"]["total"] and got["incidents"]["mttr_hours"] == mem["incidents"]["mttr_hours"]
