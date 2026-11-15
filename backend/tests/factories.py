"""Minimal valid object graph used by DB tests (hand-written, independent of the data generator)."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Acknowledgement, AlarmDefinition, AlarmEvent, AlarmSeverityPolicy, CorrectiveAction,
    CorrectiveActionStatusEvent, Incident, IncidentAlarmLink, IncidentStatusEvent, Interlock,
    InterlockStateEvent, Machine, MachineSafetyZone, MachineStateEvent, OperatorShift, Plant,
    SafetyZone, ShiftMachineAssignment,
)

T0 = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)
D0 = date(2026, 1, 1)

POLICY = {"CRITICAL": (1, 120, 300), "HIGH": (2, 600, 1800), "MEDIUM": (3, 1800, 5400), "LOW": (4, 7200, 14400)}


def build_graph(s: Session) -> dict:
    """Insert one of everything. Returns dict of the created objects."""
    for sev, (rank, dl, esc) in POLICY.items():
        s.add(AlarmSeverityPolicy(severity=sev, priority_rank=rank, ack_deadline_seconds=dl,
                                  escalation_after_seconds=esc, effective_from=D0))
    plant = Plant(plant_id="P01", plant_name="Pune Works", city="Pune", country_code="IN",
                  timezone="Asia/Kolkata", industry_type="AUTOMOTIVE", lifecycle_status="ACTIVE", effective_from=D0)
    s.add(plant)
    s.flush()
    machine = Machine(machine_id="P01-M01", plant_id="P01", machine_name="Press 1", machine_type="HYDRAULIC_PRESS",
                      criticality_class="A", lifecycle_status="ACTIVE", commissioned_on=D0, effective_from=D0)
    zone = SafetyZone(safety_zone_id="SAFE-P01-001", plant_id="P01", zone_name="Press Cell", zone_type="GUARDED_CELL",
                      risk_level="HIGH", lifecycle_status="ACTIVE", effective_from=D0)
    s.add_all([machine, zone])
    s.flush()
    link = MachineSafetyZone(machine_id="P01-M01", safety_zone_id="SAFE-P01-001", effective_from=D0, is_primary=True)
    interlock = Interlock(interlock_id="ILK-P01-M01-01", machine_id="P01-M01", safety_zone_id="SAFE-P01-001",
                          interlock_type="DOOR_SWITCH", is_required=True, bypass_allowed=False,
                          lifecycle_status="ACTIVE", effective_from=D0)
    definition = AlarmDefinition(alarm_definition_id="ADEF-0001", alarm_code="DOOR-OPEN-RUN",
                                 alarm_name="Door open while running", trigger_type="SAFETY_STATE",
                                 default_severity="HIGH", is_safety_related=True, lifecycle_status="ACTIVE",
                                 effective_from=D0)
    shift = OperatorShift(shift_id="SHF-P01-20261003-A-01", plant_id="P01", shift_date=date(2026, 10, 3),
                          shift_code="A", shift_start=T0 - timedelta(hours=2), shift_end=T0 + timedelta(hours=6),
                          employee_id="EMP-10001", operator_name="Asha Rao", operator_role="OPERATOR",
                          lifecycle_status="COMPLETED")
    s.add_all([link, interlock, definition, shift])
    s.flush()
    assign = ShiftMachineAssignment(shift_id=shift.shift_id, machine_id="P01-M01")
    mse1 = MachineStateEvent(machine_state_event_id="MSE-P01-M01-000001", machine_id="P01-M01", sequence_no=1,
                             event_ts=T0 - timedelta(minutes=10), previous_state=None, machine_state="STOPPED",
                             door_state="CLOSED", interlock_status_summary="ALL_ENGAGED", safety_assessment="SAFE",
                             shift_id=shift.shift_id)
    s.add_all([assign, mse1])
    s.flush()
    mse2 = MachineStateEvent(machine_state_event_id="MSE-P01-M01-000002", machine_id="P01-M01", sequence_no=2,
                             event_ts=T0 - timedelta(minutes=1), previous_state="STOPPED", machine_state="RUNNING",
                             door_state="OPEN", interlock_status_summary="NONE_ENGAGED", safety_assessment="UNSAFE",
                             unsafe_rule_code="R-U1", shift_id=shift.shift_id)
    s.add(mse2)
    s.flush()
    ise = InterlockStateEvent(interlock_state_event_id="ISE-ILK-P01-M01-01-000001", interlock_id=interlock.interlock_id,
                              sequence_no=1, event_ts=T0 - timedelta(minutes=1), interlock_status="DISENGAGED",
                              door_state_observed="OPEN", machine_state_event_id=mse2.machine_state_event_id)
    alarm = AlarmEvent(alarm_event_id="ALM-P01-M01-000001", machine_id="P01-M01", alarm_definition_id="ADEF-0001",
                       interlock_id=interlock.interlock_id, safety_zone_id=zone.safety_zone_id,
                       machine_state_event_id=mse2.machine_state_event_id, shift_id=shift.shift_id, severity="CRITICAL",
                       raised_at=T0, cleared_at=T0 + timedelta(minutes=5), ack_deadline_at=T0 + timedelta(seconds=120),
                       lifecycle_status="CLEARED", ingest_batch_id="BATCH-20261003-01")
    s.add_all([ise, alarm])
    s.flush()
    ack = Acknowledgement(acknowledgement_id="ACK-ALM-P01-M01-000001-01", alarm_event_id=alarm.alarm_event_id,
                          acknowledged_by_shift_id=shift.shift_id, acknowledged_at=T0 + timedelta(seconds=45),
                          ack_method="HMI", ack_delay_seconds=Decimal("45.0"), ack_status="ON_TIME")
    incident = Incident(incident_id="INC-2026-000001", machine_id="P01-M01", safety_zone_id=zone.safety_zone_id,
                        trigger_machine_state_event_id=mse2.machine_state_event_id, trigger_rule_code="R-U1",
                        shift_id=shift.shift_id, severity="CRITICAL", status="OPEN",
                        title="Door open while running", opened_at=T0)
    s.add_all([ack, incident])
    s.flush()
    inc_link = IncidentAlarmLink(incident_id=incident.incident_id, alarm_event_id=alarm.alarm_event_id,
                                 link_role="TRIGGER", linked_at=T0)
    inc_stat = IncidentStatusEvent(incident_status_event_id="ISTAT-INC-2026-000001-01",
                                   incident_id=incident.incident_id, sequence_no=1, from_status=None,
                                   to_status="OPEN", changed_at=T0, changed_by_role="SYSTEM")
    action = CorrectiveAction(corrective_action_id="ACT-INC-2026-000001-01", incident_id=incident.incident_id,
                              assigned_engineer_id="EMP-20001", assigned_engineer_name="Ravi Menon",
                              action_type="REPAIR_DOOR_SWITCH", action_description="Replace door switch",
                              status="PLANNED", planned_at=T0 + timedelta(hours=1), due_at=T0 + timedelta(days=3))
    s.add_all([inc_link, inc_stat, action])
    s.flush()
    act_stat = CorrectiveActionStatusEvent(action_status_event_id="ASTAT-ACT-INC-2026-000001-01-01",
                                           corrective_action_id=action.corrective_action_id, sequence_no=1,
                                           from_status=None, to_status="PLANNED", changed_at=T0 + timedelta(hours=1))
    s.add(act_stat)
    s.flush()
    return dict(plant=plant, machine=machine, zone=zone, link=link, interlock=interlock, definition=definition,
                shift=shift, mse1=mse1, mse2=mse2, ise=ise, alarm=alarm, ack=ack, incident=incident,
                inc_link=inc_link, inc_stat=inc_stat, action=action, act_stat=act_stat)
