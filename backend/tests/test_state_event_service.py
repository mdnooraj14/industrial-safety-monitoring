"""Machine-state event processing against PostgreSQL, plus detector accuracy against the generator's planted scenarios."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.business_rules.alarm_patterns import detect_floods, detect_nuisance_clusters
from app.core.errors import NotFoundError, RuleViolationError
from app.models import (
    Interlock, InterlockStateEvent, Machine, MachineSafetyZone, MachineStateEvent, OperatorShift, Plant, SafetyZone,
    ShiftMachineAssignment,
)
from app.services.state_event_service import record_machine_state
from data_generator.config import STARTER
from data_generator.export import TABLE_ORDER, write_exports
from data_generator.generate import generate
from data_generator.loader import load_dataset

T0 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)
D0 = date(2025, 1, 1)
DOOR, ESTOP = "ILK-P01-M01-01", "ILK-P01-M01-02"


@pytest.fixture()
def machine(session):
    session.add(Plant(plant_id="P01", plant_name="Pune Works", city="Pune", country_code="IN", timezone="Asia/Kolkata",
                      industry_type="AUTOMOTIVE", lifecycle_status="ACTIVE", effective_from=D0))
    session.flush()
    session.add_all([
        Machine(machine_id="P01-M01", plant_id="P01", machine_name="Press 1", machine_type="HYDRAULIC_PRESS",
                criticality_class="A", lifecycle_status="ACTIVE", commissioned_on=D0, effective_from=D0),
        SafetyZone(safety_zone_id="SAFE-P01-001", plant_id="P01", zone_name="Z", zone_type="GUARDED_CELL",
                   risk_level="HIGH", lifecycle_status="ACTIVE", effective_from=D0)])
    session.flush()
    session.add_all([
        Interlock(interlock_id=DOOR, machine_id="P01-M01", safety_zone_id="SAFE-P01-001", interlock_type="DOOR_SWITCH",
                  is_required=True, bypass_allowed=False, lifecycle_status="ACTIVE", effective_from=D0),
        Interlock(interlock_id=ESTOP, machine_id="P01-M01", interlock_type="E_STOP", is_required=True,
                  bypass_allowed=False, lifecycle_status="ACTIVE", effective_from=D0),
        OperatorShift(shift_id="SHF-P01-20261003-A-01", plant_id="P01", shift_date=date(2026, 10, 3), shift_code="A",
                      shift_start=T0 - timedelta(hours=2), shift_end=T0 + timedelta(hours=6), employee_id="EMP-10001",
                      operator_name="Asha Rao", operator_role="OPERATOR", lifecycle_status="COMPLETED")])
    session.flush()
    session.add(ShiftMachineAssignment(shift_id="SHF-P01-20261003-A-01", machine_id="P01-M01"))
    session.flush()
    return "P01-M01"


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_first_event_and_normal_sequence(session, machine):
    e1 = record_machine_state(session, machine, at(0), "STOPPED", "CLOSED", reason="initial")
    assert (e1.machine_state_event_id, e1.sequence_no, e1.previous_state) == ("MSE-P01-M01-000001", 1, None)
    assert (e1.safety_assessment, e1.interlock_status_summary, e1.shift_id) == ("SAFE", "ALL_ENGAGED", "SHF-P01-20261003-A-01")
    assert session.scalar(select(func.count()).select_from(InterlockStateEvent)) == 2   # initial status of each interlock
    e2 = record_machine_state(session, machine, at(5), "IDLE", "CLOSED")
    e3 = record_machine_state(session, machine, at(10), "RUNNING", "CLOSED")
    assert [e2.sequence_no, e3.sequence_no, e3.previous_state] == [2, 3, "IDLE"] and e3.safety_assessment == "SAFE"
    assert session.scalar(select(func.count()).select_from(InterlockStateEvent)) == 2   # nothing changed


def _running(session, machine):
    record_machine_state(session, machine, at(0), "STOPPED", "CLOSED")
    record_machine_state(session, machine, at(1), "IDLE", "CLOSED")
    return record_machine_state(session, machine, at(2), "RUNNING", "CLOSED")


def test_unsafe_door_open_while_running_is_flagged_and_history_kept(session, machine):
    running = _running(session, machine)
    snapshot = (running.machine_state, running.safety_assessment, running.event_ts)
    unsafe = record_machine_state(session, machine, at(3), "RUNNING", "OPEN", {DOOR: "DISENGAGED"}, "door opened")
    assert (unsafe.safety_assessment, unsafe.unsafe_rule_code, unsafe.interlock_status_summary) == ("UNSAFE", "R-U1", "PARTIAL")
    ise = session.scalars(select(InterlockStateEvent).where(InterlockStateEvent.machine_state_event_id ==
                                                           unsafe.machine_state_event_id)).all()
    assert [(i.interlock_id, i.interlock_status, i.door_state_observed, i.sequence_no) for i in ise] == [(DOOR, "DISENGAGED", "OPEN", 2)]
    # recovery is a NEW event, the unsafe one is untouched
    ok = record_machine_state(session, machine, at(4), "RUNNING", "CLOSED", {DOOR: "ENGAGED"})
    assert ok.safety_assessment == "SAFE" and ok.sequence_no == unsafe.sequence_no + 1
    session.refresh(running)
    assert (running.machine_state, running.safety_assessment, running.event_ts) == snapshot
    session.refresh(unsafe)
    assert unsafe.safety_assessment == "UNSAFE"


def test_required_interlock_inactive_and_bypass_blocked(session, machine):
    _running(session, machine)
    inactive = record_machine_state(session, machine, at(3), "RUNNING", "CLOSED", {ESTOP: "FAULT"})
    assert (inactive.safety_assessment, inactive.unsafe_rule_code) == ("UNSAFE", "R-U2")
    bypass = record_machine_state(session, machine, at(4), "RUNNING", "CLOSED", {ESTOP: "BYPASSED"})   # E-stop may not be bypassed
    assert (bypass.safety_assessment, bypass.unsafe_rule_code) == ("INVALID", "R-I4")


def test_impossible_interlock_state_is_stored_but_flagged(session, machine):
    _running(session, machine)
    bad = record_machine_state(session, machine, at(3), "RUNNING", "OPEN", {DOOR: "ENGAGED"})
    assert (bad.safety_assessment, bad.unsafe_rule_code) == ("INVALID", "R-I1")
    assert session.get(MachineStateEvent, bad.machine_state_event_id) is not None


def test_illegal_transition_is_stored_but_flagged(session, machine):
    record_machine_state(session, machine, at(0), "STOPPED", "CLOSED")
    bad = record_machine_state(session, machine, at(1), "RUNNING", "CLOSED")       # STOPPED -> RUNNING skips IDLE
    assert (bad.safety_assessment, bad.unsafe_rule_code, bad.previous_state) == ("INVALID", "R-I3", "STOPPED")


def test_rejections_write_nothing(session, machine):
    record_machine_state(session, machine, at(10), "STOPPED", "CLOSED")
    before = session.scalar(select(func.count()).select_from(MachineStateEvent))
    cases = [
        (dict(machine_id="P01-M99", event_ts=at(11), machine_state="IDLE", door_state="CLOSED"), NotFoundError),
        (dict(machine_id=machine, event_ts=at(9), machine_state="IDLE", door_state="CLOSED"), RuleViolationError),
        (dict(machine_id=machine, event_ts=at(11), machine_state="STOPPED", door_state="CLOSED"), RuleViolationError),
        (dict(machine_id=machine, event_ts=at(11), machine_state="IDLE", door_state="NOT_APPLICABLE"), RuleViolationError),
        (dict(machine_id=machine, event_ts=at(11), machine_state="IDLE", door_state="CLOSED",
              interlock_statuses={"ILK-OTHER": "ENGAGED"}), RuleViolationError),
    ]
    for kwargs, exc in cases:
        with pytest.raises(exc):
            record_machine_state(session, **kwargs)
    assert session.scalar(select(func.count()).select_from(MachineStateEvent)) == before


def test_rejection_codes_are_exposed(session, machine):
    record_machine_state(session, machine, at(10), "STOPPED", "CLOSED")
    with pytest.raises(RuleViolationError) as e:
        record_machine_state(session, machine, at(5), "IDLE", "CLOSED")
    assert e.value.code == "TIMESTAMP_ORDER" and e.value.status_code == 422


def test_service_reproduces_generator_timeline(session, engine, tmp_path):
    """Replay generated machine timelines through the service: assessments, summaries and interlock events must match."""
    ds = generate(STARTER)
    write_exports(ds.tables, tmp_path)
    load_dataset(engine, tmp_path / "csv", truncate=True)
    session.execute(text("TRUNCATE interlock_state_event, machine_state_event CASCADE"))
    session.commit()
    ise_by_mse = defaultdict(list)
    for r in ds.tables["interlock_state_event"]:
        ise_by_mse[r["machine_state_event_id"]].append(r)
    replayed = 0
    for mid in [m["machine_id"] for m in ds.tables["machine"]][:3]:
        for r in sorted((x for x in ds.tables["machine_state_event"] if x["machine_id"] == mid), key=lambda x: x["sequence_no"]):
            changes = {i["interlock_id"]: i["interlock_status"] for i in ise_by_mse[r["machine_state_event_id"]]}
            got = record_machine_state(session, mid, r["event_ts"], r["machine_state"], r["door_state"], changes,
                                       r["transition_reason"])
            assert (got.machine_state_event_id, got.sequence_no, got.previous_state, got.interlock_status_summary,
                    got.safety_assessment, got.unsafe_rule_code, got.shift_id) == (
                r["machine_state_event_id"], r["sequence_no"], r["previous_state"], r["interlock_status_summary"],
                r["safety_assessment"], r["unsafe_rule_code"], r["shift_id"])
            replayed += 1
    got_ise = {i.interlock_state_event_id for i in session.scalars(select(InterlockStateEvent))}
    expected_ise = {i["interlock_state_event_id"] for m in session.scalars(select(MachineStateEvent))
                    for i in ise_by_mse[m.machine_state_event_id]}
    assert got_ise == expected_ise and replayed > 500


# ---------------------------------------------------------------- detectors vs planted baseline scenarios
def _score(found: set, planted: set):
    tp = len(found & planted)
    return tp / len(found), tp / len(planted)


def test_flood_and_nuisance_detectors_find_planted_scenarios():
    ds = generate(STARTER)
    alarms = ds.tables["alarm_event"]
    planted = defaultdict(set)
    for r in ds.ground_truth:
        planted[r["issue_type"]].add(r["record_id"])
    p, rec = _score({a for e in detect_floods(alarms) for a in e.alarm_ids}, planted["ALARM_FLOOD"])
    assert rec >= 0.98 and p >= 0.85, (p, rec)
    p, rec = _score({a for c in detect_nuisance_clusters(alarms) for a in c.alarm_ids}, planted["NUISANCE_CLUSTER"])
    assert rec >= 0.98 and p >= 0.85, (p, rec)


def test_detectors_do_not_need_ground_truth_fields():
    """Detector inputs contain no label/scenario information."""
    ds = generate(STARTER)
    assert not set(ds.tables["alarm_event"][0]) & {"issue_type", "scenario", "label_id", "_scenario", "ground_truth"}
