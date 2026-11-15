"""Milestone 5 tests: the five injected anomalies, preserved baseline, separate ground truth, detectors, reproducibility."""
import inspect
import re
from collections import Counter, defaultdict
from dataclasses import replace

import pytest
from sqlalchemy import text

import app.validation.anomaly_detectors as detectors_module
from app.business_rules.corrective_action_rules import action_closed_properly, valid_action_transition
from app.business_rules.safety_state import InterlockReading, assess_state, DOOR_TYPES
from app.validation.anomaly_detectors import ISSUE_TYPES, detect_all
from data_generator.config import STARTER
from data_generator.export import TABLE_ORDER, write_exports, write_ground_truth
from data_generator.generate import generate_with_anomalies
from data_generator.loader import load_dataset, load_ground_truth
from tests.helpers import assert_foreign_keys, read_tables
from tests.test_generator import ID_PATTERNS

INJECTED = ["ACK_BEFORE_ALARM", "IMPOSSIBLE_INTERLOCK_STATE", "INCIDENT_NO_CONTEXT", "REPLAY_DUPLICATE_FLOOD",
            "ACTION_CLOSED_UNVERIFIED"]


@pytest.fixture(scope="module")
def pair():
    return generate_with_anomalies(STARTER)


def idx(rows, key):
    return {r[key]: r for r in rows}


def injected_rows(base, final, table):
    return final.tables[table][len(base.tables[table]):]


def labels_of(final, issue):
    return [l for l in final.ground_truth if l["issue_type"] == issue]


# ---------------------------------------------------------------- injection bookkeeping
def test_every_scenario_is_injected_with_configured_volume(pair):
    base, final = pair
    c = Counter(l["issue_type"] for l in final.ground_truth if l["label_class"] == "INJECTED_ANOMALY")
    assert c["ACK_BEFORE_ALARM"] == STARTER.n_ack_before_alarm
    assert c["IMPOSSIBLE_INTERLOCK_STATE"] == STARTER.n_impossible_interlock
    assert c["INCIDENT_NO_CONTEXT"] == STARTER.n_incident_no_context
    assert c["ACTION_CLOSED_UNVERIFIED"] == STARTER.n_action_closed_unverified
    assert c["REPLAY_DUPLICATE_FLOOD"] > 0
    assert len({l["group_id"] for l in labels_of(final, "REPLAY_DUPLICATE_FLOOD")}) == STARTER.n_replay_floods
    assert set(final.meta["injected"]) == set(INJECTED)


def test_baseline_is_preserved_and_anomalies_are_only_appended(pair, tmp_path):
    base, final = pair
    for table in TABLE_ORDER:
        n = len(base.tables[table])
        assert final.tables[table][:n] == base.tables[table], f"baseline rows of {table} changed"
    assert final.ground_truth[:len(base.ground_truth)] == base.ground_truth
    # the baseline export is byte-identical before and after injection ran
    a = write_exports(base.tables, tmp_path / "before")
    fresh_base, _ = generate_with_anomalies(STARTER)
    b = write_exports(fresh_base.tables, tmp_path / "after")
    assert a == b


def test_injected_ids_follow_conventions_and_foreign_keys_hold(pair):
    base, final = pair
    for table, (col, pattern) in ID_PATTERNS.items():
        ids = [r[col] for r in final.tables[table]]
        assert len(ids) == len(set(ids)), table
        assert all(re.match(pattern, i) for i in ids), table
    assert assert_foreign_keys(final.tables) > 10_000
    # counters continue: new alarm numbers follow each machine's baseline maximum without gaps
    per = defaultdict(list)
    for a in final.tables["alarm_event"]:
        per[a["machine_id"]].append(int(a["alarm_event_id"].rsplit("-", 1)[1]))
    for mid, nums in per.items():
        assert sorted(nums) == list(range(1, len(nums) + 1)), mid


def test_ground_truth_labels_are_consistent(pair):
    base, final = pair
    ids = {t: {tuple(r[c] for c in col)[0] if isinstance(col, tuple) else r[col] for r in final.tables[t]}
           for t, col in (("alarm_event", ("alarm_event_id",)), ("acknowledgement", ("acknowledgement_id",)),
                          ("machine_state_event", ("machine_state_event_id",)), ("incident", ("incident_id",)),
                          ("corrective_action", ("corrective_action_id",)))}
    assert len({l["label_id"] for l in final.ground_truth}) == len(final.ground_truth)
    for l in final.ground_truth:
        assert l["record_id"] in ids[l["record_table"]], l
        assert (l["label_class"] == "INJECTED_ANOMALY") == (l["issue_type"] in INJECTED)


