"""Replay machine-state history against interlock history and re-derive every state event with the rules engine.

Shared by the anomaly detectors and the validator. Uses only business rules; never reads ground truth.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from app.business_rules.safety_state import (
    DOOR_TYPES, InterlockReading, assess_state, is_valid_transition, summarize_interlocks,
)


@dataclass(frozen=True)
class ReplayedState:
    row: Mapping
    assessed: tuple[str, Optional[str]]      # assess_state() alone: R-I1/R-I4, R-U*, or SAFE
    derived: tuple[str, Optional[str]]       # after R-I3 (illegal transition) precedence
    derived_summary: str
    transition_ok: bool

    @property
    def summary_ok(self) -> bool:
        return self.row["interlock_status_summary"] == self.derived_summary


def replay_machine_states(machine_states: Iterable[Mapping], interlock_states: Iterable[Mapping],
                          interlocks: Iterable[Mapping]) -> list[ReplayedState]:
    interlocks = list(interlocks)
    specs = defaultdict(list)
    for i in interlocks:
        specs[i["machine_id"]].append(i)
    machine_of = {i["interlock_id"]: i["machine_id"] for i in interlocks}
    linked = defaultdict(list)
    for e in interlock_states:
        linked[e["machine_state_event_id"]].append(e)
    by_machine = defaultdict(list)
    for m in machine_states:
        by_machine[m["machine_id"]].append(m)
    out: list[ReplayedState] = []
    for mid in sorted(by_machine):
        events = sorted(by_machine[mid], key=lambda e: e["sequence_no"])
        il = specs[mid]
        status = {i["interlock_id"]: "ENGAGED" for i in il}
        prev_state = None
        for ev in events:
            for ie in linked.get(ev["machine_state_event_id"], []):
                if machine_of.get(ie["interlock_id"]) == mid:
                    status[ie["interlock_id"]] = ie["interlock_status"]
            door_open = ev["door_state"] == "OPEN"
            readings = [InterlockReading(i["interlock_type"], status[i["interlock_id"]], i["bypass_allowed"],
                                         (("OPEN" if door_open else "CLOSED") if i["interlock_type"] in DOOR_TYPES else None),
                                         i["is_required"]) for i in il]
            assessed = assess_state(ev["machine_state"], ev["door_state"], readings)
            ok = is_valid_transition(prev_state, ev["machine_state"])
            derived = ("INVALID", "R-I3") if (assessed[0] != "INVALID" and not ok) else assessed
            out.append(ReplayedState(ev, assessed, derived, summarize_interlocks(readings), ok))
            prev_state = ev["machine_state"]
    return out
