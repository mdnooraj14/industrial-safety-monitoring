"""Machine safety-state rules R-U*, R-S*, R-I* and the machine-state transition graph. Pure functions."""
from dataclasses import dataclass
from typing import Iterable, Optional

DOOR_TYPES = frozenset({"DOOR_SWITCH", "GUARD_LOCK"})

# Allowed machine_state transitions. A transition to the *same* state is allowed: it records a change of
# door/interlock context while the operating state is unchanged.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "STOPPED": frozenset({"IDLE", "MAINTENANCE"}),
    "IDLE": frozenset({"RUNNING", "STOPPED"}),
    "RUNNING": frozenset({"IDLE", "STOPPED", "E_STOPPED", "FAULT"}),
    "E_STOPPED": frozenset({"STOPPED"}),
    "FAULT": frozenset({"STOPPED"}),
    "MAINTENANCE": frozenset({"STOPPED"}),
}

UNSAFE_RULES = {
    "R-U1": "DOOR_OPEN_WHILE_RUNNING",
    "R-U2": "REQUIRED_INTERLOCK_INACTIVE",
    "R-U3": "REQUIRED_INTERLOCK_BYPASSED",
}
INVALID_RULES = {
    "R-I1": "DOOR_ENGAGED_BUT_OPEN",
    "R-I2": "SUMMARY_MISMATCH",
    "R-I3": "ILLEGAL_TRANSITION",
    "R-I4": "BYPASS_NOT_ALLOWED",
}


@dataclass(frozen=True)
class InterlockReading:
    """Status of one interlock at an instant."""
    interlock_type: str
    status: str                      # ENGAGED | DISENGAGED | BYPASSED | FAULT
    bypass_allowed: bool = False
    door_observed: Optional[str] = None  # OPEN | CLOSED for door-type interlocks
    required: bool = True


def is_valid_transition(previous: Optional[str], new: str) -> bool:
    if previous is None or previous == new:
        return True
    return new in ALLOWED_TRANSITIONS.get(previous, frozenset())


def summarize_interlocks(readings: Iterable[InterlockReading]) -> str:
    """ALL_ENGAGED / PARTIAL / NONE_ENGAGED over the *required* interlocks."""
    req = [r for r in readings if r.required]
    if not req:
        return "ALL_ENGAGED"
    engaged = sum(1 for r in req if r.status == "ENGAGED")
    if engaged == len(req):
        return "ALL_ENGAGED"
    return "NONE_ENGAGED" if engaged == 0 else "PARTIAL"


def check_summary(stored: str, readings: Iterable[InterlockReading]) -> bool:
    """R-I2: the stored summary must equal the one derived from interlock readings."""
    return stored == summarize_interlocks(readings)


def assess_state(machine_state: str, door_state: str, readings: Iterable[InterlockReading]) -> tuple[str, Optional[str]]:
    """Return (assessment, rule_code). Precedence: INVALID > UNSAFE > SAFE (first matching rule wins)."""
    readings = list(readings)
    for r in readings:
        if r.interlock_type in DOOR_TYPES and r.status == "ENGAGED" and r.door_observed == "OPEN":
            return "INVALID", "R-I1"
    for r in readings:
        if r.status == "BYPASSED" and not r.bypass_allowed:
            return "INVALID", "R-I4"
    if machine_state == "RUNNING":
        req = [r for r in readings if r.required]
        if door_state == "OPEN":
            return "UNSAFE", "R-U1"
        if any(r.status in ("DISENGAGED", "FAULT") for r in req):
            return "UNSAFE", "R-U2"
        if any(r.status == "BYPASSED" for r in req):
            return "UNSAFE", "R-U3"
    return "SAFE", None  # R-S1 / R-S2: not running, or running with everything engaged