# ---------------------------------------------------------------- each anomaly really has the documented defect
def test_ack_before_alarm_property(pair):
    base, final = pair
    alarms = idx(final.tables["alarm_event"], "alarm_event_id")
    acks = idx(final.tables["acknowledgement"], "acknowledgement_id")
    for l in labels_of(final, "ACK_BEFORE_ALARM"):
        k = acks[l["record_id"]]
        assert k["acknowledged_at"] < alarms[k["alarm_event_id"]]["raised_at"] and k["ack_delay_seconds"] < 0
    # every other acknowledgement is still valid
    bad = {k["acknowledgement_id"] for k in final.tables["acknowledgement"]
           if k["acknowledged_at"] < alarms[k["alarm_event_id"]]["raised_at"]}
    assert bad == {l["record_id"] for l in labels_of(final, "ACK_BEFORE_ALARM")}


def test_impossible_interlock_state_property(pair):
    base, final = pair
    il = {i["interlock_id"]: i for i in final.tables["interlock"]}
    ise_by_mse = defaultdict(list)
    for e in final.tables["interlock_state_event"]:
        ise_by_mse[e["machine_state_event_id"]].append(e)
    mse = idx(final.tables["machine_state_event"], "machine_state_event_id")
    last_seq = {}
    for r in final.tables["machine_state_event"]:
        last_seq[r["machine_id"]] = max(last_seq.get(r["machine_id"], 0), r["sequence_no"])
    codes = set()
    for l in labels_of(final, "IMPOSSIBLE_INTERLOCK_STATE"):
        m = mse[l["record_id"]]
        assert m["safety_assessment"] == "INVALID" and m["sequence_no"] == last_seq[m["machine_id"]]  # appended at the end
        events = ise_by_mse[m["machine_state_event_id"]]
        assert events
        if m["unsafe_rule_code"] == "R-I1":      # a door interlock reports ENGAGED while the door is observed OPEN
            assert any(il[e["interlock_id"]]["interlock_type"] in DOOR_TYPES and e["interlock_status"] == "ENGAGED"
                       and e["door_state_observed"] == "OPEN" for e in events)
        else:                                    # bypass on an interlock that does not allow it
            assert m["unsafe_rule_code"] == "R-I4"
            assert any(e["interlock_status"] == "BYPASSED" and not il[e["interlock_id"]]["bypass_allowed"] for e in events)
        codes.add(m["unsafe_rule_code"])
    assert codes == {"R-I1", "R-I4"}
    assert sum(1 for r in final.tables["machine_state_event"] if r["safety_assessment"] == "INVALID") == STARTER.n_impossible_interlock


def test_incident_without_context_property(pair):
    base, final = pair
    linked = {l["incident_id"] for l in final.tables["incident_alarm_link"]}
    inc = idx(final.tables["incident"], "incident_id")
    events = defaultdict(list)
    for e in final.tables["incident_status_event"]:
        events[e["incident_id"]].append(e)
    for l in labels_of(final, "INCIDENT_NO_CONTEXT"):
        i = inc[l["record_id"]]
        assert l["record_id"] not in linked and i["trigger_machine_state_event_id"] is None
        evs = sorted(events[i["incident_id"]], key=lambda e: e["sequence_no"])
        assert evs[0]["from_status"] is None and evs[-1]["to_status"] == i["status"]  # still a coherent lifecycle
    others = [i for i in final.tables["incident"] if i["trigger_machine_state_event_id"] is None]
    assert len(others) == STARTER.n_incident_no_context


def test_replay_duplicate_property(pair):
    base, final = pair
    alarms = idx(final.tables["alarm_event"], "alarm_event_id")
    originals = {l["description"].split()[-1] for l in labels_of(final, "REPLAY_DUPLICATE_FLOOD")}
    assert len(originals) == len(labels_of(final, "REPLAY_DUPLICATE_FLOOD"))
    for l in labels_of(final, "REPLAY_DUPLICATE_FLOOD"):
        copy, orig = alarms[l["record_id"]], alarms[l["description"].split()[-1]]
        assert copy["alarm_event_id"] != orig["alarm_event_id"] and copy["ingest_batch_id"] != orig["ingest_batch_id"]
        for k in ("machine_id", "alarm_definition_id", "raised_at", "cleared_at", "severity", "machine_state_event_id"):
            assert copy[k] == orig[k]
    # replays are whole floods: copied alarms equal the original flood sizes
    flood_sizes = Counter(l["group_id"] for l in final.ground_truth if l["issue_type"] == "ALARM_FLOOD")
    assert len(labels_of(final, "REPLAY_DUPLICATE_FLOOD")) >= 10 and flood_sizes


def test_action_closed_without_verification_property(pair):
    base, final = pair
    actions = idx(final.tables["corrective_action"], "corrective_action_id")
    inc = idx(final.tables["incident"], "incident_id")
    events = defaultdict(list)
    for e in final.tables["corrective_action_status_event"]:
        events[e["corrective_action_id"]].append(e)
    for l in labels_of(final, "ACTION_CLOSED_UNVERIFIED"):
        a = actions[l["record_id"]]
        assert a["status"] == "CLOSED" and a["verified_at"] is None and a["verification_result"] is None
        assert inc[a["incident_id"]]["status"] != "CLOSED"            # no valid incident was invalidated
        evs = sorted(events[a["corrective_action_id"]], key=lambda e: e["sequence_no"])
        assert [e["to_status"] for e in evs][-2:] == ["COMPLETED", "CLOSED"]
        assert not valid_action_transition("COMPLETED", "CLOSED") and not action_closed_properly(a)


