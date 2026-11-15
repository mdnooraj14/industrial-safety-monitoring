"""Unsafe episodes, incident creation (IN-1, IN-2) and repeated-unsafe detection (IN-5). Pure functions."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from .config import DEFAULT_RULES, RulesConfig


@dataclass
class UnsafeEpisode:
    machine_id: str
    rule_code: str
    start_event_id: str
    start_ts: datetime
    end_ts: datetime
    event_ids: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.end_ts - self.start_ts).total_seconds()


@dataclass
class IncidentCandidate:
    episode: UnsafeEpisode
    trigger_alarm_id: str
    linked_alarm_ids: list[str]  # trigger first
    severity: str


def find_unsafe_episodes(events: Iterable[Mapping], data_end: datetime) -> list[UnsafeEpisode]:
    """Maximal runs of consecutive UNSAFE state events per machine.

    `events` need keys: machine_id, machine_state_event_id, event_ts, safety_assessment, unsafe_rule_code
    (any order). The episode ends at the next non-UNSAFE event, else at `data_end`.
    """
    by_machine: dict[str, list[Mapping]] = defaultdict(list)
    for e in events:
        by_machine[e["machine_id"]].append(e)
    episodes: list[UnsafeEpisode] = []
    for machine_id, evs in by_machine.items():
        evs.sort(key=lambda e: (e["event_ts"], e["machine_state_event_id"]))
        current: UnsafeEpisode | None = None
        for e in evs:
            if e["safety_assessment"] == "UNSAFE":
                if current is None:
                    current = UnsafeEpisode(machine_id, e["unsafe_rule_code"], e["machine_state_event_id"],
                                            e["event_ts"], data_end, [e["machine_state_event_id"]])
                else:
                    current.event_ids.append(e["machine_state_event_id"])
            elif current is not None:
                current.end_ts = e["event_ts"]
                episodes.append(current)
                current = None
        if current is not None:
            episodes.append(current)
    episodes.sort(key=lambda ep: (ep.start_ts, ep.machine_id))
    return episodes


def incident_severity(linked_severities: Sequence[str]) -> str:
    """IN-2: CRITICAL if any linked alarm is CRITICAL, otherwise HIGH."""
    return "CRITICAL" if "CRITICAL" in linked_severities else "HIGH"


def incident_candidates(episodes: Iterable[UnsafeEpisode], alarms: Iterable[Mapping],
                        cfg: RulesConfig = DEFAULT_RULES) -> list[IncidentCandidate]:
    """IN-1. `alarms` need keys: alarm_event_id, machine_id, severity, raised_at."""
    by_machine: dict[str, list[Mapping]] = defaultdict(list)
    for a in alarms:
        by_machine[a["machine_id"]].append(a)
    for lst in by_machine.values():
        lst.sort(key=lambda a: (a["raised_at"], a["alarm_event_id"]))
    out: list[IncidentCandidate] = []
    lookback = timedelta(seconds=cfg.incident_lookback_seconds)
    for ep in episodes:
        if ep.duration_seconds < cfg.incident_min_unsafe_seconds:
            continue
        window = [a for a in by_machine.get(ep.machine_id, [])
                  if ep.start_ts - lookback <= a["raised_at"] <= ep.end_ts]
        qualifying = [a for a in window if a["severity"] in ("HIGH", "CRITICAL")]
        if not qualifying:
            continue
        trigger = qualifying[0]
        linked = [trigger["alarm_event_id"]] + [a["alarm_event_id"] for a in window
                                                 if a["alarm_event_id"] != trigger["alarm_event_id"]]
        out.append(IncidentCandidate(ep, trigger["alarm_event_id"], linked,
                                     incident_severity([a["severity"] for a in window])))
    return out


def find_repeated_unsafe(episodes: Iterable[UnsafeEpisode], cfg: RulesConfig = DEFAULT_RULES) -> list[list[UnsafeEpisode]]:
    """IN-5: groups of >= N episodes of the same (machine, rule) within a rolling window; one group per pair."""
    by_key: dict[tuple[str, str], list[UnsafeEpisode]] = defaultdict(list)
    for ep in episodes:
        by_key[(ep.machine_id, ep.rule_code)].append(ep)
    window = timedelta(days=cfg.repeated_unsafe_window_days)
    groups: list[list[UnsafeEpisode]] = []
    for key in sorted(by_key):
        eps = sorted(by_key[key], key=lambda e: e.start_ts)
        member: set[int] = set()
        for i in range(len(eps)):
            j = i
            while j < len(eps) and eps[j].start_ts - eps[i].start_ts <= window:
                j += 1
            if j - i >= cfg.repeated_unsafe_count:
                member.update(range(i, j))
        if member:
            groups.append([eps[k] for k in sorted(member)])
    return groups
