"""Raise alarms and record acknowledgements, enforcing rules AL-1 .. AL-4."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_rules.alarm_rules import ack_delay_seconds, ack_status, classify_severity
from app.core.errors import ConflictError, NotFoundError, RuleViolationError
from app.models import (
    Acknowledgement, AlarmDefinition, AlarmEvent, AlarmSeverityPolicy, Interlock, Machine, MachineSafetyZone,
    MachineStateEvent, OperatorShift, SafetyZone,
)
from app.repositories import state_repository as states
from app.repositories.generic import flush
from app.services.master_service import get_or_404


def _next_alarm_id(db: Session, machine_id: str) -> str:
    last = db.scalar(select(AlarmEvent.alarm_event_id).where(AlarmEvent.machine_id == machine_id)
                     .order_by(AlarmEvent.alarm_event_id.desc()).limit(1))
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"ALM-{machine_id}-{n:06d}"


def _state_in_effect(db: Session, machine_id: str, ts: datetime) -> MachineStateEvent | None:
    return db.scalars(select(MachineStateEvent).where(MachineStateEvent.machine_id == machine_id,
                                                      MachineStateEvent.event_ts <= ts)
                      .order_by(MachineStateEvent.sequence_no.desc()).limit(1)).first()


def raise_alarm(db: Session, machine_id: str, alarm_definition_id: str, raised_at: datetime,
                interlock_id: str | None = None, cleared_at: datetime | None = None) -> AlarmEvent:
    """AL-1: severity is classified from the definition, the machine state in effect and the zone risk.
    AL-2: the acknowledgement deadline comes from the severity policy table.
    """
    machine: Machine = get_or_404(db, Machine, machine_id, "machine")
    d: AlarmDefinition = get_or_404(db, AlarmDefinition, alarm_definition_id, "alarm definition")
    day = raised_at.date()
    if d.lifecycle_status != "ACTIVE" or d.effective_from > day or (d.effective_to and d.effective_to < day):
        raise RuleViolationError("the alarm definition is not active at raised_at", "DEFINITION_INACTIVE")
    if d.machine_type not in (None, machine.machine_type):
        raise RuleViolationError(f"the alarm definition applies to {d.machine_type} machines only", "DEFINITION_NOT_APPLICABLE")
    if cleared_at is not None and cleared_at < raised_at:
        raise RuleViolationError("cleared_at must not be before raised_at", "CLEARED_BEFORE_RAISED")
    st = _state_in_effect(db, machine_id, raised_at)
    if d.trigger_type == "SAFETY_STATE" and (st is None or st.safety_assessment != "UNSAFE"):
        raise RuleViolationError("SAFETY_STATE alarms are raised only while the machine state is UNSAFE", "NO_UNSAFE_STATE")
    interlock: Interlock | None = None
    if interlock_id:
        interlock = get_or_404(db, Interlock, interlock_id, "interlock")
        if interlock.machine_id != machine_id:
            raise RuleViolationError("the interlock belongs to another machine", "INTERLOCK_MISMATCH")
    elif d.trigger_type == "INTERLOCK_FAULT":
        interlock = db.scalars(select(Interlock).where(Interlock.machine_id == machine_id,
                                                       Interlock.interlock_type == d.interlock_type)
                               .order_by(Interlock.interlock_id).limit(1)).first()
        if interlock is None:
            raise RuleViolationError(f"the machine has no {d.interlock_type} interlock", "NO_MATCHING_INTERLOCK")
    zone_id = None
    if d.is_safety_related:
        zone_id = (interlock.safety_zone_id if interlock else None) or db.scalar(
            select(MachineSafetyZone.safety_zone_id).where(MachineSafetyZone.machine_id == machine_id,
                                                           MachineSafetyZone.is_primary.is_(True)).limit(1))
    zone = db.get(SafetyZone, zone_id) if zone_id else None
    severity = classify_severity(d.default_severity, st.safety_assessment if st else None, d.is_safety_related,
                                 zone.risk_level if zone else None)
    policy = db.get(AlarmSeverityPolicy, severity)
    raised_s = raised_at.replace(microsecond=0)
    clash = db.scalar(select(AlarmEvent.alarm_event_id).where(
        AlarmEvent.machine_id == machine_id, AlarmEvent.alarm_definition_id == alarm_definition_id,
        AlarmEvent.raised_at >= raised_s, AlarmEvent.raised_at < raised_s + timedelta(seconds=1)).limit(1))
    if clash:
        raise ConflictError(f"the same alarm was already recorded as {clash}", "DUPLICATE_ALARM")
    alarm = AlarmEvent(
        alarm_event_id=_next_alarm_id(db, machine_id), machine_id=machine_id, alarm_definition_id=alarm_definition_id,
        interlock_id=interlock.interlock_id if interlock else None, safety_zone_id=zone_id,
        machine_state_event_id=st.machine_state_event_id if st else None,
        shift_id=states.shift_on_duty(db, machine_id, raised_at), severity=severity, raised_at=raised_at,
        cleared_at=cleared_at, ack_deadline_at=raised_at + timedelta(seconds=policy.ack_deadline_seconds),
        lifecycle_status="CLEARED" if cleared_at else "ACTIVE", ingest_batch_id=f"BATCH-{raised_at:%Y%m%d}-01")
    db.add(alarm)
    flush(db)
    return alarm


def acknowledge(db: Session, alarm_event_id: str, acknowledged_at: datetime, shift_id: str | None,
                method: str, comment: str | None) -> Acknowledgement:
    """AL-4: the delay and status are derived from the timestamps; an acknowledgement can never precede its alarm."""
    alarm = get_or_404(db, AlarmEvent, alarm_event_id, "alarm event")
    if db.scalar(select(Acknowledgement.acknowledgement_id).where(Acknowledgement.alarm_event_id == alarm_event_id)):
        raise ConflictError("the alarm is already acknowledged", "ALREADY_ACKNOWLEDGED")
    if acknowledged_at < alarm.raised_at:
        raise RuleViolationError("an acknowledgement cannot be earlier than the alarm it acknowledges", "ACK_BEFORE_ALARM")
    if shift_id is None:
        shift_id = states.shift_on_duty(db, alarm.machine_id, acknowledged_at)
        if shift_id is None:
            raise RuleViolationError("no operator shift covers the machine at acknowledged_at; pass acknowledged_by_shift_id",
                                     "NO_SHIFT")
    else:
        shift = get_or_404(db, OperatorShift, shift_id, "shift")
        plant = db.get(Machine, alarm.machine_id).plant_id
        if shift.plant_id != plant:
            raise RuleViolationError("the shift belongs to another plant", "PLANT_MISMATCH")
    ack = Acknowledgement(
        acknowledgement_id=f"ACK-{alarm_event_id}-01", alarm_event_id=alarm_event_id, acknowledged_by_shift_id=shift_id,
        acknowledged_at=acknowledged_at, ack_method=method,
        ack_delay_seconds=ack_delay_seconds(alarm.raised_at, acknowledged_at),
        ack_status=ack_status(acknowledged_at, alarm.ack_deadline_at), comment=comment)
    db.add(ack)
    flush(db)
    return ack
