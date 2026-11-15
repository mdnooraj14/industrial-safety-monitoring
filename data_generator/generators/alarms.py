"""Stage 8: alarm events.

Sources, in order: (A) state-driven alarms from unsafe episodes / faults / e-stops, (B) alarm floods, (C) nuisance
clusters, (D) background process/comm/maintenance/interlock-fault alarms that fill the budget exactly.
Severity is derived by app.business_rules.alarm_rules.classify_severity; deadlines by the severity policy.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from app.business_rules.alarm_rules import ack_deadline_at, classify_severity
from app.business_rules.incident_rules import find_unsafe_episodes

from ..common import alarm_id, rng_for

CLEAR_MEDIAN_SECONDS = {"CRITICAL": 480, "HIGH": 720, "MEDIUM": 1200, "LOW": 1800}
RULE_TO_CODE = {"R-U1": "DOOR-OPEN-RUN", "R-U2": "INTERLOCK-INACTIVE-RUN", "R-U3": "INTERLOCK-BYPASS-RUN"}
HOUR_WEIGHTS = np.array([0.6] * 6 + [1.2] * 16 + [0.6] * 2)
HOUR_WEIGHTS = HOUR_WEIGHTS / HOUR_WEIGHTS.sum()
DAILY_CAP = 12  # max background alarms per (machine, definition, day)


@dataclass
class AlarmResult:
    alarms: list[dict] = field(default_factory=list)
    scenarios: dict[str, dict] = field(default_factory=dict)  # group_id -> {issue_type, alarm_ids}
    counts: dict[str, int] = field(default_factory=dict)


class _Builder:
    def __init__(self, cfg, master, shifts, states):
        self.cfg, self.master, self.shifts = cfg, master, shifts
        self.machines = {m["machine_id"]: m for m in master.machines}
        self.defs = [d for d in master.alarm_defs if d["lifecycle_status"] == "ACTIVE"]
        self.def_by_code = {d["alarm_code"]: d for d in master.alarm_defs}
        self.zone_risk = {z["safety_zone_id"]: z["risk_level"] for z in master.zones}
        self.primary_zone = {r["machine_id"]: r["safety_zone_id"] for r in master.machine_zone if r["is_primary"]}
        self.il_by_machine = defaultdict(list)
        for i in master.interlocks:
            self.il_by_machine[i["machine_id"]].append(i)
        self.states = states
        self.mse_ts: dict[str, list[datetime]] = defaultdict(list)
        self.mse_rows: dict[str, list[dict]] = defaultdict(list)
        for r in sorted(states.machine_states, key=lambda r: (r["machine_id"], r["sequence_no"])):
            self.mse_ts[r["machine_id"]].append(r["event_ts"])
            self.mse_rows[r["machine_id"]].append(r)
        self.ise_by_mse: dict[str, list[dict]] = defaultdict(list)
        for r in states.interlock_states:
            self.ise_by_mse[r["machine_state_event_id"]].append(r)
        self.rows: list[dict] = []

    # ---- applicable definitions ----------------------------------------
    def applicable(self, machine_id: str, trigger: str | None = None) -> list[dict]:
        mt = self.machines[machine_id]["machine_type"]
        types = {i["interlock_type"] for i in self.il_by_machine[machine_id]}
        out = []
        for d in self.defs:
            if d["trigger_type"] == "SAFETY_STATE":
                continue
            if trigger and d["trigger_type"] != trigger:
                continue
            if d["machine_type"] not in (None, mt):
                continue
            if d["trigger_type"] == "INTERLOCK_FAULT" and d["interlock_type"] not in types:
                continue
            out.append(d)
        return out

    def interlock_for(self, machine_id: str, itype: str | None, rng) -> dict | None:
        c = [i for i in self.il_by_machine[machine_id] if i["interlock_type"] == itype]
        return c[int(rng.integers(len(c)))] if c else None

    # ---- row construction -----------------------------------------------
    def make(self, machine_id: str, d: dict, raised_at: datetime, rng, interlock: dict | None = None,
             clear_after: timedelta | None = None, scenario: tuple[str, str] | None = None) -> dict:
        cfg = self.cfg
        raised_at = min(max(raised_at.replace(microsecond=0), cfg.start), cfg.end - timedelta(seconds=1))
        i = bisect_right(self.mse_ts[machine_id], raised_at) - 1
        st = self.mse_rows[machine_id][max(i, 0)]
        if d["trigger_type"] == "INTERLOCK_FAULT" and interlock is None:
            interlock = self.interlock_for(machine_id, d["interlock_type"], rng)
        safety = d["is_safety_related"]
        zone = None
        if safety:
            zone = (interlock or {}).get("safety_zone_id") or self.primary_zone.get(machine_id)
        sev = classify_severity(d["default_severity"], st["safety_assessment"], safety, self.zone_risk.get(zone))
        if clear_after is None:
            med = CLEAR_MEDIAN_SECONDS[sev]
            clear_after = timedelta(seconds=float(np.clip(med * np.exp(rng.normal()), 15, 6 * 3600)))
        cleared = (raised_at + clear_after).replace(microsecond=0)
        if cleared <= raised_at:
            cleared = raised_at + timedelta(seconds=1)
        if cleared >= cfg.end:
            cleared = None
        row = dict(
            alarm_event_id=None, machine_id=machine_id, alarm_definition_id=d["alarm_definition_id"],
            interlock_id=interlock["interlock_id"] if interlock else None, safety_zone_id=zone,
            machine_state_event_id=st["machine_state_event_id"],
            shift_id=self.shifts.shift_for(self.machines[machine_id]["plant_id"], machine_id, raised_at),
            severity=sev, raised_at=raised_at, cleared_at=cleared,
            ack_deadline_at=ack_deadline_at(raised_at, sev),
            lifecycle_status="CLEARED" if cleared else "ACTIVE",
            ingest_batch_id=f"BATCH-{raised_at:%Y%m%d}-01", _scenario=scenario)
        self.rows.append(row)
        return row


def _seconds(rng, lo: float, hi: float) -> timedelta:
    return timedelta(seconds=float(rng.uniform(lo, hi)))


def _state_driven(b: _Builder, rng) -> int:
    n0 = len(b.rows)
    episodes = find_unsafe_episodes(b.states.machine_states, b.cfg.end)
    for ep in episodes:
        d = b.def_by_code[RULE_TO_CODE[ep.rule_code]]
        ises = [x for x in b.ise_by_mse[ep.start_event_id] if x["interlock_status"] != "ENGAGED"]
        il_id = ises[0]["interlock_id"] if ises else None
        il = next((i for i in b.il_by_machine[ep.machine_id] if i["interlock_id"] == il_id), None)
        clear = (ep.end_ts - ep.start_ts) + _seconds(rng, 0, 60)
        b.make(ep.machine_id, d, ep.start_ts + _seconds(rng, 1, 4), rng, il, clear_after=clear)
        if il and rng.random() < 0.6:
            cands = [x for x in b.defs if x["trigger_type"] == "INTERLOCK_FAULT" and x["interlock_type"] == il["interlock_type"]]
            if cands:
                b.make(ep.machine_id, cands[int(rng.integers(len(cands)))], ep.start_ts + _seconds(rng, 2, 10), rng, il,
                       clear_after=clear)
    for mid, rows in b.mse_rows.items():
        for r in rows:
            if r["previous_state"] == r["machine_state"]:
                continue
            if r["machine_state"] == "FAULT":
                pool = b.applicable(mid, "PROCESS_LIMIT")
                if pool:
                    b.make(mid, pool[int(rng.integers(len(pool)))], r["event_ts"] + _seconds(rng, 1, 5), rng)
                    if rng.random() < 0.4:
                        b.make(mid, pool[int(rng.integers(len(pool)))], r["event_ts"] + _seconds(rng, 5, 60), rng)
            elif r["machine_state"] == "E_STOPPED":
                pool = [x for x in b.applicable(mid, "INTERLOCK_FAULT") if x["interlock_type"] == "E_STOP"]
                if pool:
                    b.make(mid, pool[int(rng.integers(len(pool)))], r["event_ts"] + _seconds(rng, 1, 3), rng)
    return len(b.rows) - n0


def _floods(b: _Builder, rng) -> int:
    n0, cfg = len(b.rows), b.cfg
    anchors = [(mid, r["event_ts"]) for mid, rows in b.mse_rows.items() for r in rows
               if r["machine_state"] in ("FAULT", "E_STOPPED") and r["previous_state"] != r["machine_state"]
               and r["event_ts"] <= cfg.end - timedelta(minutes=20)]
    anchors.sort(key=lambda a: (a[1], a[0]))
    chosen: list[tuple[str, datetime]] = []
    if anchors:
        order = rng.permutation(len(anchors))
        used = set()
        for k in order:
            if len(chosen) >= cfg.n_floods:
                break
            if anchors[k][0] in used and len(used) < len(b.machines):
                continue
            used.add(anchors[k][0])
            chosen.append(anchors[k])
    machines = sorted(b.machines)
    while len(chosen) < cfg.n_floods:
        chosen.append((machines[int(rng.integers(len(machines)))],
                       cfg.start + timedelta(seconds=float(rng.uniform(3600, (cfg.days * 86400) - 1800)))))
    for n, (mid, t0) in enumerate(sorted(chosen, key=lambda a: (a[1], a[0])), start=1):
        pool = b.applicable(mid)
        k = min(len(pool), int(rng.integers(6, 11)))
        pool = [pool[j] for j in rng.permutation(len(pool))[:k]]
        w = 1.0 / np.arange(1, k + 1) ** 0.8
        total = int(rng.integers(18, 61))
        times = np.sort(rng.uniform(0, 480, total))
        for s in times:
            d = pool[int(rng.choice(k, p=w / w.sum()))]
            b.make(mid, d, t0 + timedelta(seconds=float(s)), rng, scenario=("FLOOD", f"FLOOD-{n:04d}"))
    return len(b.rows) - n0


def _nuisance(b: _Builder, rng) -> int:
    n0, cfg = len(b.rows), b.cfg
    machines = sorted(b.machines)
    pairs, seen = [], set()
    attempts = 0
    while len(pairs) < cfg.n_nuisance_clusters and attempts < 10_000:
        attempts += 1
        mid = machines[int(rng.integers(len(machines)))]
        pool = [d for d in b.applicable(mid) if d["trigger_type"] in ("PROCESS_LIMIT", "COMMUNICATION")
                and d["default_severity"] in ("LOW", "MEDIUM")]
        if not pool:
            continue
        d = pool[int(rng.integers(len(pool)))]
        if (mid, d["alarm_definition_id"]) in seen:
            continue
        seen.add((mid, d["alarm_definition_id"]))
        pairs.append((mid, d))
    for n, (mid, d) in enumerate(pairs, start=1):
        scen = ("NUISANCE_CLUSTER", f"NUIS-{n:04d}")
        if n % 3 == 0:  # N2: repeating low-value alarm, >= 20 in one day
            day = int(rng.integers(0, max(1, cfg.days - 1)))
            for s in np.sort(rng.uniform(0, 20 * 3600, int(rng.integers(20, 31)))):
                b.make(mid, d, cfg.start + timedelta(days=day, seconds=float(s)), rng, scenario=scen,
                       clear_after=_seconds(rng, 15, 100))
        else:  # N1: chattering bursts on several days
            ndays = min(cfg.days - 1, int(rng.integers(3, 7)))
            for day in rng.permutation(max(1, cfg.days - 1))[:ndays]:
                t0 = cfg.start + timedelta(days=int(day), seconds=float(rng.uniform(0, 22 * 3600)))
                for s in np.sort(rng.uniform(0, 1500, int(rng.integers(5, 10)))):
                    b.make(mid, d, t0 + timedelta(seconds=float(s)), rng, scenario=scen,
                           clear_after=_seconds(rng, 10, 90))
    return len(b.rows) - n0


def _background(b: _Builder, rng, n: int) -> None:
    cfg = b.cfg
    machines = sorted(b.machines)
    noise = rng.lognormal(0, 0.6, len(machines))
    pm = noise / noise.sum()
    mids = rng.choice(len(machines), size=n, p=pm)
    days = rng.integers(0, cfg.days, size=n)
    hours = rng.choice(24, size=n, p=HOUR_WEIGHTS)
    secs = rng.uniform(0, 3600, size=n)
    cats = rng.choice(["PROCESS_LIMIT", "COMMUNICATION", "MAINTENANCE_DUE", "INTERLOCK_FAULT"], size=n,
                      p=[0.55, 0.22, 0.11, 0.12])
    cache: dict[tuple[str, str], tuple[list[dict], np.ndarray]] = {}
    per_day: dict[tuple[str, str, int], int] = defaultdict(int)
    for k in range(n):
        mid = machines[int(mids[k])]
        cat = str(cats[k])
        key = (mid, cat)
        if key not in cache:
            pool = b.applicable(mid, cat) or b.applicable(mid, "PROCESS_LIMIT")
            w = 1.0 / np.arange(1, len(pool) + 1) ** 0.8
            cache[key] = (pool, w / w.sum())
        pool, w = cache[key]
        for _ in range(12):  # keep natural volume of one definition below the N2 nuisance threshold (20/day)
            d = pool[int(rng.choice(len(pool), p=w))]
            if per_day[(mid, d["alarm_definition_id"], int(days[k]))] < DAILY_CAP:
                break
        per_day[(mid, d["alarm_definition_id"], int(days[k]))] += 1
        t = cfg.start + timedelta(days=int(days[k]), hours=int(hours[k]), seconds=float(secs[k]))
        b.make(mid, d, t, rng)


def build_alarms(cfg, master, shifts, states) -> AlarmResult:
    rng = rng_for(cfg.seed, "alarms")
    b = _Builder(cfg, master, shifts, states)
    counts = {"state_driven": _state_driven(b, rng), "flood": _floods(b, rng), "nuisance": _nuisance(b, rng)}
    n_bg = cfg.n_alarm_events - len(b.rows)
    if n_bg < 0:
        raise ValueError(f"scenario alarms ({len(b.rows)}) exceed n_alarm_events ({cfg.n_alarm_events}); "
                         "reduce floods / nuisance clusters or raise the target")
    _background(b, rng, n_bg)
    counts["background"] = n_bg
    # deterministic IDs: per machine, ordered by raised_at then definition then creation order
    by_machine: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for idx, r in enumerate(b.rows):
        by_machine[r["machine_id"]].append((idx, r))
    res = AlarmResult(counts=counts)
    for mid in sorted(by_machine):
        for n, (_, r) in enumerate(sorted(by_machine[mid], key=lambda x: (x[1]["raised_at"],
                                                                         x[1]["alarm_definition_id"], x[0])), start=1):
            r["alarm_event_id"] = alarm_id(mid, n)
    for r in b.rows:
        sc = r.pop("_scenario")
        if sc:
            g = res.scenarios.setdefault(sc[1], dict(issue_type=sc[0], alarm_ids=[]))
            g["alarm_ids"].append(r["alarm_event_id"])
    res.alarms = sorted(b.rows, key=lambda r: (r["machine_id"], r["alarm_event_id"]))
    return res
