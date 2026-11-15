"""Milestone 6 tests: the validation module and report."""
import copy
import inspect
import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

import app.validation.checks as checks_module
import app.validation.framework as framework_module
import app.validation.replay as replay_module
import app.validation.validator as validator_module
from app.services.validation_service import run_validation
from app.validation.checks import CHECKS
from app.validation.validator import validate
from data_generator.config import STARTER
from data_generator.export import write_exports, write_ground_truth
from data_generator.generate import generate_with_anomalies
from data_generator.loader import load_dataset, load_ground_truth

ALL_IDS = [f"V{n:02d}" for n in range(1, 18)]
EXPECTED_FAILURES = {  # validation id -> injected issue_type whose records it must report
    "V07": "IMPOSSIBLE_INTERLOCK_STATE", "V08": "ACK_BEFORE_ALARM", "V11": "INCIDENT_NO_CONTEXT",
    "V13": "ACTION_CLOSED_UNVERIFIED", "V14": "REPLAY_DUPLICATE_FLOOD",
}


@pytest.fixture(scope="module")
def pair():
    return generate_with_anomalies(STARTER)


@pytest.fixture(scope="module")
def baseline_report(pair):
    return validate(pair[0].tables)


@pytest.fixture(scope="module")
def final_report(pair):
    return validate(pair[1].tables)


def clone(tables):
    return copy.deepcopy(tables)


def first(rows, pred=lambda r: True):
    return next(r for r in rows if pred(r))


# ---------------------------------------------------------------- baseline and injected data
def test_there_are_17_validations_with_unique_ids():
    assert [c.__name__[:3].upper() for c in CHECKS] == ALL_IDS


def test_clean_baseline_passes_every_validation(baseline_report):
    s = baseline_report.summary()
    assert (s["validations_run"], s["validations_passed"], s["validations_failed"]) == (17, 17, 0)
    assert s["anomalies_detected"] == 0 and s["total_records_checked"] > 7000
    assert all(c.records_checked > 0 for c in baseline_report.checks)        # no check is vacuous


def test_injected_dataset_fails_exactly_the_expected_validations(pair, final_report):
    assert set(final_report.summary()["failed_validation_ids"]) == set(EXPECTED_FAILURES)
    truth = {}
    for l in pair[1].ground_truth:
        truth.setdefault(l["issue_type"], set()).add(l["record_id"])
    by_id = {c.check_id: c for c in final_report.checks}
    for vid, issue in EXPECTED_FAILURES.items():
        assert set(by_id[vid].failed_ids) == truth[issue], vid        # the affected ids ARE the injected records
    # structural validations and all derived-value validations still pass: anomalies do not corrupt unrelated rows
    for vid in set(ALL_IDS) - set(EXPECTED_FAILURES):
        assert by_id[vid].passed, (vid, by_id[vid].failures[:2])


def test_validator_reports_anomaly_counts_consistent_with_failures(final_report):
    s = final_report.summary()
    assert s["anomalies_detected"] == sum(s["anomalies_by_type"].values()) == 43
    assert set(s["anomalies_by_type"]) == {"ACK_BEFORE_ALARM", "ACTION_CLOSED_UNVERIFIED", "IMPOSSIBLE_INTERLOCK_STATE",
                                           "INCIDENT_NO_CONTEXT", "REPLAY_DUPLICATE_FLOOD"}


# ---------------------------------------------------------------- every validation catches its own defect
def _dup_plant(t):
    t["plant"].append(dict(t["plant"][0]))
    return t["plant"][0]["plant_id"]


def _bad_fk(t):
    a = first(t["alarm_event"])
    a["machine_id"] = "P99-M99"
    return a["alarm_event_id"]


def _bad_enum(t):
    m = t["machine"][0]
    m["machine_type"] = "LATHE"
    return m["machine_id"]


def _bad_id(t):
    k = t["acknowledgement"][0]
    old = k["acknowledgement_id"]
    k["acknowledgement_id"] = "ACK-BROKEN-1"
    return "ACK-BROKEN-1"


def _bad_time(t):
    a = first(t["alarm_event"], lambda r: r["cleared_at"])
    a["cleared_at"] = a["raised_at"] - timedelta(seconds=5)
    return a["alarm_event_id"]


def _bad_previous_state(t):
    r = first(t["machine_state_event"], lambda r: r["sequence_no"] == 5 and r["previous_state"] != "FAULT")
    r["previous_state"] = "FAULT"
    return r["machine_state_event_id"]


def _bad_summary(t):
    r = first(t["machine_state_event"], lambda r: r["interlock_status_summary"] == "ALL_ENGAGED")
    r["interlock_status_summary"] = "NONE_ENGAGED"
    return r["machine_state_event_id"]


def _ack_before(t):
    k = t["acknowledgement"][3]
    a = first(t["alarm_event"], lambda r: r["alarm_event_id"] == k["alarm_event_id"])
    k["acknowledged_at"] = a["raised_at"] - timedelta(seconds=30)
    return k["acknowledgement_id"]


def _bad_deadline(t):
    a = first(t["alarm_event"], lambda r: r["severity"] == "LOW")
    a["severity"] = "CRITICAL"
    return a["alarm_event_id"]


def _bad_ack_delay(t):
    k = t["acknowledgement"][5]
    k["ack_delay_seconds"] = k["ack_delay_seconds"] + 10
    return k["acknowledgement_id"]


def _remove_trigger(t):
    inc = t["incident"][0]
    t["incident_alarm_link"][:] = [l for l in t["incident_alarm_link"]
                                   if not (l["incident_id"] == inc["incident_id"] and l["link_role"] == "TRIGGER")]
    return inc["incident_id"]


