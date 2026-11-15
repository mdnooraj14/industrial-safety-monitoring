"""Master data endpoints: plants, machines, safety zones, interlocks, alarm definitions, operator shifts."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DB, ERRORS, Pagination, page_of, utc
from app.models import (
    AlarmDefinition, AlarmSeverityPolicy, Interlock, InterlockStateEvent, Machine, MachineSafetyZone,
    MachineStateEvent, OperatorShift, Plant, SafetyZone, ShiftMachineAssignment,
)
from app.models import enums as E
from app.repositories.generic import commit
from app.schemas import events as SE
from app.schemas import master as S
from app.schemas.common import ErrorResponse, Page, make_page
from app.services import master_service as svc
from app.services.trace_service import machine_statuses

router = APIRouter(responses={k: {**v, "model": ErrorResponse} for k, v in ERRORS.items()})


# ============================================================ plants
@router.get("/plants", response_model=Page[S.PlantOut], tags=["plants"], summary="List plants")
def list_plants(db: DB, pg: Pagination, lifecycle_status: Optional[E.PlantStatus] = None,
                industry_type: Optional[E.IndustryType] = None, q: Optional[str] = Query(None, description="name contains")):
    stmt = select(Plant).order_by(Plant.plant_id)
    if lifecycle_status:
        stmt = stmt.where(Plant.lifecycle_status == lifecycle_status.value)
    if industry_type:
        stmt = stmt.where(Plant.industry_type == industry_type.value)
    if q:
        stmt = stmt.where(Plant.plant_name.ilike(f"%{q}%"))
    return page_of(db, stmt, pg, S.PlantOut)


@router.get("/plants/{plant_id}", response_model=S.PlantOut, tags=["plants"], summary="Get a plant")
def get_plant(plant_id: str, db: DB):
    return svc.get_or_404(db, Plant, plant_id, "plant")


@router.post("/plants", response_model=S.PlantOut, status_code=201, tags=["plants"], summary="Create a plant")
def create_plant(body: S.PlantCreate, db: DB):
    obj = svc.create_plant(db, body)
    commit(db)
    return obj


# ============================================================ machines
@router.get("/machines/status", response_model=Page[SE.MachineStatusOut], tags=["machines"],
            summary="Operational status of machines (latest machine-state event each)")
def machine_status_list(db: DB, pg: Pagination, plant_id: Optional[str] = None, machine_id: Optional[str] = None):
    items, total = machine_statuses(db, plant_id, machine_id, pg.page, pg.page_size)
    return make_page(items, total, pg.page, pg.page_size)


@router.get("/machines", response_model=Page[S.MachineOut], tags=["machines"], summary="List machines")
def list_machines(db: DB, pg: Pagination, plant_id: Optional[str] = None, machine_type: Optional[E.MachineType] = None,
                  lifecycle_status: Optional[E.MachineStatus] = None, criticality_class: Optional[E.Criticality] = None,
                  q: Optional[str] = Query(None, description="name contains")):
    stmt = select(Machine).order_by(Machine.machine_id)
    if plant_id:
        stmt = stmt.where(Machine.plant_id == plant_id)
    if machine_type:
        stmt = stmt.where(Machine.machine_type == machine_type.value)
    if lifecycle_status:
        stmt = stmt.where(Machine.lifecycle_status == lifecycle_status.value)
    if criticality_class:
        stmt = stmt.where(Machine.criticality_class == criticality_class.value)
    if q:
        stmt = stmt.where(Machine.machine_name.ilike(f"%{q}%"))
    return page_of(db, stmt, pg, S.MachineOut)


@router.get("/machines/{machine_id}", response_model=S.MachineOut, tags=["machines"], summary="Get a machine")
def get_machine(machine_id: str, db: DB):
    return svc.get_or_404(db, Machine, machine_id, "machine")


@router.post("/machines", response_model=S.MachineOut, status_code=201, tags=["machines"], summary="Create a machine")
def create_machine(body: S.MachineCreate, db: DB):
    obj = svc.create_machine(db, body)
    commit(db)
    return obj


@router.get("/machines/{machine_id}/interlocks", response_model=list[S.InterlockOut], tags=["machines"],
            summary="Interlocks protecting a machine")
def machine_interlocks(machine_id: str, db: DB):
    svc.get_or_404(db, Machine, machine_id, "machine")
    return list(db.scalars(select(Interlock).where(Interlock.machine_id == machine_id).order_by(Interlock.interlock_id)))


@router.get("/machines/{machine_id}/safety-zones", response_model=list[S.SafetyZoneOut], tags=["machines"],
            summary="Safety zones linked to a machine (bridge table)")
def machine_zones(machine_id: str, db: DB):
    svc.get_or_404(db, Machine, machine_id, "machine")
    return list(db.scalars(select(SafetyZone).join(MachineSafetyZone, MachineSafetyZone.safety_zone_id == SafetyZone.safety_zone_id)
                           .where(MachineSafetyZone.machine_id == machine_id).order_by(SafetyZone.safety_zone_id)))


# ============================================================ safety zones
@router.get("/safety-zones", response_model=Page[S.SafetyZoneOut], tags=["safety zones"], summary="List safety zones")
def list_zones(db: DB, pg: Pagination, plant_id: Optional[str] = None, risk_level: Optional[E.RiskLevel] = None,
               zone_type: Optional[E.ZoneType] = None, machine_id: Optional[str] = None):
    stmt = select(SafetyZone).order_by(SafetyZone.safety_zone_id)
    if plant_id:
        stmt = stmt.where(SafetyZone.plant_id == plant_id)
    if risk_level:
        stmt = stmt.where(SafetyZone.risk_level == risk_level.value)
    if zone_type:
        stmt = stmt.where(SafetyZone.zone_type == zone_type.value)
    if machine_id:
        stmt = stmt.where(SafetyZone.safety_zone_id.in_(select(MachineSafetyZone.safety_zone_id)
                                                        .where(MachineSafetyZone.machine_id == machine_id)))
    return page_of(db, stmt, pg, S.SafetyZoneOut)


@router.get("/safety-zones/{zone_id}", response_model=S.SafetyZoneOut, tags=["safety zones"], summary="Get a safety zone")
def get_zone(zone_id: str, db: DB):
    return svc.get_or_404(db, SafetyZone, zone_id, "safety zone")


@router.get("/safety-zones/{zone_id}/machines", response_model=list[S.MachineZoneLinkOut], tags=["safety zones"],
            summary="Machines linked to a zone (bridge rows)")
def zone_machines(zone_id: str, db: DB):
    svc.get_or_404(db, SafetyZone, zone_id, "safety zone")
    return list(db.scalars(select(MachineSafetyZone).where(MachineSafetyZone.safety_zone_id == zone_id)
                           .order_by(MachineSafetyZone.machine_id)))


@router.post("/safety-zones", response_model=S.SafetyZoneOut, status_code=201, tags=["safety zones"],
             summary="Create a safety zone (optionally linking machines)")
def create_zone(body: S.SafetyZoneCreate, db: DB):
    obj = svc.create_safety_zone(db, body)
    commit(db)
    return obj


# ============================================================ interlocks
@router.get("/interlocks", response_model=Page[S.InterlockOut], tags=["interlocks"], summary="List interlocks")
def list_interlocks(db: DB, pg: Pagination, machine_id: Optional[str] = None, plant_id: Optional[str] = None,
                    interlock_type: Optional[E.InterlockType] = None, is_required: Optional[bool] = None,
                    safety_zone_id: Optional[str] = None):
    stmt = select(Interlock).order_by(Interlock.interlock_id)
    if machine_id:
        stmt = stmt.where(Interlock.machine_id == machine_id)
    if plant_id:
        stmt = stmt.where(Interlock.machine_id.in_(select(Machine.machine_id).where(Machine.plant_id == plant_id)))
    if interlock_type:
        stmt = stmt.where(Interlock.interlock_type == interlock_type.value)
    if is_required is not None:
        stmt = stmt.where(Interlock.is_required.is_(is_required))
    if safety_zone_id:
        stmt = stmt.where(Interlock.safety_zone_id == safety_zone_id)
    return page_of(db, stmt, pg, S.InterlockOut)


@router.get("/interlocks/{interlock_id}", response_model=S.InterlockOut, tags=["interlocks"], summary="Get an interlock")
def get_interlock(interlock_id: str, db: DB):
    return svc.get_or_404(db, Interlock, interlock_id, "interlock")


@router.get("/interlocks/{interlock_id}/state-events", response_model=Page[S.InterlockStateEventOut], tags=["interlocks"],
            summary="Status history of an interlock (append-only)")
def interlock_history(interlock_id: str, db: DB, pg: Pagination, from_ts: Optional[datetime] = None,
                      to_ts: Optional[datetime] = None, order: str = Query("desc", pattern="^(asc|desc)$")):
    svc.get_or_404(db, Interlock, interlock_id, "interlock")
    col = InterlockStateEvent.sequence_no
    stmt = select(InterlockStateEvent).where(InterlockStateEvent.interlock_id == interlock_id)
    if from_ts:
        stmt = stmt.where(InterlockStateEvent.event_ts >= utc(from_ts))
    if to_ts:
        stmt = stmt.where(InterlockStateEvent.event_ts <= utc(to_ts))
    return page_of(db, stmt.order_by(col.asc() if order == "asc" else col.desc()), pg, S.InterlockStateEventOut)


@router.post("/interlocks", response_model=S.InterlockOut, status_code=201, tags=["interlocks"], summary="Create an interlock")
def create_interlock(body: S.InterlockCreate, db: DB):
    obj = svc.create_interlock(db, body)
    commit(db)
    return obj


# ============================================================ alarm definitions
@router.get("/alarm-definitions/severity-policy", response_model=list[S.SeverityPolicyOut], tags=["alarm definitions"],
            summary="Severity policy: priority rank and acknowledgement deadline per severity")
def severity_policy(db: DB):
    return list(db.scalars(select(AlarmSeverityPolicy).order_by(AlarmSeverityPolicy.priority_rank)))


@router.get("/alarm-definitions", response_model=Page[S.AlarmDefinitionOut], tags=["alarm definitions"],
            summary="List alarm definitions")
def list_definitions(db: DB, pg: Pagination, trigger_type: Optional[E.TriggerType] = None,
                     default_severity: Optional[E.Severity] = None, lifecycle_status: Optional[E.AlarmDefStatus] = None,
                     machine_type: Optional[E.MachineType] = None, is_safety_related: Optional[bool] = None,
                     q: Optional[str] = Query(None, description="code or name contains")):
    stmt = select(AlarmDefinition).order_by(AlarmDefinition.alarm_definition_id)
    if trigger_type:
        stmt = stmt.where(AlarmDefinition.trigger_type == trigger_type.value)
    if default_severity:
        stmt = stmt.where(AlarmDefinition.default_severity == default_severity.value)
    if lifecycle_status:
        stmt = stmt.where(AlarmDefinition.lifecycle_status == lifecycle_status.value)
    if machine_type:
        stmt = stmt.where(AlarmDefinition.machine_type == machine_type.value)
    if is_safety_related is not None:
        stmt = stmt.where(AlarmDefinition.is_safety_related.is_(is_safety_related))
    if q:
        stmt = stmt.where(AlarmDefinition.alarm_code.ilike(f"%{q}%") | AlarmDefinition.alarm_name.ilike(f"%{q}%"))
    return page_of(db, stmt, pg, S.AlarmDefinitionOut)


@router.get("/alarm-definitions/{definition_id}", response_model=S.AlarmDefinitionOut, tags=["alarm definitions"],
            summary="Get an alarm definition")
def get_definition(definition_id: str, db: DB):
    return svc.get_or_404(db, AlarmDefinition, definition_id, "alarm definition")


@router.post("/alarm-definitions", response_model=S.AlarmDefinitionOut, status_code=201, tags=["alarm definitions"],
             summary="Create an alarm definition")
def create_definition(body: S.AlarmDefinitionCreate, db: DB):
    obj = svc.create_alarm_definition(db, body)
    commit(db)
    return obj


# ============================================================ operator shifts
def _shift_detail(db, shift: OperatorShift) -> S.OperatorShiftDetail:
    out = S.OperatorShiftDetail.model_validate(shift)
    out.machine_ids = list(db.scalars(select(ShiftMachineAssignment.machine_id)
                                      .where(ShiftMachineAssignment.shift_id == shift.shift_id)
                                      .order_by(ShiftMachineAssignment.machine_id)))
    return out


@router.get("/operator-shifts", response_model=Page[S.OperatorShiftOut], tags=["operator shifts"],
            summary="List operator shifts")
def list_shifts(db: DB, pg: Pagination, plant_id: Optional[str] = None, shift_date: Optional[date] = None,
                shift_code: Optional[E.ShiftCode] = None, employee_id: Optional[str] = None,
                machine_id: Optional[str] = Query(None, description="shifts that cover this machine"),
                covering: Optional[datetime] = Query(None, description="shifts in progress at this instant")):
    stmt = select(OperatorShift).order_by(OperatorShift.shift_start.desc(), OperatorShift.shift_id)
    if plant_id:
        stmt = stmt.where(OperatorShift.plant_id == plant_id)
    if shift_date:
        stmt = stmt.where(OperatorShift.shift_date == shift_date)
    if shift_code:
        stmt = stmt.where(OperatorShift.shift_code == shift_code.value)
    if employee_id:
        stmt = stmt.where(OperatorShift.employee_id == employee_id)
    if machine_id:
        stmt = stmt.where(OperatorShift.shift_id.in_(select(ShiftMachineAssignment.shift_id)
                                                     .where(ShiftMachineAssignment.machine_id == machine_id)))
    if covering:
        stmt = stmt.where(OperatorShift.shift_start <= utc(covering), OperatorShift.shift_end > utc(covering))
    return page_of(db, stmt, pg, S.OperatorShiftOut)


@router.get("/operator-shifts/{shift_id}", response_model=S.OperatorShiftDetail, tags=["operator shifts"],
            summary="Get a shift with the machines it covers")
def get_shift(shift_id: str, db: DB):
    return _shift_detail(db, svc.get_or_404(db, OperatorShift, shift_id, "shift"))


@router.post("/operator-shifts", response_model=S.OperatorShiftDetail, status_code=201, tags=["operator shifts"],
             summary="Create an operator shift (optionally assigning machines)")
def create_shift(body: S.OperatorShiftCreate, db: DB):
    obj = svc.create_shift(db, body)
    commit(db)
    return _shift_detail(db, obj)
