"""Stage 9: acknowledgements.

Delay and the missing-acknowledgement outcome follow documented rules, driven by rule-derived workload features only
(severity, shift, alarms raised on the plant in the previous 10 min, repeats of the same alarm in the previous 30 min).
No scenario labels are consulted. Delay and status are derived from timestamps by the alarm rules.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta

import numpy as np

from app.business_rules.alarm_rules import ack_delay_seconds, ack_status

from ..common import ack_id, rng_for

MEDIAN_DELAY = {"CRITICAL": 30, "HIGH": 120, "MEDIUM": 420, "LOW": 1500}   # seconds
P_UNACK = {"CRITICAL": 0.005, "HIGH": 0.02, "MEDIUM": 0.06, "LOW": 0.12}
SHIFT_FACTOR = {"A": 1.0, "B": 1.1, "C": 1.3}                                # night shift is slower
LOAD_WINDOW = timedelta(minutes=10)
REPEAT_WINDOW = timedelta(minutes=30)


def build_acks(cfg, master, shifts, alarms: list[dict]) -> list[dict]:
    rng = rng_for(cfg.seed, "acks")
    plant_of = {m["machine_id"]: m["plant_id"] for m in master.machines}
    plant_times = defaultdict(list)
    key_times = defaultdict(list)
    for a in alarms:
        plant_times[plant_of[a["machine_id"]]].append(a["raised_at"])
        key_times[(a["machine_id"], a["alarm_definition_id"])].append(a["raised_at"])
    for v in plant_times.values():
        v.sort()
    for v in key_times.values():
        v.sort()

    out = []
    for a in sorted(alarms, key=lambda r: r["alarm_event_id"]):
        u, z, m = rng.random(), rng.normal(), rng.random()  # fixed draws per alarm keeps the stream stable
        mid, t, sev = a["machine_id"], a["raised_at"], a["severity"]
        pt = plant_times[plant_of[mid]]
        load = bisect_right(pt, t) - bisect_left(pt, t - LOAD_WINDOW) - 1
        kt = key_times[(mid, a["alarm_definition_id"])]
        repeats = max(0, bisect_right(kt, t) - bisect_left(kt, t - REPEAT_WINDOW) - 1)
        shift_code = (a["shift_id"] or "-A-01").split("-")[-2]
        p_un = P_UNACK[sev] * (1 + load / 10) * (1 + 0.3 * min(repeats, 6))
        if sev == "LOW" and a["cleared_at"] and (a["cleared_at"] - t).total_seconds() <= 30:
            p_un *= 3  # self-clearing blip nobody bothers to acknowledge
        if u < min(p_un, 0.7):
            continue
        mult = SHIFT_FACTOR.get(shift_code, 1.0) * (1 + load / 8) * (1 + 0.5 * min(repeats, 6))
        delay = MEDIAN_DELAY[sev] * mult * float(np.exp(0.9 * z))
        ack_at = (t + timedelta(seconds=max(1.0, delay))).replace(microsecond=0)
        if ack_at >= cfg.end:
            continue  # not yet acknowledged at data cut-off
        out.append(dict(
            acknowledgement_id=ack_id(a["alarm_event_id"]), alarm_event_id=a["alarm_event_id"],
            acknowledged_by_shift_id=shifts.shift_for(plant_of[mid], mid, ack_at), acknowledged_at=ack_at,
            ack_method="HMI" if m < 0.7 else ("SCADA" if m < 0.9 else "MOBILE"),
            ack_delay_seconds=ack_delay_seconds(t, ack_at),
            ack_status=ack_status(ack_at, a["ack_deadline_at"]), comment=None))
    return out
