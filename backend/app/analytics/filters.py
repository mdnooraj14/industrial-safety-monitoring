"""Filters (plant, machine, severity, date, incident status) applied to the feature tables. Pure functions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping, Sequence

Tables = Mapping[str, Sequence[Mapping]]


@dataclass(frozen=True)
class Filters:
    plant_id: str | None = None
    machine_id: str | None = None
    severity: str | None = None          # alarm severity; also incident severity
    date_from: date | None = None        # inclusive, UTC calendar day
    date_to: date | None = None          # inclusive, UTC calendar day
    incident_status: str | None = None

    def is_empty(self) -> bool:
        return not any((self.plant_id, self.machine_id, self.severity, self.date_from, self.date_to,
                        self.incident_status))

    def as_dict(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in self.__dict__.items() if v}


def _bounds(f: Filters) -> tuple[datetime | None, datetime | None]:
    lo = datetime.combine(f.date_from, time.min, tzinfo=timezone.utc) if f.date_from else None
    hi = (datetime.combine(f.date_to, time.min, tzinfo=timezone.utc) + timedelta(days=1)) if f.date_to else None
    return lo, hi


def apply_filters(tables: Tables, f: Filters | None) -> dict[str, list]:
    out = {k: list(v) for k, v in tables.items()}
    if f is None or f.is_empty():
        return out
    lo, hi = _bounds(f)

    def in_range(ts) -> bool:
        return (lo is None or ts >= lo) and (hi is None or ts < hi)

    machines = [m for m in out["machine"] if (not f.plant_id or m["plant_id"] == f.plant_id)
                and (not f.machine_id or m["machine_id"] == f.machine_id)]
    mids = {m["machine_id"] for m in machines}
    out["machine"] = machines
    out["plant"] = [p for p in out["plant"] if p["plant_id"] in {m["plant_id"] for m in machines}]
    out["alarm_event"] = [a for a in out["alarm_event"] if a["machine_id"] in mids
                          and (not f.severity or a["severity"] == f.severity) and in_range(a["raised_at"])]
    alarm_ids = {a["alarm_event_id"] for a in out["alarm_event"]}
    out["acknowledgement"] = [k for k in out["acknowledgement"] if k["alarm_event_id"] in alarm_ids]
    out["machine_state_event"] = [s for s in out["machine_state_event"] if s["machine_id"] in mids and in_range(s["event_ts"])]
    state_ids = {s["machine_state_event_id"] for s in out["machine_state_event"]}
    out["interlock"] = [i for i in out["interlock"] if i["machine_id"] in mids]
    il_ids = {i["interlock_id"] for i in out["interlock"]}
    out["interlock_state_event"] = [e for e in out["interlock_state_event"]
                                    if e["interlock_id"] in il_ids and e["machine_state_event_id"] in state_ids]
    out["incident"] = [i for i in out["incident"] if i["machine_id"] in mids
                       and (not f.severity or i["severity"] == f.severity)
                       and (not f.incident_status or i["status"] == f.incident_status) and in_range(i["opened_at"])]
    inc_ids = {i["incident_id"] for i in out["incident"]}
    out["incident_alarm_link"] = [l for l in out["incident_alarm_link"] if l["incident_id"] in inc_ids]
    out["incident_status_event"] = [e for e in out["incident_status_event"] if e["incident_id"] in inc_ids]
    out["corrective_action"] = [a for a in out["corrective_action"] if a["incident_id"] in inc_ids]
    act_ids = {a["corrective_action_id"] for a in out["corrective_action"]}
    out["corrective_action_status_event"] = [e for e in out["corrective_action_status_event"]
                                             if e["corrective_action_id"] in act_ids]
    return out
