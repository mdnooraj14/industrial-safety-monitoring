"""Event endpoints: machine-state events, alarm events, acknowledgements."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, ERRORS, Pagination, page_of, utc
from app.models import (
    Acknowledgement, AlarmEvent, AlarmSeverityPolicy, InterlockStateEvent, Machine, MachineStateEvent,
)
from app.models import enums as E
from app.repositories.generic import commit
from app.schemas import events as S
from app.schemas.common import ErrorResponse, Page
from app.schemas.master import InterlockStateEventOut
from app.services import alarm_service
from app.services.master_service import get_or_404
from app.services.state_event_service import record_machine_state
from app.services.trace_service import alarm_trace

router = APIRouter(responses={k: {**v, "model": ErrorResponse} for k, v in ERRORS.items()})


def _state_detail(db, mse: MachineStateEvent) -> S.MachineStateEventDetail:
    out = S.MachineStateEventDetail.model_validate(mse)
    out.interlock_events = [InterlockStateEventOut.model_validate(e) for e in db.scalars(
        select(InterlockStateEvent).where(InterlockStateEvent.machine_state_event_id == mse.machine_state_event_id)
        .order_by(InterlockStateEvent.interlock_id))]
    return out


# ============================================================ machine-state events
def _state_stmt(machine_id, plant_id, assessment, rule, from_ts, to_ts, order):
    stmt = select(MachineStateEvent)
    if machine_id:
        stmt = stmt.where(MachineStateEvent.machine_id == machine_id)
    if plant_id:
        stmt = stmt.where(MachineStateEvent.machine_id.in_(select(Machine.machine_id).where(Machine.plant_id == plant_id)))
    if assessment:
        stmt = stmt.where(MachineStateEvent.safety_assessment == assessment.value)
    if rule:
        stmt = stmt.where(MachineStateEvent.unsafe_rule_code == rule)
    if from_ts:
        stmt = stmt.where(MachineStateEvent.event_ts >= utc(from_ts))
    if to_ts:
        stmt = stmt.where(MachineStateEvent.event_ts <= utc(to_ts))
    asc = order == "asc"
    return stmt.order_by(MachineStateEvent.event_ts.asc() if asc else MachineStateEvent.event_ts.desc(),
                         MachineStateEvent.machine_state_event_id)


@router.get("/machine-state-events", response_model=Page[S.MachineStateEventOut], tags=["machine state events"],
            summary="List machine-state events (append-only history)")
def list_states(db: DB, pg: Pagination, machine_id: Optional[str] = None, plant_id: Optional[str] = None,
                safety_assessment: Optional[E.SafetyAssessment] = None, unsafe_rule_code: Optional[str] = None,
                from_ts: Optional[datetime] = None, to_ts: Optional[datetime] = None,
                order: str = Query("desc", pattern="^(asc|desc)$")):
    return page_of(db, _state_stmt(machine_id, plant_id, safety_assessment, unsafe_rule_code, from_ts, to_ts, order),
                   pg, S.MachineStateEventOut)


@router.get("/machines/{machine_id}/state-events", response_model=Page[S.MachineStateEventOut], tags=["machines"],
            summary="Machine-state history of one machine")
def machine_state_history(machine_id: str, db: DB, pg: Pagination, safety_assessment: Optional[E.SafetyAssessment] = None,
                          from_ts: Optional[datetime] = None, to_ts: Optional[datetime] = None,
                          order: str = Query("desc", pattern="^(asc|desc)$")):
    get_or_404(db, Machine, machine_id, "machine")
    return page_of(db, _state_stmt(machine_id, None, safety_assessment, None, from_ts, to_ts, order), pg, S.MachineStateEventOut)


@router.get("/machine-state-events/{event_id}", response_model=S.MachineStateEventDetail, tags=["machine state events"],
            summary="Get a machine-state event with its interlock events")
def get_state(event_id: str, db: DB):
    return _state_detail(db, get_or_404(db, MachineStateEvent, event_id, "machine state event"))


@router.post("/machine-state-events", response_model=S.MachineStateEventDetail, status_code=201,
             tags=["machine state events"],
             summary="Record a machine-state reading",
             description="The safety assessment is derived by the rules (R-U1..R-U3 unsafe, R-I1/R-I3/R-I4 flagged INVALID). "
                         "Impossible readings are stored and flagged; malformed or out-of-order readings are rejected (422).")
def record_state(body: S.MachineStateCreate, db: DB):
    mse = record_machine_state(db, body.machine_id, body.event_ts, body.machine_state.value, body.door_state.value,
                               {k: v.value for k, v in body.interlock_statuses.items()}, body.reason)
    commit(db)
    return _state_detail(db, mse)


# ============================================================ alarm events
@router.get("/alarm-events", response_model=Page[S.AlarmEventOut], tags=["alarm events"],
            summary="List alarm events",
            description="`sort=priority` orders by severity rank (CRITICAL first) then newest (rule AL-3); default is newest first.")
def list_alarms(db: DB, pg: Pagination, machine_id: Optional[str] = None, plant_id: Optional[str] = None,
                severity: Optional[E.Severity] = None, alarm_definition_id: Optional[str] = None,
                lifecycle_status: Optional[E.AlarmLifecycle] = None,
                acknowledged: Optional[bool] = Query(None, description="true: has an acknowledgement; false: has none"),
                overdue_before: Optional[datetime] = Query(None, description="unacknowledged alarms whose deadline passed before this time"),
                from_ts: Optional[datetime] = None, to_ts: Optional[datetime] = None,
                sort: str = Query("time", pattern="^(time|priority)$"), order: str = Query("desc", pattern="^(asc|desc)$")):
    stmt = select(AlarmEvent)
    if machine_id:
        stmt = stmt.where(AlarmEvent.machine_id == machine_id)
    if plant_id:
        stmt = stmt.where(AlarmEvent.machine_id.in_(select(Machine.machine_id).where(Machine.plant_id == plant_id)))
    if severity:
        stmt = stmt.where(AlarmEvent.severity == severity.value)
    if alarm_definition_id:
        stmt = stmt.where(AlarmEvent.alarm_definition_id == alarm_definition_id)
    if lifecycle_status:
        stmt = stmt.where(AlarmEvent.lifecycle_status == lifecycle_status.value)
    acked = select(Acknowledgement.alarm_event_id)
    if acknowledged is True:
        stmt = stmt.where(AlarmEvent.alarm_event_id.in_(acked))
    elif acknowledged is False:
        stmt = stmt.where(AlarmEvent.alarm_event_id.not_in(acked))
    if overdue_before:
        stmt = stmt.where(AlarmEvent.alarm_event_id.not_in(acked), AlarmEvent.ack_deadline_at < utc(overdue_before))
    if from_ts:
        stmt = stmt.where(AlarmEvent.raised_at >= utc(from_ts))
    if to_ts:
        stmt = stmt.where(AlarmEvent.raised_at <= utc(to_ts))
    when = AlarmEvent.raised_at.asc() if order == "asc" else AlarmEvent.raised_at.desc()
    if sort == "priority":
        stmt = stmt.join(AlarmSeverityPolicy, AlarmSeverityPolicy.severity == AlarmEvent.severity).order_by(
            AlarmSeverityPolicy.priority_rank, when, AlarmEvent.alarm_event_id)
    else:
        stmt = stmt.order_by(when, AlarmEvent.alarm_event_id)
    return page_of(db, stmt, pg, S.AlarmEventOut)


@router.get("/alarm-events/{alarm_event_id}", response_model=S.AlarmEventDetail, tags=["alarm events"],
            summary="Get an alarm event with its acknowledgement")
def get_alarm(alarm_event_id: str, db: DB):
    a = get_or_404(db, AlarmEvent, alarm_event_id, "alarm event")
    out = S.AlarmEventDetail.model_validate(a)
    ack = db.scalar(select(Acknowledgement).where(Acknowledgement.alarm_event_id == alarm_event_id))
    out.acknowledgement = S.AcknowledgementOut.model_validate(ack) if ack else None
    return out


@router.get("/alarm-events/{alarm_event_id}/trace", response_model=S.AlarmTrace, tags=["alarm events"],
            summary="Trace an alarm to its machine state, operator, acknowledgement, incident and corrective actions")
def trace_alarm(alarm_event_id: str, db: DB, cutoff: Optional[datetime] = Query(
        None, description="snapshot time for the MISSED/PENDING decision (default: now)")):
    return alarm_trace(db, alarm_event_id, utc(cutoff))


@router.post("/alarm-events", response_model=S.AlarmEventOut, status_code=201, tags=["alarm events"],
             summary="Raise an alarm",
             description="Severity is classified (AL-1) from the definition, the machine state in effect and the zone risk; "
                         "the acknowledgement deadline comes from the severity policy (AL-2). SAFETY_STATE alarms need an UNSAFE "
                         "state; an identical alarm in the same second is rejected as a duplicate (409).")
def raise_alarm(body: S.AlarmCreate, db: DB):
    a = alarm_service.raise_alarm(db, body.machine_id, body.alarm_definition_id, body.raised_at, body.interlock_id,
                                  body.cleared_at)
    commit(db)
    return a


# ============================================================ acknowledgements
@router.get("/acknowledgements", response_model=Page[S.AcknowledgementOut], tags=["acknowledgements"],
            summary="List acknowledgements")
def list_acks(db: DB, pg: Pagination, alarm_event_id: Optional[str] = None, machine_id: Optional[str] = None,
              ack_status: Optional[E.AckStatus] = None, shift_id: Optional[str] = None,
              min_delay_seconds: Optional[float] = None, from_ts: Optional[datetime] = None,
              to_ts: Optional[datetime] = None, order: str = Query("desc", pattern="^(asc|desc)$")):
    stmt = select(Acknowledgement)
    if alarm_event_id:
        stmt = stmt.where(Acknowledgement.alarm_event_id == alarm_event_id)
    if machine_id:
        stmt = stmt.where(Acknowledgement.alarm_event_id.in_(select(AlarmEvent.alarm_event_id)
                                                             .where(AlarmEvent.machine_id == machine_id)))
    if ack_status:
        stmt = stmt.where(Acknowledgement.ack_status == ack_status.value)
    if shift_id:
        stmt = stmt.where(Acknowledgement.acknowledged_by_shift_id == shift_id)
    if min_delay_seconds is not None:
        stmt = stmt.where(Acknowledgement.ack_delay_seconds >= min_delay_seconds)
    if from_ts:
        stmt = stmt.where(Acknowledgement.acknowledged_at >= utc(from_ts))
    if to_ts:
        stmt = stmt.where(Acknowledgement.acknowledged_at <= utc(to_ts))
    col = Acknowledgement.acknowledged_at
    return page_of(db, stmt.order_by(col.asc() if order == "asc" else col.desc(), Acknowledgement.acknowledgement_id),
                   pg, S.AcknowledgementOut)


@router.get("/acknowledgements/{acknowledgement_id}", response_model=S.AcknowledgementOut, tags=["acknowledgements"],
            summary="Get an acknowledgement")
def get_ack(acknowledgement_id: str, db: DB):
    return get_or_404(db, Acknowledgement, acknowledgement_id, "acknowledgement")


@router.post("/acknowledgements", response_model=S.AcknowledgementOut, status_code=201, tags=["acknowledgements"],
             summary="Acknowledge an alarm",
             description="Delay and ON_TIME/LATE status are derived from the timestamps (AL-4). An acknowledgement earlier than its "
                         "alarm is rejected (422 ACK_BEFORE_ALARM); a second acknowledgement is a conflict (409).")
def acknowledge(body: S.AcknowledgementCreate, db: DB):
    ack = alarm_service.acknowledge(db, body.alarm_event_id, body.acknowledged_at, body.acknowledged_by_shift_id,
                                    body.ack_method.value, body.comment)
    commit(db)
    return ack
