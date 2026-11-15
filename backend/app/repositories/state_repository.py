"""Data access for machine / interlock state history (no business rules here)."""
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import (
    Interlock, InterlockStateEvent, Machine, MachineStateEvent, OperatorShift, ShiftMachineAssignment,
)


def get_machine(db: Session, machine_id: str) -> Machine | None:
    return db.get(Machine, machine_id)


def active_interlocks(db: Session, machine_id: str) -> list[Interlock]:
    return list(db.scalars(select(Interlock).where(
        Interlock.machine_id == machine_id, Interlock.lifecycle_status == "ACTIVE",
        Interlock.effective_to.is_(None)).order_by(Interlock.interlock_id)))


def latest_state_event(db: Session, machine_id: str) -> MachineStateEvent | None:
    return db.scalars(select(MachineStateEvent).where(MachineStateEvent.machine_id == machine_id)
                      .order_by(MachineStateEvent.sequence_no.desc()).limit(1)).first()


def latest_interlock_event(db: Session, interlock_id: str) -> InterlockStateEvent | None:
    return db.scalars(select(InterlockStateEvent).where(InterlockStateEvent.interlock_id == interlock_id)
                      .order_by(InterlockStateEvent.sequence_no.desc()).limit(1)).first()


def shift_on_duty(db: Session, machine_id: str, ts: datetime) -> str | None:
    return db.scalars(select(OperatorShift.shift_id).join(
        ShiftMachineAssignment, ShiftMachineAssignment.shift_id == OperatorShift.shift_id).where(and_(
            ShiftMachineAssignment.machine_id == machine_id, OperatorShift.shift_start <= ts,
            OperatorShift.shift_end > ts)).limit(1)).first()


def state_history(db: Session, machine_id: str) -> list[MachineStateEvent]:
    return list(db.scalars(select(MachineStateEvent).where(MachineStateEvent.machine_id == machine_id)
                           .order_by(MachineStateEvent.sequence_no)))
