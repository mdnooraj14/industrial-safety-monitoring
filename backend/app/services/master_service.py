"""Create master data (plants, machines, zones, interlocks, alarm definitions, shifts) with parent checks."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, RuleViolationError
from app.models import (
    AlarmDefinition, AlarmSeverityPolicy, Interlock, Machine, MachineSafetyZone, OperatorShift, Plant, SafetyZone,
    ShiftMachineAssignment,
)
from app.repositories.generic import flush
from app.schemas import master as S


def get_or_404(db: Session, model, key: str, label: str):
    obj = db.get(model, key)
    if obj is None:
        raise NotFoundError(f"{label} {key} not found")
    return obj


def _dump(data) -> dict:
    return data.model_dump(mode="python")


def _new(db: Session, model, key: str, label: str, values: dict):
    if db.get(model, key) is not None:
        raise ConflictError(f"{label} {key} already exists", "DUPLICATE")
    obj = model(**values)
    db.add(obj)
    flush(db)
    return obj


def create_plant(db: Session, data: S.PlantCreate) -> Plant:
    return _new(db, Plant, data.plant_id, "plant", _dump(data))


def create_machine(db: Session, data: S.MachineCreate) -> Machine:
    get_or_404(db, Plant, data.plant_id, "plant")
    return _new(db, Machine, data.machine_id, "machine", _dump(data))


def create_safety_zone(db: Session, data: S.SafetyZoneCreate) -> SafetyZone:
    get_or_404(db, Plant, data.plant_id, "plant")
    machines = []
    for mid in data.machine_ids:
        m = get_or_404(db, Machine, mid, "machine")
        if m.plant_id != data.plant_id:
            raise RuleViolationError(f"machine {mid} belongs to another plant", "PLANT_MISMATCH")
        machines.append(m)
    values = _dump(data)
    values.pop("machine_ids")
    zone = _new(db, SafetyZone, data.safety_zone_id, "safety zone", values)
    for m in machines:
        has_primary = db.scalar(select(MachineSafetyZone).where(MachineSafetyZone.machine_id == m.machine_id,
                                                                 MachineSafetyZone.is_primary.is_(True)).limit(1))
        db.add(MachineSafetyZone(machine_id=m.machine_id, safety_zone_id=zone.safety_zone_id,
                                 effective_from=data.effective_from, is_primary=has_primary is None))
    flush(db)
    return zone


def create_interlock(db: Session, data: S.InterlockCreate) -> Interlock:
    machine = get_or_404(db, Machine, data.machine_id, "machine")
    if data.safety_zone_id:
        zone = get_or_404(db, SafetyZone, data.safety_zone_id, "safety zone")
        linked = db.scalar(select(MachineSafetyZone).where(MachineSafetyZone.machine_id == machine.machine_id,
                                                           MachineSafetyZone.safety_zone_id == zone.safety_zone_id).limit(1))
        if linked is None:
            raise RuleViolationError("the safety zone is not linked to the machine", "ZONE_NOT_LINKED")
    return _new(db, Interlock, data.interlock_id, "interlock", _dump(data))


def create_alarm_definition(db: Session, data: S.AlarmDefinitionCreate) -> AlarmDefinition:
    if db.get(AlarmSeverityPolicy, data.default_severity) is None:
        raise RuleViolationError("no severity policy for that severity", "NO_POLICY")
    if db.scalar(select(AlarmDefinition).where(AlarmDefinition.alarm_code == data.alarm_code).limit(1)):
        raise ConflictError(f"alarm code {data.alarm_code} already exists", "DUPLICATE")
    return _new(db, AlarmDefinition, data.alarm_definition_id, "alarm definition", _dump(data))


def create_shift(db: Session, data: S.OperatorShiftCreate) -> OperatorShift:
    get_or_404(db, Plant, data.plant_id, "plant")
    for mid in data.machine_ids:
        m = get_or_404(db, Machine, mid, "machine")
        if m.plant_id != data.plant_id:
            raise RuleViolationError(f"machine {mid} belongs to another plant", "PLANT_MISMATCH")
    values = _dump(data)
    values.pop("machine_ids")
    shift = _new(db, OperatorShift, data.shift_id, "shift", values)
    for mid in dict.fromkeys(data.machine_ids):
        db.add(ShiftMachineAssignment(shift_id=shift.shift_id, machine_id=mid))
    flush(db)
    return shift
