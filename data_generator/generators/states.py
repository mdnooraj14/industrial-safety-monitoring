"""Stage 7: machine-state and interlock-state event timelines.

Each machine runs a seeded state machine (STOPPED -> IDLE -> RUNNING -> ...). Door/interlock context changes are
emitted as additional events. Every event's safety assessment is DERIVED by app.business_rules.safety_state, and every
transition is checked against the allowed graph, so the baseline never contains invalid transitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from app.business_rules.safety_state import (
    DOOR_TYPES, InterlockReading, assess_state, is_valid_transition, summarize_interlocks,
)

from ..common import rng_for

DURATION = {  # (median seconds, sigma, min, max)
    "STOPPED": (600, 0.8, 30, 3600), "IDLE": (480, 0.8, 30, 3600), "RUNNING": (5400, 0.7, 600, 21600),
    "FAULT": (1200, 0.8, 60, 7200), "E_STOPPED": (300, 0.6, 60, 1800), "MAINTENANCE": (7200, 0.5, 900, 28800),
}
NEXT = {
    "RUNNING": (["IDLE", "STOPPED", "FAULT", "E_STOPPED"], [0.72, 0.14, 0.09, 0.05]),
    "IDLE": (["RUNNING", "STOPPED"], [0.80, 0.20]),
    "STOPPED": (["IDLE", "MAINTENANCE"], [0.90, 0.10]),
    "FAULT": (["STOPPED"], [1.0]), "E_STOPPED": (["STOPPED"], [1.0]), "MAINTENANCE": (["STOPPED"], [1.0]),
}
DOOR_OPEN_PROB = {"STOPPED": 0.30, "IDLE": 0.30, "MAINTENANCE": 1.0}


@dataclass
class StateResult:
    machine_states: list[dict] = field(default_factory=list)
    interlock_states: list[dict] = field(default_factory=list)


class _MachineSim:
    def __init__(self, cfg, machine, interlocks, shifts, rng, unsafe_prob, rows: StateResult):
        self.cfg, self.m, self.shifts, self.rng, self.rows = cfg, machine, shifts, rng, rows
        self.unsafe_prob = unsafe_prob
        self.il = interlocks
        self.il_by_id = {i["interlock_id"]: i for i in interlocks}
        self.status = {i["interlock_id"]: "ENGAGED" for i in interlocks}
        self.door_types = [i["interlock_id"] for i in interlocks if i["interlock_type"] in DOOR_TYPES]
        self.estop = [i["interlock_id"] for i in interlocks if i["interlock_type"] == "E_STOP"]
        self.has_door = bool(self.door_types)
        self.state: str | None = None
        self.door = "CLOSED" if self.has_door else "NOT_APPLICABLE"
        self.seq = 0
        self.iseq = {i["interlock_id"]: 0 for i in interlocks}
        self.last_ts: datetime | None = None

    # ---- event emission -------------------------------------------------
    def _readings(self, status: dict, door: str) -> list[InterlockReading]:
        out = []
        for i in self.il:
            iid = i["interlock_id"]
            obs = (("OPEN" if door == "OPEN" else "CLOSED") if i["interlock_type"] in DOOR_TYPES else None)
            out.append(InterlockReading(i["interlock_type"], status[iid], i["bypass_allowed"], obs, i["is_required"]))
        return out

    def emit(self, ts: datetime, state: str, door: str | None = None, changes: dict | None = None,
             reason: str | None = None) -> datetime:
        if self.last_ts is not None and ts <= self.last_ts:
            ts = self.last_ts + timedelta(seconds=1)
        ts = ts.replace(microsecond=0)
        door = door or self.door
        status = dict(self.status)
        status.update(changes or {})
        first = self.state is None
        if not first and state == self.state and door == self.door and status == self.status:
            return self.last_ts
        if not is_valid_transition(self.state, state):
            raise AssertionError(f"illegal transition {self.state}->{state} on {self.m['machine_id']}")
        readings = self._readings(status, door)
        assessment, code = assess_state(state, door, readings)
        self.seq += 1
        mid = self.m["machine_id"]
        mse_id = f"MSE-{mid}-{self.seq:06d}"
        self.rows.machine_states.append(dict(
            machine_state_event_id=mse_id, machine_id=mid, sequence_no=self.seq, event_ts=ts,
            previous_state=self.state, machine_state=state, door_state=door,
            interlock_status_summary=summarize_interlocks(readings), safety_assessment=assessment,
            unsafe_rule_code=code, shift_id=self.shifts.shift_for(self.m["plant_id"], mid, ts),
            transition_reason=reason))
        for i in self.il:
            iid = i["interlock_id"]
            if first or status[iid] != self.status[iid] or (i["interlock_type"] in DOOR_TYPES and door != self.door):
                self.iseq[iid] += 1
                obs = (("OPEN" if door == "OPEN" else "CLOSED") if i["interlock_type"] in DOOR_TYPES else None)
                self.rows.interlock_states.append(dict(
                    interlock_state_event_id=f"ISE-{iid}-{self.iseq[iid]:06d}", interlock_id=iid,
                    sequence_no=self.iseq[iid], event_ts=ts, interlock_status=status[iid],
                    door_state_observed=obs, machine_state_event_id=mse_id))
        self.state, self.door, self.status, self.last_ts = state, door, status, ts
        return ts

    # ---- helpers --------------------------------------------------------
    def _door_changes(self, opened: bool) -> dict:
        return {iid: ("DISENGAGED" if opened else "ENGAGED") for iid in self.door_types}

    def _duration(self, state: str) -> timedelta:
        med, sigma, lo, hi = DURATION[state]
        return timedelta(seconds=float(np.clip(med * np.exp(sigma * self.rng.normal()), lo, hi)))

    def _next_state(self, state: str) -> str:
        opts, p = NEXT[state]
        return str(self.rng.choice(opts, p=p))

    def _estop_changes(self, entering: bool) -> dict:
        return {iid: ("DISENGAGED" if entering else "ENGAGED") for iid in self.estop}

    # ---- segments -------------------------------------------------------
    def _idle_door(self, state: str, t: datetime, seg_end: datetime) -> None:
        if not self.has_door or (seg_end - t).total_seconds() < 120:
            return
        if self.rng.random() >= DOOR_OPEN_PROB[state]:
            return
        span = (seg_end - t).total_seconds()
        open_t = t + timedelta(seconds=float(self.rng.uniform(0.05, 0.3) * span))
        close_t = seg_end - timedelta(seconds=float(self.rng.uniform(5, 20)))
        self.emit(open_t, state, "OPEN", self._door_changes(True), "door opened for access")
        self.emit(close_t, state, "CLOSED", self._door_changes(False), "door closed")

    def _pick_excursion(self):
        cands, w = [], []
        required = [i for i in self.il if i["is_required"]]
        if self.has_door:
            cands.append("R-U1"); w.append(0.8 if self.m["machine_id"] in self.cfg._problem else 0.6)
        non_door = [i["interlock_id"] for i in required if i["interlock_type"] not in DOOR_TYPES]
        if non_door:
            cands.append("R-U2"); w.append(0.25)
        byp = [i["interlock_id"] for i in required if i["bypass_allowed"]]
        if byp:
            cands.append("R-U3"); w.append(0.15)
        if not cands:
            return None, None
        rule = str(self.rng.choice(cands, p=np.array(w) / sum(w)))
        if rule == "R-U1":
            return rule, None
        pool = non_door if rule == "R-U2" else byp
        return rule, str(self.rng.choice(pool))

    def _running(self, t: datetime, seg_end: datetime) -> datetime | None:
        """Possibly one unsafe excursion; returns the time of an early E_STOPPED event, if any."""
        span = (seg_end - t).total_seconds()
        if span < 180 or self.rng.random() >= self.unsafe_prob:
            return None
        rule, iid = self._pick_excursion()
        if rule is None:
            return None
        offset = float(self.rng.uniform(60, span - 120))
        dur = float(np.clip(90 * np.exp(1.0 * self.rng.normal()), 8, min(1200, span - offset - 30)))
        t0 = t + timedelta(seconds=offset)
        if rule == "R-U1":
            self.emit(t0, "RUNNING", "OPEN", self._door_changes(True), "door opened while running")
        elif rule == "R-U2":
            self.emit(t0, "RUNNING", None, {iid: str(self.rng.choice(["DISENGAGED", "FAULT"]))},
                      f"interlock {iid} inactive while running")
        else:
            self.emit(t0, "RUNNING", None, {iid: "BYPASSED"}, f"interlock {iid} bypassed while running")
        t1 = t0 + timedelta(seconds=dur)
        restore = {i["interlock_id"]: "ENGAGED" for i in self.il}
        if self.rng.random() < 0.30:  # operator hits the e-stop
            restore.update(self._estop_changes(True))
            return self.emit(t1, "E_STOPPED", "CLOSED" if self.has_door else None, restore, "e-stop after excursion")
        self.emit(t1, "RUNNING", "CLOSED" if self.has_door else None, restore, "excursion cleared")
        return None

    def run(self) -> None:
        end, t = self.cfg.end, self.cfg.start
        self.emit(t, "STOPPED", reason="initial state")
        state = "STOPPED"
        while t < end:
            seg_end = min(t + self._duration(state), end)
            early = None
            if state == "RUNNING":
                early = self._running(t, seg_end)
            elif state in DOOR_OPEN_PROB:
                self._idle_door(state, t, seg_end)
            if early is not None:
                t, state = early, "E_STOPPED"
                continue
            if seg_end >= end:
                break
            nxt = self._next_state(state)
            changes = {}
            if nxt == "E_STOPPED":
                changes = self._estop_changes(True)
            elif state == "E_STOPPED":
                changes = self._estop_changes(False)
            t = self.emit(seg_end, nxt, changes=changes, reason=f"{state} -> {nxt}")
            state = nxt


def simulate_states(cfg, master, shifts) -> StateResult:
    rows = StateResult()
    object.__setattr__(cfg, "_problem", master.problem_machines)  # read-only helper for excursion weighting
    for idx, machine in enumerate(master.machines):
        rng = rng_for(cfg.seed, "states", idx)
        il = [i for i in master.interlocks if i["machine_id"] == machine["machine_id"]]
        prob = cfg.problem_unsafe_prob if machine["machine_id"] in master.problem_machines else cfg.base_unsafe_prob
        _MachineSim(cfg, machine, il, shifts, rng, prob, rows).run()
    return rows