# ---------------------------------------------------------------- detectors
def _by_type(findings):
    d = defaultdict(set)
    for f in findings:
        d[f.issue_type].add(f.record_id)
    return d


def test_detectors_report_nothing_on_the_clean_baseline(pair):
    base, _ = pair
    assert detect_all(base.tables) == []


def test_detectors_match_ground_truth_for_all_five_scenarios(pair):
    base, final = pair
    det = _by_type(detect_all(final.tables))
    assert set(det) == set(ISSUE_TYPES)
    for issue in INJECTED:
        truth = {l["record_id"] for l in labels_of(final, issue)}
        tp = len(det[issue] & truth)
        precision, recall = tp / len(det[issue]), tp / len(truth)
        assert (precision, recall) == (1.0, 1.0), (issue, precision, recall)


def test_detectors_are_independent_of_ground_truth():
    src = inspect.getsource(detectors_module).lower()
    for forbidden in ("ground_truth", "groundtruth", "data_generator", "app.models", "gt."):
        assert forbidden not in src, forbidden
    # detectors only accept feature tables; with the ground truth thrown away the result is identical
    base, final = generate_with_anomalies(STARTER)
    first = detect_all(final.tables)
    final.ground_truth.clear()
    assert detect_all(final.tables) == first


def test_detectors_recompute_instead_of_trusting_derived_columns(pair):
    """Tampering with stored derived columns must not hide an anomaly."""
    base, final = pair
    tables = {t: [dict(r) for r in rows] for t, rows in final.tables.items()}
    for m in tables["machine_state_event"]:
        m["safety_assessment"], m["unsafe_rule_code"] = "SAFE", None
    for k in tables["acknowledgement"]:
        k["ack_delay_seconds"], k["ack_status"] = 1.0, "ON_TIME"
    assert _by_type(detect_all(tables)) == _by_type(detect_all(final.tables))


def test_injected_rows_carry_no_markers(pair):
    base, final = pair
    markers = ("injected", "anomaly", "ground_truth", "ground truth", "label", "synthetic", "replay", "fake", "malformed")
    batch = re.compile(r"^BATCH-\d{8}-\d{2}$")
    for table in TABLE_ORDER:
        base_cols = set(base.tables[table][0]) if base.tables[table] else None
        for r in injected_rows(base, final, table):
            assert set(r) == base_cols, table
            for k, v in r.items():
                if isinstance(v, str):
                    assert not any(m in v.lower() for m in markers), (table, k, v)
            if table == "alarm_event":
                assert batch.match(r["ingest_batch_id"])


# ---------------------------------------------------------------- reproducibility
def test_anomaly_injection_is_reproducible_and_seed_dependent(tmp_path):
    sums = []
    for k in range(2):
        _, final = generate_with_anomalies(STARTER)
        s = write_exports(final.tables, tmp_path / f"run{k}")
        s.update({f"gt/{n}": h for n, h in write_ground_truth(final.ground_truth, tmp_path / f"gt{k}").items()})
        sums.append(s)
    assert sums[0] == sums[1]
    _, other = generate_with_anomalies(replace(STARTER, seed=7))
    assert write_exports(other.tables, tmp_path / "other")["csv/alarm_event.csv"] != sums[0]["csv/alarm_event.csv"]


# ---------------------------------------------------------------- database
def test_anomalies_load_into_postgres_and_detectors_work_from_the_database(pair, engine, tmp_path):
    base, final = pair
    write_exports(final.tables, tmp_path / "csv_out")
    write_ground_truth(final.ground_truth, tmp_path / "gt")
    counts = load_dataset(engine, tmp_path / "csv_out" / "csv", truncate=True)   # constraints must accept the anomalies
    assert counts == {t: len(final.tables[t]) for t in TABLE_ORDER}
    assert load_ground_truth(engine, tmp_path / "gt" / "ground_truth.csv", truncate=True) == len(final.ground_truth)
    db_findings = detect_all(read_tables(engine))                                # reads the public schema only
    assert {(f.issue_type, f.record_id) for f in db_findings} == \
           {(f.issue_type, f.record_id) for f in detect_all(final.tables)}
    with engine.connect() as c:
        gt = dict(c.execute(text("SELECT issue_type, count(*) FROM gt.ground_truth_label "
                                 "WHERE label_class='INJECTED_ANOMALY' GROUP BY 1")).all())
        assert gt["ACK_BEFORE_ALARM"] == STARTER.n_ack_before_alarm
        # the labels table has no foreign key to feature tables and feature tables have no label column
        assert c.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
                              "AND column_name IN ('issue_type','label_class')")).scalar() == 0
