"""Shared helpers: stage-specific RNGs, ID builders, formatting."""
from datetime import date, datetime, timezone

import numpy as np

# Stage ids make every stage's random stream independent and stable (changing one stage never shifts another).
STAGE = {"plants": 1, "machines": 2, "zones": 3, "interlocks": 4, "defs": 5, "shifts": 6, "states": 7,
         "alarms": 8, "acks": 9, "incidents": 10, "people": 11, "anomalies": 12}


def rng_for(seed: int, stage: str, *keys: int) -> np.random.Generator:
    return np.random.default_rng([seed, STAGE[stage], *keys])


def plant_id(n: int) -> str:
    return f"P{n:02d}"


def machine_id(pid: str, n: int) -> str:
    return f"{pid}-M{n:02d}"


def zone_id(pid: str, n: int) -> str:
    return f"SAFE-{pid}-{n:03d}"


def interlock_id(mid: str, n: int) -> str:
    return f"ILK-{mid}-{n:02d}"


def alarm_id(mid: str, n: int) -> str:
    return f"ALM-{mid}-{n:06d}"


def ack_id(aid: str, n: int = 1) -> str:
    return f"ACK-{aid}-{n:02d}"


def incident_id(year: int, n: int) -> str:
    return f"INC-{year}-{n:06d}"


def action_id(inc: str, n: int) -> str:
    return f"ACT-{inc}-{n:02d}"


def fmt_ts(dt: datetime | None) -> str:
    """Deterministic ISO-8601 UTC string, second precision."""
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def fmt_date(d: date | None) -> str:
    return "" if d is None else d.isoformat()