def _wrong_trigger_rule(t):
    inc = t["incident"][0]
    inc["trigger_rule_code"] = "R-U3" if inc["trigger_rule_code"] != "R-U3" else "R-U1"
    return inc["incident_id"]


def _close_open_incident(t):
    inc = first(t["incident"], lambda r: r["status"] != "CLOSED")
    inc["status"] = "CLOSED"
    return inc["incident_id"]


def _unverify_closed_action(t):
    a = first(t["corrective_action"], lambda r: r["status"] == "CLOSED")
    a["verified_at"] = a["verification_result"] = a["verified_by_id"] = None
    return a["corrective_action_id"]


def _duplicate_ack(t):
    k = dict(t["acknowledgement"][0])
    k["acknowledgement_id"] = k["acknowledgement_id"][:-2] + "02"
    t["acknowledgement"].append(k)
    return k["acknowledgement_id"]


def _flip_ack_status(t):
    k = first(t["acknowledgement"], lambda r: r["ack_status"] == "ON_TIME")
    k["ack_status"] = "LATE"
    return "ON_TIME acknowledgements recount"      # reconciliation failures are named after the total that broke


def _drop_primary_zone(t):
    m = t["machine"][0]["machine_id"]
    t["machine_safety_zone"][:] = [r for r in t["machine_safety_zone"] if not (r["machine_id"] == m and r["is_primary"])]
    return m


def _bad_assessment(t):
    r = first(t["machine_state_event"], lambda r: r["safety_assessment"] == "SAFE")
    r["safety_assessment"], r["unsafe_rule_code"] = "UNSAFE", "R-U1"
    return r["machine_state_event_id"]


def _illegal_transition(t):
    """Make an event jump STOPPED -> RUNNING (skipping IDLE) in an otherwise consistent record."""
    r = first(t["machine_state_event"], lambda r: r["previous_state"] == "IDLE" and r["machine_state"] == "RUNNING")
    prev = first(t["machine_state_event"], lambda p: p["machine_id"] == r["machine_id"]
                 and p["sequence_no"] == r["sequence_no"] - 1)
    prev["machine_state"] = "STOPPED"
    r["previous_state"] = "STOPPED"
    return r["machine_state_event_id"]


TAMPERS = [
    ("V01", _dup_plant), ("V02", _bad_fk), ("V03", _bad_enum), ("V04", _bad_id), ("V05", _bad_time),
    ("V06", _bad_previous_state), ("V06", _illegal_transition), ("V07", _bad_summary), ("V08", _ack_before),
    ("V09", _bad_deadline), ("V10", _bad_ack_delay), ("V11", _remove_trigger), ("V11", _wrong_trigger_rule),
    ("V12", _close_open_incident), ("V13", _unverify_closed_action), ("V14", _duplicate_ack),
    ("V15", _flip_ack_status), ("V16", _drop_primary_zone), ("V17", _bad_assessment),
]


@pytest.mark.parametrize("vid,mutate", TAMPERS, ids=[f"{v}-{m.__name__}" for v, m in TAMPERS])
def test_each_validation_detects_its_defect(pair, vid, mutate):
    tables = clone(pair[0].tables)
    rid = mutate(tables)
    report = validate(tables)
    check = next(c for c in report.checks if c.check_id == vid)
    assert not check.passed, f"{vid} did not fail"
    assert rid in check.failed_ids or any(rid in f.detail for f in check.failures), (vid, rid, check.failed_ids[:5])
    assert all(f.detail for f in check.failures)                     # every failure has an error description


def test_unrelated_validations_stay_green_for_a_single_defect(pair):
    tables = clone(pair[0].tables)
    _bad_time(tables)                                                # one bad timestamp
    failed = set(validate(tables).summary()["failed_validation_ids"])
    assert "V05" in failed and failed <= {"V05", "V09"} and not failed & {"V01", "V02", "V03", "V04", "V06"}


# ---------------------------------------------------------------- report content
def test_report_content_and_serialisation(final_report):
    d = final_report.to_dict()
    json.dumps(d)                                                    # fully JSON-serialisable
    s = d["summary"]
    assert {"total_records_checked", "validations_passed", "validations_failed", "anomalies_detected"} <= set(s)
    failed = [c for c in d["checks"] if c["status"] == "FAILED"]
    assert failed and all(c["affected_record_ids"] and c["errors"] and c["failure_count"] for c in failed)
    md = final_report.to_markdown("Test")
    for needle in ("Total records checked", "Validations passed", "Validations failed", "Anomalies detected",
                   "Errors and affected records", "V07", "Records per table"):
        assert needle in md
    assert md.count("**FAILED**") == 5


def test_validation_is_deterministic(pair):
    a, b = validate(pair[1].tables), validate(pair[1].tables)
    assert a.to_dict() == b.to_dict()


def test_validation_never_reads_ground_truth():
    for mod in (checks_module, framework_module, replay_module, validator_module):
        src = inspect.getsource(mod).lower()
        for forbidden in ("ground_truth", "groundtruth", "data_generator", "gt."):
            assert forbidden not in src, (mod.__name__, forbidden)


# ---------------------------------------------------------------- against PostgreSQL
def test_validation_of_the_database_matches_in_memory_result(pair, final_report, engine, tmp_path):
    write_exports(pair[1].tables, tmp_path / "out")
    write_ground_truth(pair[1].ground_truth, tmp_path / "gt")
    load_dataset(engine, tmp_path / "out" / "csv", truncate=True)
    load_ground_truth(engine, tmp_path / "gt" / "ground_truth.csv", truncate=True)
    with Session(engine) as db:
        report = run_validation(db)
    assert report.summary() == final_report.summary()
    assert {(c.check_id, tuple(c.failed_ids)) for c in report.checks} == {(c.check_id, tuple(c.failed_ids)) for c in final_report.checks}
