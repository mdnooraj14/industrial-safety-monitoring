"""Milestone 8 tests: write endpoints enforce the business rules; errors, atomicity, append-only."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.validation.validator import validate
from tests.factories import T0, build_graph
from tests.helpers import read_tables

API = "/api/v1"
DOOR = "ILK-P01-M01-01"


def at(minutes=0.0, seconds=0.0, hours=0.0):
    return (T0 + timedelta(minutes=minutes, seconds=seconds, hours=hours)).isoformat()


@pytest.fixture()
def api(client, session):
    build_graph(session)
    session.commit()
    return client


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.json()
    return r.json()["error"]


def count(engine, table):
    with engine.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {table}")).scalar()


# ============================================================ master data
PLANT = dict(plant_id="P02", plant_name="Stuttgart", city="Stuttgart", country_code="DE", timezone="Europe/Berlin",
             industry_type="METAL_FABRICATION", effective_from="2025-01-01")
MACHINE = dict(machine_id="P02-M01", plant_id="P02", machine_name="Mill 1", machine_type="CNC_MILL", criticality_class="B",
               commissioned_on="2020-01-01", effective_from="2025-01-01")


def test_create_plant_machine_zone_interlock_definition_shift(api):
    r = api.post(f"{API}/plants", json=PLANT)
    assert r.status_code == 201 and r.json()["lifecycle_status"] == "ACTIVE"
    assert api.get(f"{API}/plants/P02").json()["plant_name"] == "Stuttgart"
    err(api.post(f"{API}/plants", json=PLANT), 409, "DUPLICATE")
    bad = api.post(f"{API}/plants", json={**PLANT, "plant_id": "X1", "industry_type": "MINING", "effective_to": "2024-01-01"})
    e = err(bad, 422, "VALIDATION_ERROR")
    assert {d["field"] for d in e["details"]} >= {"plant_id", "industry_type"}
    # machines
    assert api.post(f"{API}/machines", json=MACHINE).status_code == 201
    err(api.post(f"{API}/machines", json={**MACHINE, "machine_id": "P09-M01", "plant_id": "P09"}), 404, "NOT_FOUND")
    err(api.post(f"{API}/machines", json={**MACHINE, "machine_id": "P01-M05"}), 422, "VALIDATION_ERROR")      # id/plant mismatch
    err(api.post(f"{API}/machines", json={**MACHINE, "machine_id": "P02-M02", "commissioned_on": "2026-01-01"}), 422, "VALIDATION_ERROR")
    # zones (links machines, first zone becomes primary)
    zone = dict(safety_zone_id="SAFE-P02-001", plant_id="P02", zone_name="Cell A", zone_type="GUARDED_CELL", risk_level="HIGH",
                effective_from="2025-01-01", machine_ids=["P02-M01"])
    assert api.post(f"{API}/safety-zones", json=zone).status_code == 201
    assert api.post(f"{API}/safety-zones", json={**zone, "safety_zone_id": "SAFE-P02-002", "zone_name": "Cell B"}).status_code == 201
    links = {l["safety_zone_id"]: l["is_primary"] for l in api.get(f"{API}/safety-zones/SAFE-P02-001/machines").json()}
    assert links == {"SAFE-P02-001": True} or links["SAFE-P02-001"] is True
    assert api.get(f"{API}/safety-zones/SAFE-P02-002/machines").json()[0]["is_primary"] is False
    err(api.post(f"{API}/safety-zones", json={**zone, "safety_zone_id": "SAFE-P02-003", "zone_name": "C", "machine_ids": ["P01-M01"]}),
        422, "PLANT_MISMATCH")
    err(api.post(f"{API}/safety-zones", json={**zone, "safety_zone_id": "SAFE-P02-004", "zone_name": "D", "machine_ids": ["NOPE"]}),
        404, "NOT_FOUND")
    assert api.get(f"{API}/safety-zones/SAFE-P02-004").status_code == 404                  # failed create left nothing behind
    # interlocks
    il = dict(interlock_id="ILK-P02-M01-01", machine_id="P02-M01", safety_zone_id="SAFE-P02-001", interlock_type="DOOR_SWITCH",
              is_required=True, bypass_allowed=False, effective_from="2025-01-01")
    assert api.post(f"{API}/interlocks", json=il).status_code == 201
    err(api.post(f"{API}/interlocks", json=il), 409, "DUPLICATE")
    err(api.post(f"{API}/interlocks", json={**il, "interlock_id": "ILK-P02-M01-02", "safety_zone_id": "SAFE-P01-001"}), 422, "ZONE_NOT_LINKED")
    err(api.post(f"{API}/interlocks", json={**il, "interlock_id": "ILK-P02-M01-03", "interlock_type": "E_STOP", "bypass_allowed": True}),
        422, "VALIDATION_ERROR")
    # alarm definitions
    d = dict(alarm_definition_id="ADEF-0002", alarm_code="HYD-PRESSURE-HIGH", alarm_name="Hydraulic pressure high",
             machine_type="HYDRAULIC_PRESS", trigger_type="PROCESS_LIMIT", default_severity="LOW", is_safety_related=False,
             effective_from="2025-01-01")
    assert api.post(f"{API}/alarm-definitions", json=d).status_code == 201
    err(api.post(f"{API}/alarm-definitions", json={**d, "alarm_definition_id": "ADEF-0003"}), 409, "DUPLICATE")       # same code
    err(api.post(f"{API}/alarm-definitions", json={**d, "alarm_definition_id": "ADEF-0003", "alarm_code": "X",
                                                   "trigger_type": "INTERLOCK_FAULT"}), 422, "VALIDATION_ERROR")
    # operator shifts
    sh = dict(shift_id="SHF-P01-20261003-B-01", plant_id="P01", shift_date="2026-10-03", shift_code="B",
              shift_start=at(hours=6), shift_end=at(hours=14), employee_id="EMP-10002", operator_name="Ravi Menon",
              machine_ids=["P01-M01"])
    r = api.post(f"{API}/operator-shifts", json=sh)
    assert r.status_code == 201 and r.json()["machine_ids"] == ["P01-M01"]
    assert api.get(f"{API}/operator-shifts/SHF-P01-20261003-B-01").json()["machine_ids"] == ["P01-M01"]
    err(api.post(f"{API}/operator-shifts", json={**sh, "shift_id": "SHF-P01-20261003-B-02", "machine_ids": ["P02-M01"]}), 422, "PLANT_MISMATCH")
    err(api.post(f"{API}/operator-shifts", json={**sh, "shift_id": "SHF-P01-20261003-B-03", "shift_end": at(hours=5)}), 422, "VALIDATION_ERROR")


# ============================================================ machine state events
def state(api, minutes, machine_state, door, statuses=None, seconds=0):
    return api.post(f"{API}/machine-state-events", json=dict(machine_id="P01-M01", event_ts=at(minutes, seconds), machine_state=machine_state,
                                                            door_state=door, interlock_statuses=statuses or {}))


def test_recording_machine_state_derives_the_assessment(api, engine):
    r = state(api, 1, "RUNNING", "CLOSED", {DOOR: "ENGAGED"})                           # door closes again after the graph's unsafe event
    body = r.json()
    assert r.status_code == 201 and body["sequence_no"] == 3 and body["previous_state"] == "RUNNING"
    assert (body["safety_assessment"], body["unsafe_rule_code"]) == ("SAFE", None)
    assert [(e["interlock_id"], e["interlock_status"], e["door_state_observed"]) for e in body["interlock_events"]] == [(DOOR, "ENGAGED", "CLOSED")]
    assert body["shift_id"] == "SHF-P01-20261003-A-01"
    unsafe = state(api, 2, "RUNNING", "OPEN", {DOOR: "DISENGAGED"}).json()
    assert (unsafe["safety_assessment"], unsafe["unsafe_rule_code"], unsafe["interlock_status_summary"]) == ("UNSAFE", "R-U1", "NONE_ENGAGED")
    impossible = state(api, 3, "RUNNING", "OPEN", {DOOR: "ENGAGED"}).json()          # door open but its interlock says engaged
    assert (impossible["safety_assessment"], impossible["unsafe_rule_code"]) == ("INVALID", "R-I1")
    got = api.get(f"{API}/machine-state-events/{impossible['machine_state_event_id']}").json()
    assert got["safety_assessment"] == "INVALID" and got["interlock_events"]


def test_rejected_state_readings_write_nothing(api, engine):
    before = count(engine, "machine_state_event"), count(engine, "interlock_state_event")
    err(state(api, -30, "IDLE", "CLOSED"), 422, "TIMESTAMP_ORDER")
    err(state(api, 5, "RUNNING", "CLOSED", {"ILK-X": "ENGAGED"}), 422, "UNKNOWN_INTERLOCK")
    err(state(api, 5, "RUNNING", "NOT_APPLICABLE"), 422, "DOOR_STATE_MISMATCH")
    err(state(api, 5, "RUNNING", "OPEN"), 422, "NO_CHANGE")
    err(api.post(f"{API}/machine-state-events", json=dict(machine_id="P01-M99", event_ts=at(5), machine_state="IDLE", door_state="CLOSED")), 404, "NOT_FOUND")
    err(api.post(f"{API}/machine-state-events", json=dict(machine_id="P01-M01", event_ts=at(5), machine_state="FLYING", door_state="CLOSED")), 422, "VALIDATION_ERROR")
    assert (count(engine, "machine_state_event"), count(engine, "interlock_state_event")) == before


# ============================================================ alarms and acknowledgements
def alarm(api, definition, minutes=0.0, seconds=0.0, **extra):
    return api.post(f"{API}/alarm-events", json=dict(machine_id="P01-M01", alarm_definition_id=definition,
                                                    raised_at=at(minutes, seconds), **extra))


def add_definition(api, code, n, **kw):
    body = dict(alarm_definition_id=f"ADEF-{n:04d}", alarm_code=code, alarm_name=code.title(), trigger_type="PROCESS_LIMIT",
                default_severity="LOW", is_safety_related=False, effective_from="2025-01-01")
    body.update(kw)
    r = api.post(f"{API}/alarm-definitions", json=body)
    assert r.status_code == 201, r.text
    return body["alarm_definition_id"]


def test_raising_alarms_classifies_severity_and_sets_deadline(api, engine):
    r = alarm(api, "ADEF-0001", seconds=30)                    # safety alarm while the machine is UNSAFE -> escalated to CRITICAL
    a = r.json()
    assert r.status_code == 201 and a["alarm_event_id"] == "ALM-P01-M01-000002"
    assert a["severity"] == "CRITICAL" and a["machine_state_event_id"] == "MSE-P01-M01-000002"
    assert a["safety_zone_id"] == "SAFE-P01-001" and a["shift_id"] == "SHF-P01-20261003-A-01" and a["lifecycle_status"] == "ACTIVE"
    assert a["ack_deadline_at"] == at(seconds=30 + 120) and a["ingest_batch_id"] == "BATCH-20261003-01"
    err(alarm(api, "ADEF-0001", seconds=30), 409, "DUPLICATE_ALARM")                          # idempotent retry is refused
    err(alarm(api, "ADEF-0001", minutes=-5), 422, "NO_UNSAFE_STATE")                         # machine was safe five minutes earlier
    proc = add_definition(api, "HYD-PRESSURE-HIGH", 2, machine_type="HYDRAULIC_PRESS")
    safe = alarm(api, proc, minutes=-5).json()                                                  # LOW, machine state STOPPED (safe)
    assert safe["severity"] == "LOW" and safe["ack_deadline_at"] == at(minutes=-5, seconds=7200) and safe["safety_zone_id"] is None
    unsafe = alarm(api, proc, minutes=10).json()                                                # same definition while unsafe -> MEDIUM
    assert unsafe["severity"] == "MEDIUM" and unsafe["ack_deadline_at"] == at(minutes=10, seconds=1800)
    cleared = alarm(api, proc, minutes=20, cleared_at=at(minutes=21)).json()
    assert cleared["lifecycle_status"] == "CLEARED"


def test_alarm_rejections(api, engine):
    deprecated = add_definition(api, "OLD-ALARM", 3, lifecycle_status="DEPRECATED")
    wrong_type = add_definition(api, "MILL-ONLY", 4, machine_type="CNC_MILL")
    proc = add_definition(api, "HYD-PRESSURE-HIGH", 5)
    ilk_fault = add_definition(api, "ESTOP-FAULT", 6, trigger_type="INTERLOCK_FAULT", interlock_type="E_STOP", is_safety_related=True)
    door_fault = add_definition(api, "DOOR-FAULT", 7, trigger_type="INTERLOCK_FAULT", interlock_type="DOOR_SWITCH", is_safety_related=True)
    before = count(engine, "alarm_event")
    err(alarm(api, deprecated, minutes=5), 422, "DEFINITION_INACTIVE")
    err(alarm(api, wrong_type, minutes=5), 422, "DEFINITION_NOT_APPLICABLE")
    err(alarm(api, proc, minutes=5, cleared_at=at(minutes=4)), 422, "CLEARED_BEFORE_RAISED")
    err(alarm(api, ilk_fault, minutes=5), 422, "NO_MATCHING_INTERLOCK")                       # machine has no E_STOP interlock
    err(alarm(api, proc, minutes=5, interlock_id="ILK-NOPE"), 404, "NOT_FOUND")
    err(api.post(f"{API}/alarm-events", json=dict(machine_id="P01-M99", alarm_definition_id=proc, raised_at=at(5))), 404, "NOT_FOUND")
    err(alarm(api, "ADEF-9999", minutes=5), 404, "NOT_FOUND")
    assert count(engine, "alarm_event") == before
    ok = alarm(api, door_fault, minutes=5).json()                                                # interlock chosen from the definition's type
    assert ok["interlock_id"] == DOOR and ok["safety_zone_id"] == "SAFE-P01-001"


def ack(api, alarm_id, minutes=0.0, seconds=0.0, hours=0.0, **extra):
    return api.post(f"{API}/acknowledgements", json=dict(alarm_event_id=alarm_id, acknowledged_at=at(minutes, seconds, hours), **extra))


def test_acknowledgement_rules(api, engine):
    a = alarm(api, "ADEF-0001", seconds=30).json()["alarm_event_id"]                       # CRITICAL, deadline raised+120 s
    r = ack(api, a, seconds=80)
    k = r.json()
    assert r.status_code == 201 and k["acknowledgement_id"] == f"ACK-{a}-01" and k["ack_delay_seconds"] == 50.0
    assert k["ack_status"] == "ON_TIME" and k["acknowledged_by_shift_id"] == "SHF-P01-20261003-A-01"
    err(ack(api, a, seconds=90), 409, "ALREADY_ACKNOWLEDGED")
    proc = add_definition(api, "SLOW-ALARM", 2)
    low = alarm(api, proc, minutes=10).json()["alarm_event_id"]                                # MEDIUM while unsafe, deadline 1800 s
    late = ack(api, low, minutes=10, seconds=2000).json()
    assert late["ack_status"] == "LATE" and late["ack_delay_seconds"] == 2000.0
    fresh = alarm(api, proc, minutes=60).json()["alarm_event_id"]
    before = count(engine, "acknowledgement")
    e = err(ack(api, fresh, minutes=59), 422, "ACK_BEFORE_ALARM")
    assert "earlier" in e["message"]
    err(ack(api, fresh, hours=10), 422, "NO_SHIFT")                                             # no operator on duty that late
    err(ack(api, "ALM-NOPE", minutes=61), 404, "NOT_FOUND")
    err(ack(api, fresh, minutes=61, acknowledged_by_shift_id="SHF-NOPE"), 404, "NOT_FOUND")
    err(api.post(f"{API}/acknowledgements", json=dict(alarm_event_id=fresh, acknowledged_at=at(61), ack_method="TELEPATHY")), 422, "VALIDATION_ERROR")
    assert count(engine, "acknowledgement") == before
    assert ack(api, fresh, minutes=61, acknowledged_by_shift_id="SHF-P01-20261003-A-01", ack_method="MOBILE").status_code == 201


# ============================================================ incidents
def incident(api, mse_id):
    return api.post(f"{API}/incidents", json={"trigger_machine_state_event_id": mse_id})


def test_incident_creation_applies_rule_in_1(api, engine):
    err(incident(api, "MSE-P01-M01-000002"), 409, "INCIDENT_EXISTS")                           # graph already has one for this episode
    err(incident(api, "MSE-P01-M01-000001"), 422, "NOT_UNSAFE")
    err(incident(api, "MSE-P01-M01-999999"), 404, "NOT_FOUND")
    state(api, 1, "RUNNING", "CLOSED", {DOOR: "ENGAGED"})                                       # MSE 3: episode 1 ends
    short = state(api, 60, "RUNNING", "OPEN", {DOOR: "DISENGAGED"}).json()                     # MSE 4 .. closed 5 s later
    state(api, 60, "RUNNING", "CLOSED", {DOOR: "ENGAGED"}, seconds=5)
    alarm(api, "ADEF-0001", minutes=60, seconds=2)
    err(incident(api, short["machine_state_event_id"]), 422, "EPISODE_TOO_SHORT")
    quiet = state(api, 120, "RUNNING", "OPEN", {DOOR: "DISENGAGED"}).json()                    # MSE 6 .. closed after 90 s, no alarm
    state(api, 120, "RUNNING", "CLOSED", {DOOR: "ENGAGED"}, seconds=90)
    err(incident(api, quiet["machine_state_event_id"]), 422, "NO_QUALIFYING_ALARM")
    trig = state(api, 180, "RUNNING", "OPEN", {DOOR: "DISENGAGED"}).json()                     # MSE 8 .. closed after 120 s, alarmed
    state(api, 180, "RUNNING", "CLOSED", {DOOR: "ENGAGED"}, seconds=120)
    a = alarm(api, "ADEF-0001", minutes=180, seconds=3).json()
    assert a["severity"] == "CRITICAL"
    before = count(engine, "incident")
    r = incident(api, trig["machine_state_event_id"])
    d = r.json()
    assert r.status_code == 201 and d["incident"]["incident_id"] == "INC-2026-000002"
    assert (d["incident"]["severity"], d["incident"]["status"], d["incident"]["trigger_rule_code"]) == ("CRITICAL", "OPEN", "R-U1")
    assert d["incident"]["opened_at"] == a["raised_at"] and d["context_complete"] is True
    assert [(x["link_role"], x["alarm"]["alarm_event_id"]) for x in d["alarms"]] == [("TRIGGER", a["alarm_event_id"])]
    assert d["trigger_state"]["machine_state_event_id"] == trig["machine_state_event_id"] and d["shift"]["shift_id"] == "SHF-P01-20261003-A-01"
    assert [h["to_status"] for h in d["status_history"]] == ["OPEN"] and d["status_history"][0]["changed_by_role"] == "SYSTEM"
    assert count(engine, "incident") == before + 1
    err(incident(api, trig["machine_state_event_id"]), 409, "INCIDENT_EXISTS")                  # one incident per unsafe episode
    assert count(engine, "incident") == before + 1


# ============================================================ lifecycle
def move_incident(api, status, minutes, **extra):
    return api.post(f"{API}/incidents/INC-2026-000001/transitions", json=dict(to_status=status, changed_at=at(minutes), **extra))


def move_action(api, aid, status, minutes, **extra):
    return api.post(f"{API}/corrective-actions/{aid}/transitions", json=dict(to_status=status, changed_at=at(minutes), **extra))


A1, A2 = "ACT-INC-2026-000001-01", "ACT-INC-2026-000001-02"


def test_full_incident_and_action_lifecycle_with_every_guard(api, engine):
    # --- incident guards
    err(move_incident(api, "CLOSED", 10), 409, "INVALID_TRANSITION")
    err(move_incident(api, "INVESTIGATING", -60), 422, "TIMESTAMP_ORDER")
    r = move_incident(api, "INVESTIGATING", 60)
    assert r.status_code == 200 and r.json()["status"] == "INVESTIGATING" and r.json()["investigation_started_at"] == at(60)
    err(move_incident(api, "RESOLVED", 61), 422, "ACTIONS_NOT_VERIFIED")
    assert move_incident(api, "ACTION_IN_PROGRESS", 120, note="work started").status_code == 200
    # --- action guards (graph action ACT-...-01 is PLANNED)
    err(move_action(api, A1, "COMPLETED", 130), 409, "INVALID_TRANSITION")
    assert move_action(api, A1, "IN_PROGRESS", 180).json()["started_at"] == at(180)
    assert move_action(api, A1, "COMPLETED", 240).json()["status"] == "COMPLETED"
    err(move_action(api, A1, "CLOSED", 250), 409, "INVALID_TRANSITION")                          # cannot skip verification
    err(move_action(api, A1, "VERIFIED", 250), 422, "VERIFICATION_REQUIRED")
    err(move_action(api, A1, "VERIFIED", 250, verification_result="PASSED", verified_by_id="EMP-20001"), 422, "SELF_VERIFICATION")
    failed = move_action(api, A1, "VERIFIED", 250, verification_result="FAILED", verified_by_id="EMP-30001").json()
    assert (failed["status"], failed["verification_result"], failed["verified_by_id"]) == ("VERIFIED", "FAILED", "EMP-30001")
    e = err(move_action(api, A1, "CLOSED", 260), 422, "NOT_VERIFIED")                            # CA-3
    assert "without a PASSED verification" in e["message"]
    follow = api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json=dict(
        assigned_engineer_id="EMP-20001", assigned_engineer_name="Ravi Menon", action_type="REPLACE_INTERLOCK",
        action_description="Replace the door switch after the failed verification", planned_at=at(300), due_at=at(hours=48)))
    assert follow.status_code == 201 and follow.json()["corrective_action_id"] == A2 and follow.json()["status"] == "PLANNED"
    assert move_action(api, A1, "CANCELLED", 300).json()["status"] == "CANCELLED"                  # superseded, history kept
    err(move_incident(api, "RESOLVED", 310), 422, "ACTIONS_NOT_VERIFIED")                         # live action -02 not verified yet
    move_action(api, A2, "IN_PROGRESS", 360)
    move_action(api, A2, "COMPLETED", 420)
    ver = move_action(api, A2, "VERIFIED", 480, verification_result="PASSED", verified_by_id="EMP-30001").json()
    assert ver["verification_result"] == "PASSED"
    err(move_action(api, A2, "CANCELLED", 490), 422, "NOT_SUPERSEDABLE")                           # a passed verification is not superseded
    assert move_incident(api, "RESOLVED", 540).json()["resolved_at"] == at(540)
    err(move_incident(api, "CLOSED", 550), 422, "ACTIONS_NOT_CLOSED")                              # IN-4: action -02 is VERIFIED, not CLOSED
    assert move_action(api, A2, "CLOSED", 570).json()["closed_at"] == at(570)
    closed = move_incident(api, "CLOSED", 600).json()
    assert closed["status"] == "CLOSED" and closed["closed_at"] == at(600)
    err(api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json=dict(
        assigned_engineer_id="EMP-20001", assigned_engineer_name="x", action_type="INSTALL_GUARD", action_description="late",
        due_at=at(hours=99))), 409, "INCIDENT_CLOSED")
    err(move_incident(api, "OPEN", 700), 409, "INVALID_TRANSITION")
    # --- history is complete and ordered
    d = api.get(f"{API}/incidents/INC-2026-000001").json()
    assert [h["to_status"] for h in d["status_history"]] == ["OPEN", "INVESTIGATING", "ACTION_IN_PROGRESS", "RESOLVED", "CLOSED"]
    assert [h["from_status"] for h in d["status_history"]] == [None, "OPEN", "INVESTIGATING", "ACTION_IN_PROGRESS", "RESOLVED"]
    hist = {a["corrective_action_id"]: [h["to_status"] for h in a["history"]] for a in d["corrective_actions"]}
    assert hist[A1] == ["PLANNED", "IN_PROGRESS", "COMPLETED", "VERIFIED", "CANCELLED"]
    assert hist[A2] == ["PLANNED", "IN_PROGRESS", "COMPLETED", "VERIFIED", "CLOSED"]
    # --- the validation module agrees that API-written history is consistent
    report = validate(read_tables(engine))
    for c in report.checks:
        if c.check_id in ("V12", "V13", "V05"):
            touching = [f for f in c.failures if "000001" in f.record_id and "INC" in f.record_id or f.record_id in (A1, A2)]
            assert not touching, (c.check_id, touching)


def test_action_creation_validation(api, engine):
    body = dict(assigned_engineer_id="EMP-20001", assigned_engineer_name="Ravi Menon", action_type="INSTALL_GUARD",
                action_description="Install a guard", due_at=at(hours=48))
    err(api.post(f"{API}/incidents/INC-NOPE/corrective-actions", json=body), 404, "NOT_FOUND")
    err(api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json={**body, "planned_at": at(hours=-5)}), 422, "TIMESTAMP_ORDER")
    err(api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json={**body, "planned_at": at(hours=50)}), 422, "VALIDATION_ERROR")
    err(api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json={**body, "assigned_engineer_id": "bob"}), 422, "VALIDATION_ERROR")
    err(api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json={**body, "action_type": "MAGIC"}), 422, "VALIDATION_ERROR")
    r = api.post(f"{API}/incidents/INC-2026-000001/corrective-actions", json={**body, "planned_at": at(hours=1)})
    assert r.status_code == 201 and r.json()["corrective_action_id"] == A2


# ============================================================ cross-cutting behaviour
def test_history_has_no_update_or_delete_endpoints(api):
    for path in ("alarm-events/ALM-P01-M01-000001", "acknowledgements/ACK-ALM-P01-M01-000001-01", "machine-state-events/MSE-P01-M01-000001",
                 "incidents/INC-2026-000001", "corrective-actions/ACT-INC-2026-000001-01", "plants/P01"):
        for method in ("delete", "put", "patch"):
            r = getattr(api, method)(f"{API}/{path}")
            assert r.status_code == 405 and r.json()["error"]["code"] == "HTTP_ERROR", (method, path)


def test_unexpected_errors_are_500_envelopes_not_stack_traces(api, monkeypatch):
    import app.services.master_service as master

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(master, "get_or_404", boom)
    r = api.get(f"{API}/plants/P01")
    assert r.status_code == 500 and r.json() == {"error": {"code": "INTERNAL_ERROR", "message": "unexpected server error", "details": None}}
    assert "secret" not in r.text


def test_malformed_json_and_wrong_types_are_422(api):
    r = api.post(f"{API}/plants", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = api.post(f"{API}/machine-state-events", json={"machine_id": 5})
    assert r.status_code == 422 and {d["field"] for d in r.json()["error"]["details"]} >= {"event_ts", "machine_state"}


def test_writes_are_visible_to_analytics_immediately(api):
    before = api.get(f"{API}/analytics/kpis/overview").json()["alarm_events_stored"]
    alarm(api, "ADEF-0001", seconds=30)
    after = api.get(f"{API}/analytics/kpis/overview").json()["alarm_events_stored"]
    assert after == before + 1                                                    # cache is invalidated by new data
