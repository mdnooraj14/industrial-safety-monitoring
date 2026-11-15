"""Milestone 2 tests: relationships, FK integrity, constraints, append-only history, anomaly storability."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Acknowledgement, AlarmEvent, Base, CorrectiveAction, GroundTruthLabel, Incident, IncidentAlarmLink,
    Machine, MachineStateEvent, Plant,
)
from tests.factories import T0, build_graph

EXPECTED_TABLES = {
    "plant", "machine", "safety_zone", "machine_safety_zone", "interlock", "alarm_severity_policy",
    "alarm_definition", "operator_shift", "shift_machine_assignment", "machine_state_event",
    "interlock_state_event", "alarm_event", "acknowledgement", "incident", "incident_alarm_link",
    "incident_status_event", "corrective_action", "corrective_action_status_event",
}


def test_all_tables_exist(engine):
    insp = inspect(engine)
    assert EXPECTED_TABLES <= set(insp.get_table_names())
    assert "ground_truth_label" in insp.get_table_names(schema="gt")
    assert len(EXPECTED_TABLES) + 1 == 19


def test_every_table_has_primary_key(engine):
    insp = inspect(engine)
    for t in EXPECTED_TABLES:
        assert insp.get_pk_constraint(t)["constrained_columns"], t


def test_indexes_present(engine):
    insp = inspect(engine)
    names = {i["name"] for i in insp.get_indexes("alarm_event")}
    assert {"ix_alarm_event_machine_id_raised_at", "ix_alarm_event_severity_raised_at"} <= names


# ---------------- relationships ----------------
def test_one_to_many_and_navigation(session):
    g = build_graph(session)
    session.commit()
    plant = session.get(Plant, "P01")
    assert [m.machine_id for m in plant.machines] == ["P01-M01"]
    machine = session.get(Machine, "P01-M01")
    assert machine.plant is plant
    assert len(machine.interlocks) == 1 and len(machine.state_events) == 2
    assert len(machine.alarm_events) == 1 and len(machine.incidents) == 1


def test_many_to_many_bridges(session):
    build_graph(session)
    session.commit()
    machine = session.get(Machine, "P01-M01")
    assert machine.zone_links[0].safety_zone.safety_zone_id == "SAFE-P01-001"
    incident = session.get(Incident, "INC-2026-000001")
    assert incident.alarm_links[0].alarm_event.alarm_event_id == "ALM-P01-M01-000001"
    assert incident.alarm_links[0].link_role == "TRIGGER"


def test_one_to_one_optional_acknowledgement(session):
    g = build_graph(session)
    session.commit()
    alarm = session.get(AlarmEvent, "ALM-P01-M01-000001")
    assert alarm.acknowledgement.acknowledgement_id == "ACK-ALM-P01-M01-000001-01"
    assert alarm.acknowledgement.alarm_event is alarm
    # second ack for same alarm violates the 0..1 rule
    session.add(Acknowledgement(
        acknowledgement_id="ACK-ALM-P01-M01-000001-02", alarm_event_id=alarm.alarm_event_id,
        acknowledged_by_shift_id=g["shift"].shift_id, acknowledged_at=T0 + timedelta(seconds=60),
        ack_method="HMI", ack_delay_seconds=Decimal("60.0"), ack_status="ON_TIME"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_status_histories_ordered(session):
    build_graph(session)
    session.commit()
    inc = session.get(Incident, "INC-2026-000001")
    assert [e.sequence_no for e in inc.status_events] == [1]
    assert inc.corrective_actions[0].status_events[0].to_status == "PLANNED"


# ---------------- foreign-key integrity ----------------
@pytest.mark.parametrize("make", [
    lambda g: AlarmEvent(alarm_event_id="ALM-P01-M99-000001", machine_id="P01-M99", alarm_definition_id="ADEF-0001",
                         severity="LOW", raised_at=T0, ack_deadline_at=T0 + timedelta(hours=2),
                         lifecycle_status="ACTIVE", ingest_batch_id="B"),
    lambda g: Incident(incident_id="INC-2026-000002", machine_id="NOPE", severity="HIGH", status="OPEN",
                       title="x", opened_at=T0),
    lambda g: CorrectiveAction(corrective_action_id="ACT-INC-2026-000099-01", incident_id="INC-2026-000099",
                               assigned_engineer_id="EMP-20001", assigned_engineer_name="x",
                               action_type="INSTALL_GUARD", action_description="x", status="PLANNED",
                               planned_at=T0, due_at=T0 + timedelta(days=1)),
    lambda g: IncidentAlarmLink(incident_id="INC-2026-000001", alarm_event_id="ALM-P01-M01-999999",
                                link_role="CONTRIBUTING", linked_at=T0),
    lambda g: MachineStateEvent(machine_state_event_id="MSE-P01-M01-000003", machine_id="P01-M01", sequence_no=3,
                                event_ts=T0, previous_state="RUNNING", machine_state="IDLE", door_state="CLOSED",
                                interlock_status_summary="ALL_ENGAGED", safety_assessment="SAFE",
                                shift_id="SHF-NOT-REAL"),
])
def test_foreign_keys_rejected(session, make):
    g = build_graph(session)
    session.commit()
    session.add(make(g))
    with pytest.raises(IntegrityError):
        session.commit()


def test_restrict_delete_of_parent(session):
    build_graph(session)
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(text("DELETE FROM machine WHERE machine_id='P01-M01'"))


# ---------------- unique / check constraints ----------------
def test_duplicate_state_sequence_rejected(session):
    build_graph(session)
    session.commit()
    session.add(MachineStateEvent(
        machine_state_event_id="MSE-P01-M01-000009", machine_id="P01-M01", sequence_no=2, event_ts=T0,
        previous_state="STOPPED", machine_state="IDLE", door_state="CLOSED",
        interlock_status_summary="ALL_ENGAGED", safety_assessment="SAFE"))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("field,value", [
    ("plant_id", "P1X"), ("lifecycle_status", "BROKEN"), ("industry_type", "MINING"),
    ("country_code", "in"), ("plant_name", "  "),
])
def test_plant_check_constraints(session, field, value):
    base = dict(plant_id="P02", plant_name="Plant Two", city="X", country_code="IN", timezone="UTC",
                industry_type="CHEMICAL", lifecycle_status="ACTIVE", effective_from=T0.date())
    base[field] = value
    session.add(Plant(**base))
    with pytest.raises(IntegrityError):
        session.commit()


def test_effective_range_check(session):
    session.add(Plant(plant_id="P03", plant_name="Back", city="X", country_code="IN", timezone="UTC",
                      industry_type="CHEMICAL", lifecycle_status="ACTIVE", effective_from=T0.date(),
                      effective_to=T0.date() - timedelta(days=1)))
    with pytest.raises(IntegrityError):
        session.commit()


def test_id_must_embed_parent(session):
    g = build_graph(session)
    session.commit()
    session.add(AlarmEvent(alarm_event_id="ALM-P02-M01-000001", machine_id="P01-M01", alarm_definition_id="ADEF-0001",
                           severity="LOW", raised_at=T0, ack_deadline_at=T0 + timedelta(hours=2),
                           lifecycle_status="ACTIVE", ingest_batch_id="B"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_alarm_timestamp_and_status_checks(session):
    build_graph(session)
    session.commit()
    bad = dict(machine_id="P01-M01", alarm_definition_id="ADEF-0001", severity="LOW", ingest_batch_id="B")
    session.add(AlarmEvent(alarm_event_id="ALM-P01-M01-000002", raised_at=T0, cleared_at=T0 - timedelta(seconds=1),
                           ack_deadline_at=T0 + timedelta(hours=2), lifecycle_status="CLEARED", **bad))
    with pytest.raises(IntegrityError):
        session.commit()


def test_estop_cannot_allow_bypass(session):
    from app.models import Interlock
    g = build_graph(session)
    session.commit()
    session.add(Interlock(interlock_id="ILK-P01-M01-02", machine_id="P01-M01", interlock_type="E_STOP",
                          is_required=True, bypass_allowed=True, lifecycle_status="ACTIVE", effective_from=T0.date()))
    with pytest.raises(IntegrityError):
        session.commit()


def test_safe_state_has_no_rule_code(session):
    build_graph(session)
    session.commit()
    session.add(MachineStateEvent(
        machine_state_event_id="MSE-P01-M01-000003", machine_id="P01-M01", sequence_no=3, event_ts=T0,
        previous_state="RUNNING", machine_state="IDLE", door_state="CLOSED", interlock_status_summary="ALL_ENGAGED",
        safety_assessment="SAFE", unsafe_rule_code="R-U1"))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------- append-only history ----------------
@pytest.mark.parametrize("sql", [
    "UPDATE machine_state_event SET machine_state='FAULT'",
    "DELETE FROM machine_state_event",
    "UPDATE interlock_state_event SET interlock_status='FAULT'",
    "DELETE FROM interlock_state_event",
    "UPDATE alarm_event SET severity='LOW'",
    "DELETE FROM alarm_event",
    "UPDATE acknowledgement SET ack_method='SCADA'",
    "DELETE FROM acknowledgement",
    "UPDATE incident_status_event SET to_status='CLOSED'",
    "DELETE FROM incident_status_event",
    "UPDATE corrective_action_status_event SET to_status='CLOSED'",
    "DELETE FROM corrective_action_status_event",
])
def test_history_tables_are_append_only(session, sql):
    build_graph(session)
    session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text(sql))
    session.rollback()


def test_incident_current_state_may_be_updated(session):
    build_graph(session)
    session.commit()
    session.execute(text("UPDATE incident SET status='INVESTIGATING', investigation_started_at=opened_at"))
    session.commit()


# ---------------- injected anomalies must be storable (and are therefore NOT blocked by the DB) ----------------
def test_anomaly_ack_before_alarm_storable(session):
    g = build_graph(session)
    session.commit()
    a = g["alarm"]
    session.add(AlarmEvent(alarm_event_id="ALM-P01-M01-000002", machine_id="P01-M01", alarm_definition_id="ADEF-0001",
                           severity="LOW", raised_at=T0, ack_deadline_at=T0 + timedelta(hours=2),
                           lifecycle_status="ACTIVE", ingest_batch_id="B"))
    session.flush()
    session.add(Acknowledgement(acknowledgement_id="ACK-ALM-P01-M01-000002-01", alarm_event_id="ALM-P01-M01-000002",
                                acknowledged_by_shift_id=g["shift"].shift_id, acknowledged_at=T0 - timedelta(seconds=30),
                                ack_method="HMI", ack_delay_seconds=Decimal("-30.0"), ack_status="ON_TIME"))
    session.commit()


def test_anomaly_incident_without_context_storable(session):
    build_graph(session)
    session.commit()
    session.add(Incident(incident_id="INC-2026-000002", machine_id="P01-M01", severity="HIGH", status="OPEN",
                         title="No context", opened_at=T0))
    session.commit()


def test_anomaly_action_closed_without_verification_storable(session):
    build_graph(session)
    session.commit()
    session.execute(text(
        "UPDATE corrective_action SET status='CLOSED', started_at=planned_at, completed_at=planned_at, closed_at=planned_at"))
    session.commit()


def test_replay_duplicates_storable(session):
    g = build_graph(session)
    session.commit()
    a = g["alarm"]
    session.add(AlarmEvent(alarm_event_id="ALM-P01-M01-000002", machine_id=a.machine_id,
                           alarm_definition_id=a.alarm_definition_id, severity=a.severity, raised_at=a.raised_at,
                           ack_deadline_at=a.ack_deadline_at, lifecycle_status="ACTIVE",
                           ingest_batch_id="BATCH-REPLAY-01"))
    session.commit()


# ---------------- ground truth separation ----------------
def test_ground_truth_is_separate_and_unlinked(engine):
    insp = inspect(engine)
    assert insp.get_foreign_keys("ground_truth_label", schema="gt") == []
    for table in EXPECTED_TABLES:
        cols = {c["name"] for c in insp.get_columns(table)}
        assert not cols & {"issue_type", "ground_truth", "is_anomaly"}, table
        for fk in insp.get_foreign_keys(table):
            assert fk.get("referred_schema") != "gt"


def test_ground_truth_label_checks(session):
    ok = dict(label_id="GT-000001", label_class="INJECTED_ANOMALY", issue_type="ACK_BEFORE_ALARM",
              record_table="acknowledgement", record_id="ACK-X-01", expected_decision="FLAG", description="d")
    session.add(GroundTruthLabel(**ok))
    session.commit()
    session.add(GroundTruthLabel(**{**ok, "label_id": "GT-000002", "label_class": "BASELINE_SCENARIO"}))
    with pytest.raises(IntegrityError):
        session.commit()
