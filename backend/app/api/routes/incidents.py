"""Incident and corrective-action endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, ERRORS, Pagination, page_of, utc
from app.models import CorrectiveAction, CorrectiveActionStatusEvent, Incident, IncidentStatusEvent, Machine
from app.models import enums as E
from app.repositories.generic import commit
from app.schemas import incident as S
from app.schemas.common import ErrorResponse, Page
from app.services import incident_service as svc
from app.services.master_service import get_or_404
from app.services.trace_service import _action_details, incident_detail

router = APIRouter(responses={k: {**v, "model": ErrorResponse} for k, v in ERRORS.items()})


# ============================================================ incidents
@router.get("/incidents", response_model=Page[S.IncidentOut], tags=["incidents"], summary="List incidents")
def list_incidents(db: DB, pg: Pagination, machine_id: Optional[str] = None, plant_id: Optional[str] = None,
                   severity: Optional[E.IncidentSeverity] = None, status: Optional[E.IncidentStatus] = None,
                   trigger_rule_code: Optional[str] = None,
                   open: Optional[bool] = Query(None, description="true: not CLOSED; false: CLOSED"),
                   from_ts: Optional[datetime] = None, to_ts: Optional[datetime] = None,
                   order: str = Query("desc", pattern="^(asc|desc)$")):
    stmt = select(Incident)
    if machine_id:
        stmt = stmt.where(Incident.machine_id == machine_id)
    if plant_id:
        stmt = stmt.where(Incident.machine_id.in_(select(Machine.machine_id).where(Machine.plant_id == plant_id)))
    if severity:
        stmt = stmt.where(Incident.severity == severity.value)
    if status:
        stmt = stmt.where(Incident.status == status.value)
    if trigger_rule_code:
        stmt = stmt.where(Incident.trigger_rule_code == trigger_rule_code)
    if open is True:
        stmt = stmt.where(Incident.status != "CLOSED")
    elif open is False:
        stmt = stmt.where(Incident.status == "CLOSED")
    if from_ts:
        stmt = stmt.where(Incident.opened_at >= utc(from_ts))
    if to_ts:
        stmt = stmt.where(Incident.opened_at <= utc(to_ts))
    return page_of(db, stmt.order_by(Incident.opened_at.asc() if order == "asc" else Incident.opened_at.desc(),
                                     Incident.incident_id), pg, S.IncidentOut)


@router.get("/incidents/{incident_id}", response_model=S.IncidentDetail, tags=["incidents"],
            summary="Incident with full traceability",
            description="Trigger machine state (with interlock events), linked alarms, operator on duty, lifecycle history and "
                        "corrective actions with their histories. `context_complete=false` marks an incident without trigger context.")
def get_incident(incident_id: str, db: DB):
    return incident_detail(db, incident_id)


@router.post("/incidents", response_model=S.IncidentDetail, status_code=201, tags=["incidents"],
             summary="Create an incident from an unsafe machine state (rule IN-1)",
             description="Requires an UNSAFE machine-state event whose unsafe episode lasted at least 10 s and has a HIGH/CRITICAL "
                         "alarm on the machine (from 60 s before the episode to its end). Severity follows IN-2. One incident per "
                         "unsafe episode (409 otherwise).")
def create_incident(body: S.IncidentCreate, db: DB):
    inc = svc.create_incident(db, body)
    commit(db)
    return incident_detail(db, inc.incident_id)


@router.get("/incidents/{incident_id}/status-history", response_model=list[S.IncidentStatusEventOut], tags=["incidents"],
            summary="Incident lifecycle history (append-only)")
def incident_history(incident_id: str, db: DB):
    get_or_404(db, Incident, incident_id, "incident")
    return list(db.scalars(select(IncidentStatusEvent).where(IncidentStatusEvent.incident_id == incident_id)
                           .order_by(IncidentStatusEvent.sequence_no)))


@router.post("/incidents/{incident_id}/transitions", response_model=S.IncidentOut, tags=["incidents"],
             summary="Move an incident along its lifecycle (IN-3 / IN-4)",
             description="OPEN > INVESTIGATING > ACTION_IN_PROGRESS > RESOLVED > CLOSED. ACTION_IN_PROGRESS needs a corrective action; "
                         "RESOLVED needs every live action verified PASSED; CLOSED needs every live action CLOSED. Illegal moves are 409, "
                         "unmet preconditions 422. The change is appended to the history.")
def transition_incident(incident_id: str, body: S.IncidentTransition, db: DB):
    inc = svc.transition_incident(db, incident_id, body)
    commit(db)
    return inc


# ============================================================ corrective actions
@router.get("/incidents/{incident_id}/corrective-actions", response_model=list[S.CorrectiveActionDetail],
            tags=["corrective actions"], summary="Corrective actions of an incident, with histories")
def incident_actions(incident_id: str, db: DB):
    get_or_404(db, Incident, incident_id, "incident")
    return _action_details(db, incident_id)


@router.post("/incidents/{incident_id}/corrective-actions", response_model=S.CorrectiveActionOut, status_code=201,
             tags=["corrective actions"], summary="Plan a corrective action for an incident (CA-1)")
def create_action(incident_id: str, body: S.CorrectiveActionCreate, db: DB):
    act = svc.create_action(db, incident_id, body)
    commit(db)
    return act


@router.get("/corrective-actions", response_model=Page[S.CorrectiveActionOut], tags=["corrective actions"],
            summary="List corrective actions")
def list_actions(db: DB, pg: Pagination, incident_id: Optional[str] = None, machine_id: Optional[str] = None,
                 plant_id: Optional[str] = None, status: Optional[E.ActionStatus] = None,
                 assigned_engineer_id: Optional[str] = None, verification_result: Optional[E.VerificationResult] = None,
                 overdue: Optional[bool] = Query(None, description="due date passed and not CLOSED/CANCELLED"),
                 order: str = Query("desc", pattern="^(asc|desc)$")):
    stmt = select(CorrectiveAction)
    if incident_id:
        stmt = stmt.where(CorrectiveAction.incident_id == incident_id)
    if machine_id:
        stmt = stmt.where(CorrectiveAction.incident_id.in_(select(Incident.incident_id).where(Incident.machine_id == machine_id)))
    if plant_id:
        stmt = stmt.where(CorrectiveAction.incident_id.in_(select(Incident.incident_id).where(
            Incident.machine_id.in_(select(Machine.machine_id).where(Machine.plant_id == plant_id)))))
    if status:
        stmt = stmt.where(CorrectiveAction.status == status.value)
    if assigned_engineer_id:
        stmt = stmt.where(CorrectiveAction.assigned_engineer_id == assigned_engineer_id)
    if verification_result:
        stmt = stmt.where(CorrectiveAction.verification_result == verification_result.value)
    if overdue is not None:
        late = (CorrectiveAction.due_at < datetime.now(timezone.utc)) & CorrectiveAction.status.not_in(["CLOSED", "CANCELLED"])
        stmt = stmt.where(late if overdue else ~late)
    return page_of(db, stmt.order_by(CorrectiveAction.planned_at.asc() if order == "asc" else CorrectiveAction.planned_at.desc(),
                                     CorrectiveAction.corrective_action_id), pg, S.CorrectiveActionOut)


@router.get("/corrective-actions/{action_id}", response_model=S.CorrectiveActionDetail, tags=["corrective actions"],
            summary="Get a corrective action with its history")
def get_action(action_id: str, db: DB):
    a = get_or_404(db, CorrectiveAction, action_id, "corrective action")
    out = S.CorrectiveActionDetail.model_validate(a)
    out.history = [S.CorrectiveActionStatusEventOut.model_validate(e) for e in db.scalars(
        select(CorrectiveActionStatusEvent).where(CorrectiveActionStatusEvent.corrective_action_id == action_id)
        .order_by(CorrectiveActionStatusEvent.sequence_no))]
    return out


@router.post("/corrective-actions/{action_id}/transitions", response_model=S.CorrectiveActionOut,
             tags=["corrective actions"], summary="Move an action along its lifecycle (CA-2 / CA-3 / CA-4)",
             description="PLANNED > IN_PROGRESS > COMPLETED > VERIFIED > CLOSED. VERIFIED needs verification_result and an independent "
                         "verified_by_id. CLOSED needs a PASSED verification - closing an unverified action is rejected (422 NOT_VERIFIED). "
                         "After a FAILED verification the action may be CANCELLED (superseded) and a follow-up planned.")
def transition_action(action_id: str, body: S.ActionTransition, db: DB):
    act = svc.transition_action(db, action_id, body)
    commit(db)
    return act
