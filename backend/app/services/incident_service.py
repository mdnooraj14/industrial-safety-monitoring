"""Incident creation (IN-1/IN-2), lifecycle (IN-3/IN-4) and corrective-action rules (CA-1..CA-4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.business_rules.corrective_action_rules import (
    can_close_incident, valid_action_transition, valid_incident_transition,
)
from app.business_rules.incident_rules import find_unsafe_episodes, incident_candidates
from app.business_rules.config import DEFAULT_RULES
from app.core.errors import ConflictError, RuleViolationError
from app.models import (
    AlarmEvent, CorrectiveAction, CorrectiveActionStatusEvent, Incident, IncidentAlarmLink, IncidentStatusEvent,
    Machine, MachineSafetyZone, MachineStateEvent,
)
from app.repositories import state_repository as states
from app.repositories.generic import flush
from app.schemas import incident as S
from app.services.master_service import get_or_404

RULE_TITLE = {"R-U1": "Safety door open while running", "R-U2": "Required interlock inactive while running",
              "R-U3": "Required interlock bypassed while running"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(a: AlarmEvent) -> dict:
    return dict(alarm_event_id=a.alarm_event_id, machine_id=a.machine_id, severity=a.severity, raised_at=a.raised_at)


# ======================================================================== creation
def create_incident(db: Session, data: S.IncidentCreate) -> Incident:
    mse: MachineStateEvent = get_or_404(db, MachineStateEvent, data.trigger_machine_state_event_id, "machine state event")
    if mse.safety_assessment != "UNSAFE":
        raise RuleViolationError("incidents are created from UNSAFE machine states only", "NOT_UNSAFE")
    history = [dict(machine_state_event_id=e.machine_state_event_id, machine_id=e.machine_id, event_ts=e.event_ts,
                    safety_assessment=e.safety_assessment, unsafe_rule_code=e.unsafe_rule_code)
               for e in states.state_history(db, mse.machine_id)]
    data_end = max(_now(), mse.event_ts + timedelta(seconds=1))
    episodes = [e for e in find_unsafe_episodes(history, data_end) if mse.machine_state_event_id in e.event_ids]
    if not episodes:
        raise RuleViolationError("the state event is not part of an unsafe episode", "NO_EPISODE")
    ep = episodes[0]
    exists = db.scalar(select(Incident.incident_id).where(Incident.trigger_machine_state_event_id.in_(ep.event_ids)).limit(1))
    if exists:
        raise ConflictError(f"incident {exists} already exists for this unsafe episode", "INCIDENT_EXISTS")
    if ep.duration_seconds < DEFAULT_RULES.incident_min_unsafe_seconds:
        raise RuleViolationError(f"the unsafe episode lasted {ep.duration_seconds:.0f} s; at least "
                                 f"{DEFAULT_RULES.incident_min_unsafe_seconds} s is required", "EPISODE_TOO_SHORT")
    lo = ep.start_ts - timedelta(seconds=DEFAULT_RULES.incident_lookback_seconds)
    alarms = [_row(a) for a in db.scalars(select(AlarmEvent).where(
        AlarmEvent.machine_id == ep.machine_id, AlarmEvent.raised_at >= lo, AlarmEvent.raised_at <= ep.end_ts))]
    cands = incident_candidates([ep], alarms)
    if not cands:
        raise RuleViolationError("no HIGH or CRITICAL alarm on the machine in the unsafe episode window",
                                 "NO_QUALIFYING_ALARM")
    c = cands[0]
    trig = db.get(AlarmEvent, c.trigger_alarm_id)
    opened = trig.raised_at
    year = opened.astimezone(timezone.utc).year
    last = db.scalar(select(Incident.incident_id).where(Incident.incident_id.like(f"INC-{year}-%"))
                     .order_by(Incident.incident_id.desc()).limit(1))
    inc_id = f"INC-{year}-{(int(last.rsplit('-', 1)[1]) + 1) if last else 1:06d}"
    machine = db.get(Machine, ep.machine_id)
    zone = trig.safety_zone_id or db.scalar(select(MachineSafetyZone.safety_zone_id).where(
        MachineSafetyZone.machine_id == ep.machine_id, MachineSafetyZone.is_primary.is_(True)).limit(1))
    rule_title = RULE_TITLE.get(ep.rule_code, "Unsafe machine state")
    inc = Incident(
        incident_id=inc_id, machine_id=ep.machine_id, safety_zone_id=zone,
        trigger_machine_state_event_id=ep.start_event_id, trigger_rule_code=ep.rule_code,
        shift_id=states.shift_on_duty(db, ep.machine_id, opened), severity=c.severity, status="OPEN",
        title=data.title or f"{rule_title} on {machine.machine_name}",
        description=data.description or (f"{rule_title} ({ep.rule_code}) lasted {ep.duration_seconds:.0f} s on "
                                         f"{machine.machine_name} ({ep.machine_id}); {len(c.linked_alarm_ids)} related alarm(s)."),
        opened_at=opened)
    db.add(inc)
    flush(db)
    by_id = {a["alarm_event_id"]: a for a in alarms}
    for aid in c.linked_alarm_ids:
        db.add(IncidentAlarmLink(incident_id=inc_id, alarm_event_id=aid,
                                 link_role="TRIGGER" if aid == c.trigger_alarm_id else "CONTRIBUTING",
                                 linked_at=max(opened, by_id[aid]["raised_at"])))
    db.add(IncidentStatusEvent(incident_status_event_id=f"ISTAT-{inc_id}-01", incident_id=inc_id, sequence_no=1,
                               from_status=None, to_status="OPEN", changed_at=opened, changed_by_role="SYSTEM"))
    flush(db)
    return inc


# ======================================================================== lifecycle
def _live_actions(db: Session, incident_id: str) -> list[CorrectiveAction]:
    return [a for a in db.scalars(select(CorrectiveAction).where(CorrectiveAction.incident_id == incident_id))
            if a.status != "CANCELLED"]


def _action_dict(a: CorrectiveAction) -> dict:
    return dict(status=a.status, verification_result=a.verification_result, verified_at=a.verified_at,
                verified_by_id=a.verified_by_id, assigned_engineer_id=a.assigned_engineer_id,
                completed_at=a.completed_at, closed_at=a.closed_at)


def transition_incident(db: Session, incident_id: str, data: S.IncidentTransition) -> Incident:
    inc: Incident = get_or_404(db, Incident, incident_id, "incident")
    to = data.to_status.value if hasattr(data.to_status, "value") else data.to_status
    if not valid_incident_transition(inc.status, to):
        raise ConflictError(f"transition {inc.status} -> {to} is not allowed", "INVALID_TRANSITION")
    last = db.scalars(select(IncidentStatusEvent).where(IncidentStatusEvent.incident_id == incident_id)
                      .order_by(IncidentStatusEvent.sequence_no.desc()).limit(1)).first()
    at = data.changed_at or _now()
    if last is not None and at < last.changed_at:
        raise RuleViolationError("changed_at is earlier than the previous status change", "TIMESTAMP_ORDER")
    live = _live_actions(db, incident_id)
    if to == "ACTION_IN_PROGRESS" and not live:
        raise RuleViolationError("add a corrective action before moving to ACTION_IN_PROGRESS", "NO_ACTION")
    if to == "RESOLVED":
        if not live:
            raise RuleViolationError("an incident needs at least one corrective action to be resolved", "NO_ACTION")
        if any(a.verification_result != "PASSED" for a in live):
            raise RuleViolationError("every live corrective action needs a PASSED verification before the incident "
                                     "can be resolved", "ACTIONS_NOT_VERIFIED")
    if to == "CLOSED" and not can_close_incident([_action_dict(a) for a in live]):
        raise RuleViolationError("an incident can be closed only when it has corrective actions and all live actions "
                                 "are CLOSED with a valid verification (IN-4)", "ACTIONS_NOT_CLOSED")
    db.add(IncidentStatusEvent(
        incident_status_event_id=f"ISTAT-{incident_id}-{(last.sequence_no + 1 if last else 1):02d}", incident_id=incident_id,
        sequence_no=(last.sequence_no + 1 if last else 1), from_status=inc.status, to_status=to, changed_at=at,
        changed_by_role=data.changed_by_role.value if hasattr(data.changed_by_role, "value") else data.changed_by_role,
        note=data.note))
    inc.status = to
    if to == "INVESTIGATING":
        inc.investigation_started_at = at
    elif to == "RESOLVED":
        inc.resolved_at = at
    elif to == "CLOSED":
        inc.closed_at = at
    flush(db)
    return inc


# ======================================================================== corrective actions
def create_action(db: Session, incident_id: str, data: S.CorrectiveActionCreate) -> CorrectiveAction:
    inc: Incident = get_or_404(db, Incident, incident_id, "incident")
    if inc.status == "CLOSED":
        raise ConflictError("the incident is CLOSED; no further actions can be added", "INCIDENT_CLOSED")
    planned = data.planned_at or _now()
    if planned < inc.opened_at:
        raise RuleViolationError("planned_at is earlier than the incident's opening", "TIMESTAMP_ORDER")
    last = db.scalar(select(CorrectiveAction.corrective_action_id).where(CorrectiveAction.incident_id == incident_id)
                     .order_by(CorrectiveAction.corrective_action_id.desc()).limit(1))
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    aid = f"ACT-{incident_id}-{n:02d}"
    act = CorrectiveAction(
        corrective_action_id=aid, incident_id=incident_id, assigned_engineer_id=data.assigned_engineer_id,
        assigned_engineer_name=data.assigned_engineer_name, action_type=data.action_type.value,
        action_description=data.action_description, status="PLANNED", planned_at=planned, due_at=data.due_at)
    db.add(act)
    flush(db)
    db.add(CorrectiveActionStatusEvent(action_status_event_id=f"ASTAT-{aid}-01", corrective_action_id=aid, sequence_no=1,
                                       from_status=None, to_status="PLANNED", changed_at=planned))
    flush(db)
    return act


def transition_action(db: Session, action_id: str, data: S.ActionTransition) -> CorrectiveAction:
    act: CorrectiveAction = get_or_404(db, CorrectiveAction, action_id, "corrective action")
    to = data.to_status.value if hasattr(data.to_status, "value") else data.to_status
    if not valid_action_transition(act.status, to):
        raise ConflictError(f"transition {act.status} -> {to} is not allowed", "INVALID_TRANSITION")
    last = db.scalars(select(CorrectiveActionStatusEvent).where(CorrectiveActionStatusEvent.corrective_action_id == action_id)
                      .order_by(CorrectiveActionStatusEvent.sequence_no.desc()).limit(1)).first()
    at = data.changed_at or _now()
    if last is not None and at < last.changed_at:
        raise RuleViolationError("changed_at is earlier than the previous status change", "TIMESTAMP_ORDER")
    if to == "IN_PROGRESS":
        act.started_at = at
    elif to == "COMPLETED":
        act.completed_at = at
    elif to == "VERIFIED":
        if data.verification_result is None or not data.verified_by_id:
            raise RuleViolationError("verification_result and verified_by_id are required to verify an action",
                                     "VERIFICATION_REQUIRED")
        if data.verified_by_id == act.assigned_engineer_id:
            raise RuleViolationError("an action cannot be verified by the engineer it is assigned to", "SELF_VERIFICATION")
        act.verified_at, act.verified_by_id = at, data.verified_by_id
        act.verification_result = data.verification_result.value if hasattr(data.verification_result, "value") else data.verification_result
    elif to == "CLOSED":
        if act.verification_result != "PASSED":                      # CA-3
            raise RuleViolationError("an action cannot be closed without a PASSED verification", "NOT_VERIFIED")
        act.closed_at = at
    elif to == "CANCELLED" and act.status == "VERIFIED" and act.verification_result != "FAILED":
        raise RuleViolationError("a verified action can only be cancelled (superseded) after a FAILED verification",
                                 "NOT_SUPERSEDABLE")
    db.add(CorrectiveActionStatusEvent(
        action_status_event_id=f"ASTAT-{action_id}-{(last.sequence_no + 1 if last else 1):02d}", corrective_action_id=action_id,
        sequence_no=(last.sequence_no + 1 if last else 1), from_status=act.status, to_status=to, changed_at=at, note=data.note))
    act.status = to
    flush(db)
    return act
