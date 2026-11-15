"""Record machine-state events: validate against history, derive the assessment, append immutable rows."""
from datetime import datetime
from typing import Mapping

from sqlalchemy.orm import Session

from app.business_rules.safety_state import DOOR_TYPES
from app.business_rules.state_processing import (
    InterlockSpec, PreviousState, RuleViolation, process_state_event,
)
from app.core.errors import NotFoundError, RuleViolationError
from app.models import InterlockStateEvent, MachineStateEvent
from app.repositories import state_repository as repo


def record_machine_state(db: Session, machine_id: str, event_ts: datetime, machine_state: str, door_state: str,
                         interlock_statuses: Mapping[str, str] | None = None,
                         reason: str | None = None) -> MachineStateEvent:
    """Append one machine-state event (+ interlock events for every interlock whose status changed).

    * Unknown machine -> NotFoundError. Hard rule violations -> RuleViolationError (nothing is written).
    * Impossible-but-recordable readings are stored with safety_assessment INVALID (flagged), never dropped.
    * Existing rows are never modified.
    """
    machine = repo.get_machine(db, machine_id)
    if machine is None:
        raise NotFoundError(f"machine {machine_id} not found")
    interlocks = repo.active_interlocks(db, machine_id)
    specs = [InterlockSpec(i.interlock_id, i.interlock_type, i.is_required, i.bypass_allowed) for i in interlocks]
    last = repo.latest_state_event(db, machine_id)
    prev = None
    if last is not None:
        current = {}
        for i in interlocks:
            ev = repo.latest_interlock_event(db, i.interlock_id)
            current[i.interlock_id] = ev.interlock_status if ev else "ENGAGED"
        prev = PreviousState(last.machine_state, last.door_state, last.event_ts, last.sequence_no, current)
    try:
        result = process_state_event(prev, event_ts, machine_state, door_state, interlock_statuses or {}, specs)
    except RuleViolation as exc:
        raise RuleViolationError(exc.message, exc.code) from exc

    mse = MachineStateEvent(
        machine_state_event_id=f"MSE-{machine_id}-{result.sequence_no:06d}", machine_id=machine_id,
        sequence_no=result.sequence_no, event_ts=event_ts, previous_state=result.previous_state,
        machine_state=machine_state, door_state=door_state, interlock_status_summary=result.summary,
        safety_assessment=result.assessment, unsafe_rule_code=result.rule_code,
        shift_id=repo.shift_on_duty(db, machine_id, event_ts), transition_reason=reason)
    db.add(mse)
    db.flush()
    by_id = {i.interlock_id: i for i in interlocks}
    for iid in result.changed_interlocks:
        last_i = repo.latest_interlock_event(db, iid)
        seq = (last_i.sequence_no + 1) if last_i else 1
        is_door = by_id[iid].interlock_type in DOOR_TYPES
        db.add(InterlockStateEvent(
            interlock_state_event_id=f"ISE-{iid}-{seq:06d}", interlock_id=iid, sequence_no=seq, event_ts=event_ts,
            interlock_status=result.interlock_statuses[iid],
            door_state_observed=(("OPEN" if door_state == "OPEN" else "CLOSED") if is_door else None),
            machine_state_event_id=mse.machine_state_event_id))
    db.flush()
    return mse
