"""Stage 6: operator shifts, shift-machine assignment, and a fast shift lookup. Also people (engineers)."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from faker import Faker

from ..common import rng_for

SHIFT_STARTS = [("A", 6), ("B", 14), ("C", 22)]
SLOTS = 2  # operators per shift


def _fake(seed: int, key: int) -> Faker:
    f = Faker(["en_US", "en_IN"])
    f.seed_instance(seed * 1000 + key)
    return f


@dataclass
class ShiftIndex:
    shifts: list[dict] = field(default_factory=list)
    assignments: list[dict] = field(default_factory=list)
    engineers: dict[str, list[dict]] = field(default_factory=dict)     # plant_id -> engineers
    _starts: dict[str, list[datetime]] = field(default_factory=dict)
    _slots: dict[str, list[dict[int, str]]] = field(default_factory=dict)
    _slot_of_machine: dict[str, int] = field(default_factory=dict)

    def slot_of(self, machine_id: str) -> int:
        return self._slot_of_machine[machine_id]

    def shift_for(self, plant_id: str, machine_id: str, ts: datetime) -> str | None:
        starts = self._starts[plant_id]
        i = bisect_right(starts, ts) - 1
        if i < 0:
            return None
        return self._slots[plant_id][i].get(self._slot_of_machine[machine_id])


def build_shifts(cfg, master) -> ShiftIndex:
    idx = ShiftIndex()
    cutoff = cfg.end
    for pi, plant in enumerate(master.plants):
        pid = plant["plant_id"]
        tz = ZoneInfo(plant["timezone"])
        fake = _fake(cfg.seed, pi + 1)
        operators = [dict(employee_id=f"EMP-1{pi}{n:03d}", name=fake.name()) for n in range(cfg.operators_per_plant)]
        idx.engineers[pid] = (
            [dict(employee_id=f"EMP-2{pi}{n:03d}", name=fake.name()) for n in range(cfg.engineers_per_plant)]
            + [dict(employee_id=f"EMP-3{pi}{n:03d}", name=fake.name(), quality=True) for n in range(2)])
        machines = [m for m in master.machines if m["plant_id"] == pid]
        for k, m in enumerate(machines):
            idx._slot_of_machine[m["machine_id"]] = (k % SLOTS) + 1
        local_start = datetime.fromtimestamp(cfg.start.timestamp(), tz).date() - timedelta(days=1)
        local_end = datetime.fromtimestamp(cutoff.timestamp(), tz).date() + timedelta(days=1)
        starts, slot_maps = [], []
        counter = 0
        d = local_start
        while d <= local_end:
            for code, hour in SHIFT_STARTS:
                naive_start = datetime(d.year, d.month, d.day, hour)
                s_utc = naive_start.replace(tzinfo=tz).astimezone(timezone.utc)
                e_utc = (naive_start + timedelta(hours=8)).replace(tzinfo=tz).astimezone(timezone.utc)
                if e_utc <= cfg.start or s_utc >= cutoff + timedelta(hours=1):
                    continue
                slot_map = {}
                for slot in range(1, SLOTS + 1):
                    op = operators[(counter * SLOTS + slot - 1) % len(operators)]
                    sid = f"SHF-{pid}-{d:%Y%m%d}-{code}-{slot:02d}"
                    idx.shifts.append(dict(
                        shift_id=sid, plant_id=pid, shift_date=d, shift_code=code, shift_start=s_utc, shift_end=e_utc,
                        employee_id=op["employee_id"], operator_name=op["name"], operator_role="OPERATOR",
                        lifecycle_status="COMPLETED" if e_utc <= cutoff else "SCHEDULED"))
                    slot_map[slot] = sid
                    for m in machines:
                        if idx._slot_of_machine[m["machine_id"]] == slot:
                            idx.assignments.append(dict(shift_id=sid, machine_id=m["machine_id"]))
                starts.append(s_utc)
                slot_maps.append(slot_map)
                counter += 1
            d += timedelta(days=1)
        idx._starts[pid] = starts
        idx._slots[pid] = slot_maps
    return idx
