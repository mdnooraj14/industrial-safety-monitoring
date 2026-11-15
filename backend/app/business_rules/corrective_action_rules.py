"""Incident and corrective-action lifecycle rules (IN-3, IN-4, CA-1..CA-5). Pure functions."""
from typing import Iterable, Mapping, Optional

INCIDENT_TRANSITIONS: dict[Optional[str], frozenset[str]] = {
    None: frozenset({"OPEN"}),
    "OPEN": frozenset({"INVESTIGATING"}),
    "INVESTIGATING": frozenset({"ACTION_IN_PROGRESS", "RESOLVED"}),
    "ACTION_IN_PROGRESS": frozenset({"RESOLVED"}),
    "RESOLVED": frozenset({"CLOSED", "ACTION_IN_PROGRESS"}),
    "CLOSED": frozenset(),
}

ACTION_TRANSITIONS: dict[Optional[str], frozenset[str]] = {
    None: frozenset({"PLANNED"}),
    "PLANNED": frozenset({"IN_PROGRESS", "CANCELLED"}),
    "IN_PROGRESS": frozenset({"COMPLETED", "CANCELLED"}),
    "COMPLETED": frozenset({"VERIFIED"}),
    # CLOSED needs a PASSED verification (CA-3). A FAILED-verification action is superseded (CANCELLED) once its
    # follow-up action is planned (CA-4); its verification record is kept.
    "VERIFIED": frozenset({"CLOSED", "CANCELLED"}),
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}

# Probability that verification FAILS, by action type (documented generation parameter, rule CA-4).
VERIFICATION_FAIL_RATE = {
    "REPLACE_INTERLOCK": 0.05, "REPAIR_DOOR_SWITCH": 0.08, "INSTALL_GUARD": 0.05,
    "ADJUST_ALARM_LIMIT": 0.15, "REVISE_PROCEDURE": 0.15, "OPERATOR_TRAINING": 0.25,
}


def valid_incident_transition(prev: Optional[str], new: str) -> bool:
    return new in INCIDENT_TRANSITIONS.get(prev, frozenset())


def valid_action_transition(prev: Optional[str], new: str) -> bool:
    return new in ACTION_TRANSITIONS.get(prev, frozenset())


def action_closed_properly(action: Mapping) -> bool:
    """CA-3: a CLOSED action needs a PASSED verification by a different person, ordered in time."""
    if action["status"] != "CLOSED":
        return True
    return (action.get("verification_result") == "PASSED" and action.get("verified_at") is not None
            and action.get("verified_by_id") not in (None, action["assigned_engineer_id"])
            and action["completed_at"] is not None and action["completed_at"] <= action["verified_at"]
            <= action["closed_at"])


def can_close_incident(actions: Iterable[Mapping]) -> bool:
    """IN-4: >= 1 action and every non-cancelled action is CLOSED and properly verified."""
    live = [a for a in actions if a["status"] != "CANCELLED"]
    return bool(live) and all(a["status"] == "CLOSED" and action_closed_properly(a) for a in live)


def completion_rate(actions: Iterable[Mapping]) -> float:
    """CA-5: status >= COMPLETED among non-cancelled actions."""
    live = [a for a in actions if a["status"] != "CANCELLED"]
    done = [a for a in live if a["status"] in ("COMPLETED", "VERIFIED", "CLOSED")]
    return len(done) / len(live) if live else 0.0


def verification_rate(actions: Iterable[Mapping]) -> float:
    """CA-5: verification_result set among completed actions."""
    done = [a for a in actions if a["status"] in ("COMPLETED", "VERIFIED", "CLOSED")]
    ver = [a for a in done if a.get("verification_result")]
    return len(ver) / len(done) if done else 0.0
