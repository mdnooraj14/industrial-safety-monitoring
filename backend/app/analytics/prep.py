"""Prepare the analysis view of the data: cut-off, replay duplicates and invalid acknowledgements are set aside.

Business KPIs must not be distorted by data-quality defects, so (a) re-ingested replay duplicates of alarms and
(b) acknowledgements dated before their alarm are excluded from KPI maths and counted separately instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from app.validation.anomaly_detectors import detect_replay_duplicates

Tables = Mapping[str, Sequence[Mapping]]


def infer_cutoff(tables: Tables) -> datetime:
    """Snapshot time of the data: the latest event timestamp rounded UP to the next whole hour.

    Pass an explicit cut-off when it is known (e.g. the generation window end); this is a fallback.
    """
    latest = None
    for table, col in (("alarm_event", "raised_at"), ("acknowledgement", "acknowledged_at"),
                       ("machine_state_event", "event_ts"), ("incident_status_event", "changed_at"),
                       ("corrective_action_status_event", "changed_at")):
        for r in tables.get(table, []):
            if latest is None or r[col] > latest:
                latest = r[col]
    if latest is None:
        raise ValueError("cannot infer a cut-off from empty data")
    latest = latest.astimezone(timezone.utc)        # round in UTC so the result does not depend on the session time zone
    floor = latest.replace(minute=0, second=0, microsecond=0)
    return floor if floor == latest else floor + timedelta(hours=1)


@dataclass
class Prepared:
    tables: Tables
    cutoff: datetime
    alarms: list[Mapping]                       # replay duplicates removed
    duplicate_alarm_ids: set[str]
    ack_by_alarm: dict[str, Mapping]            # valid acknowledgements only
    invalid_ack_ids: list[str] = field(default_factory=list)


def prepare(tables: Tables, cutoff: datetime | None = None) -> Prepared:
    cutoff = cutoff or infer_cutoff(tables)
    dup = {f.record_id for f in detect_replay_duplicates(tables["alarm_event"])}
    alarms = [a for a in tables["alarm_event"] if a["alarm_event_id"] not in dup]
    by_id = {a["alarm_event_id"]: a for a in alarms}
    valid, invalid = {}, []
    for k in tables["acknowledgement"]:
        a = by_id.get(k["alarm_event_id"])
        if a is None:
            continue
        if k["acknowledged_at"] < a["raised_at"]:
            invalid.append(k["acknowledgement_id"])
        else:
            valid[k["alarm_event_id"]] = k
    return Prepared(tables, cutoff, alarms, dup, valid, sorted(invalid))
