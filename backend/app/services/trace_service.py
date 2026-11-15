"""Read models that tie records together: incident trace, alarm trace, machine operational status."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    Acknowledgement, AlarmDefinition, AlarmEvent, CorrectiveAction, CorrectiveActionStatusEvent, Incident,
    IncidentAlarmLink, IncidentStatusEvent, InterlockStateEvent, Machine, MachineStateEvent, OperatorShift,
)
from app.schemas import events as SE
from app.schemas import incident as SI
from app.schemas import master as SM
from app.services.master_service import get_or_404


def _state_detail(db: Session, state_id: str | None) -> SE.MachineStateEventDetail | None:
    if not state_id:
        return None
    mse = db.get(MachineStateEvent, state_id)
    if mse is None:
        return None
    ev = list(db.scalars(select(InterlockStateEvent).where(InterlockStateEvent.machine_state_event_id == state_id)
                         .order_by(InterlockStateEvent.interlock_id)))
    out = SE.MachineStateEventDetail.model_validate(mse)
    out.interlock_events = [SM.InterlockStateEventOut.model_validate(e) for e in ev]
    return out


def _action_details(db: Session, incident_id: str) -> list[SI.CorrectiveActionDetail]:
    out = []
    for a in db.scalars(select(CorrectiveAction).where(CorrectiveAction.incident_id == incident_id)
                        .order_by(CorrectiveAction.corrective_action_id)):
        d = SI.CorrectiveActionDetail.model_validate(a)
        d.history = [SI.CorrectiveActionStatusEventOut.model_validate(e) for e in db.scalars(
            select(CorrectiveActionStatusEvent).where(CorrectiveActionStatusEvent.corrective_action_id == a.corrective_action_id)
            .order_by(CorrectiveActionStatusEvent.sequence_no))]
        out.append(d)
    return out


def incident_detail(db: Session, incident_id: str) -> SI.IncidentDetail:
    inc = get_or_404(db, Incident, incident_id, "incident")
    links = list(db.scalars(select(IncidentAlarmLink).where(IncidentAlarmLink.incident_id == incident_id)))
    alarms = []
    for l in sorted(links, key=lambda l: (l.link_role != "TRIGGER", l.linked_at, l.alarm_event_id)):
        alarms.append(SI.IncidentAlarmOut(link_role=l.link_role, linked_at=l.linked_at,
                                          alarm=SE.AlarmEventOut.model_validate(db.get(AlarmEvent, l.alarm_event_id))))
    shift = db.get(OperatorShift, inc.shift_id) if inc.shift_id else None
    history = db.scalars(select(IncidentStatusEvent).where(IncidentStatusEvent.incident_id == incident_id)
                         .order_by(IncidentStatusEvent.sequence_no))
    return SI.IncidentDetail(
        incident=SI.IncidentOut.model_validate(inc), trigger_state=_state_detail(db, inc.trigger_machine_state_event_id),
        alarms=alarms, shift=SM.OperatorShiftOut.model_validate(shift) if shift else None,
        status_history=[SI.IncidentStatusEventOut.model_validate(e) for e in history],
        corrective_actions=_action_details(db, incident_id),
        context_complete=bool(inc.trigger_machine_state_event_id) and any(l.link_role == "TRIGGER" for l in links))


def alarm_trace(db: Session, alarm_event_id: str, cutoff: datetime | None = None) -> SE.AlarmTrace:
    a = get_or_404(db, AlarmEvent, alarm_event_id, "alarm event")
    ack = db.scalar(select(Acknowledgement).where(Acknowledgement.alarm_event_id == alarm_event_id))
    cutoff = cutoff or datetime.now(timezone.utc)
    if ack is not None:
        outcome = ("ON_TIME" if ack.acknowledged_at <= a.ack_deadline_at else "LATE") if ack.acknowledged_at >= a.raised_at else "INVALID"
    else:
        outcome = "MISSED" if a.ack_deadline_at < cutoff else "PENDING"
    shift = db.get(OperatorShift, a.shift_id) if a.shift_id else None
    incidents, actions = [], []
    for l in db.scalars(select(IncidentAlarmLink).where(IncidentAlarmLink.alarm_event_id == alarm_event_id)):
        inc = db.get(Incident, l.incident_id)
        incidents.append(SE.IncidentRef(incident_id=inc.incident_id, severity=inc.severity, status=inc.status,
                                        link_role=l.link_role))
        actions += [x.model_dump(mode="json") for x in _action_details(db, inc.incident_id)]
    return SE.AlarmTrace(
        alarm=SE.AlarmEventOut.model_validate(a),
        definition=SM.AlarmDefinitionOut.model_validate(db.get(AlarmDefinition, a.alarm_definition_id)),
        machine=SM.MachineOut.model_validate(db.get(Machine, a.machine_id)),
        machine_state=_state_detail(db, a.machine_state_event_id),
        shift=SM.OperatorShiftOut.model_validate(shift) if shift else None,
        acknowledgement=SE.AcknowledgementOut.model_validate(ack) if ack else None, ack_outcome=outcome,
        incidents=incidents, corrective_actions=actions)


def machine_statuses(db: Session, plant_id: str | None, machine_id: str | None, page: int, page_size: int):
    latest = (select(MachineStateEvent.machine_id.label("mid"), func.max(MachineStateEvent.sequence_no).label("seq"),
                     func.count().label("n")).group_by(MachineStateEvent.machine_id).subquery())
    stmt = (select(Machine, MachineStateEvent, latest.c.n)
            .outerjoin(latest, latest.c.mid == Machine.machine_id)
            .outerjoin(MachineStateEvent, and_(MachineStateEvent.machine_id == latest.c.mid,
                                               MachineStateEvent.sequence_no == latest.c.seq))
            .order_by(Machine.machine_id))
    if plant_id:
        stmt = stmt.where(Machine.plant_id == plant_id)
    if machine_id:
        stmt = stmt.where(Machine.machine_id == machine_id)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(stmt.limit(page_size).offset((page - 1) * page_size)).all()
    items = []
    for m, s, n in rows:
        items.append(SE.MachineStatusOut(
            machine_id=m.machine_id, machine_name=m.machine_name, plant_id=m.plant_id, machine_type=m.machine_type,
            lifecycle_status=m.lifecycle_status, machine_state=s.machine_state if s else None,
            door_state=s.door_state if s else None, interlock_status_summary=s.interlock_status_summary if s else None,
            safety_assessment=s.safety_assessment if s else None, unsafe_rule_code=s.unsafe_rule_code if s else None,
            last_event_ts=s.event_ts if s else None, state_events=n or 0))
    return items, total
