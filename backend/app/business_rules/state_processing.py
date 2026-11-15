"""Machine-state event processing: validate an incoming reading against history and derive its safety assessment.

Hard problems (unknown interlock, bad enum, non-increasing timestamp, door state that does not fit the machine, no
change) raise RuleViolation and the event is rejected. *Impossible but recordable* readings (R-I1 door engaged while
open, R-I3 illegal transition, R-I4 disallowed bypass) are accepted and FLAGGED with assessment INVALID so they stay
visible - history is never dropped or rewritten. Pure: no database access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Sequence

from .safety_state import (
    DOOR_TYPES, InterlockReading, assess_state, is_valid_transition, summarize_interlocks,
)

MACHINE_STATES = {"RUNNING", "IDLE", "STOPPED", "E_STOPPED", "FAULT", "MAINTENANCE"}
DOOR_STATES = {"OPEN", "CLOSED", "NOT_APPLICABLE"}
INTERLOCK_STATUSES = {"ENGAGED", "DISENGAGED", "BYPASSED", "FAULT"}


class RuleViolation(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class InterlockSpec:
    interlock_id: str
    interlock_type: str
    is_required: bool
    bypass_allowed: bool


@dataclass(frozen=True)
class PreviousState:
    machine_state: str
    door_state: str
    event_ts: datetime
    sequence_no: int
    interlock_statuses: Mapping[str, str]


@dataclass
class ProcessedState:
    machine_state: str
    door_state: str
    interlock_statuses: dict[str, str]
    summary: str
    assessment: str
    rule_code: Optional[str]
    previous_state: Optional[str]
    sequence_no: int
    changed_interlocks: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.assessment != "SAFE"


def process_state_event(prev: Optional[PreviousState], event_ts: datetime, machine_state: str, door_state: str,
                        new_statuses: Mapping[str, str], interlocks: Sequence[InterlockSpec]) -> ProcessedState:
    if machine_state not in MACHINE_STATES:
        raise RuleViolation("BAD_MACHINE_STATE", f"unknown machine_state {machine_state!r}")
    if door_state not in DOOR_STATES:
        raise RuleViolation("BAD_DOOR_STATE", f"unknown door_state {door_state!r}")
    by_id = {i.interlock_id: i for i in interlocks}
    for iid, st in new_statuses.items():
        if iid not in by_id:
            raise RuleViolation("UNKNOWN_INTERLOCK", f"interlock {iid} does not belong to this machine")
        if st not in INTERLOCK_STATUSES:
            raise RuleViolation("BAD_INTERLOCK_STATUS", f"unknown interlock status {st!r}")
    has_door = any(i.interlock_type in DOOR_TYPES for i in interlocks)
    if has_door == (door_state == "NOT_APPLICABLE"):
        raise RuleViolation("DOOR_STATE_MISMATCH",
                            "door_state must be OPEN/CLOSED for machines with door interlocks, else NOT_APPLICABLE")
    if prev is not None and event_ts <= prev.event_ts:
        raise RuleViolation("TIMESTAMP_ORDER", "event_ts must be later than the previous state event")

    statuses = {i.interlock_id: (prev.interlock_statuses.get(i.interlock_id, "ENGAGED") if prev else "ENGAGED")
                for i in interlocks}
    statuses.update(new_statuses)
    if prev is not None and (machine_state, door_state) == (prev.machine_state, prev.door_state) \
            and statuses == dict(prev.interlock_statuses):
        raise RuleViolation("NO_CHANGE", "reading is identical to the current state")

    readings = [InterlockReading(i.interlock_type, statuses[i.interlock_id], i.bypass_allowed,
                                 (("OPEN" if door_state == "OPEN" else "CLOSED") if i.interlock_type in DOOR_TYPES else None),
                                 i.is_required) for i in interlocks]
    assessment, code = assess_state(machine_state, door_state, readings)
    previous_state = prev.machine_state if prev else None
    if assessment != "INVALID" and not is_valid_transition(previous_state, machine_state):
        assessment, code = "INVALID", "R-I3"
    changed = [iid for iid in statuses
               if prev is None or statuses[iid] != prev.interlock_statuses.get(iid)
               or (by_id[iid].interlock_type in DOOR_TYPES and door_state != prev.door_state)]
    return ProcessedState(machine_state, door_state, statuses, summarize_interlocks(readings), assessment, code,
                          previous_state, (prev.sequence_no + 1) if prev else 1, changed)
