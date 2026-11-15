"""Alarm pattern rules: flood (AL-5), nuisance clusters (AL-6), repeated alarm patterns (AL-7). Pure functions.

Inputs are plain mappings with: alarm_event_id, machine_id, alarm_definition_id, severity, raised_at, cleared_at.
They never look at ground-truth labels.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

from .config import DEFAULT_RULES, RulesConfig


@dataclass
class FloodEpisode:
    machine_id: str
    start: datetime
    end: datetime
    alarm_ids: list[str] = field(default_factory=list)

    @property
    def alarm_count(self) -> int:
        return len(self.alarm_ids)


@dataclass
class NuisanceCluster:
    machine_id: str
    alarm_definition_id: str
    rules: set[str]
    alarm_ids: list[str]
    first: datetime
    last: datetime


@dataclass
class RepeatedPattern:
    machine_id: str
    alarm_definition_id: str
    max_in_window: int
    alarm_ids: list[str]


def _by_key(alarms: Iterable[Mapping], key) -> dict:
    groups: dict = defaultdict(list)
    for a in alarms:
        groups[key(a)].append(a)
    for lst in groups.values():
        lst.sort(key=lambda a: (a["raised_at"], a["alarm_event_id"]))
    return groups


def detect_floods(alarms: Iterable[Mapping], cfg: RulesConfig = DEFAULT_RULES) -> list[FloodEpisode]:
    """AL-5: >= flood_threshold alarms on one machine within a rolling flood_window; the episode starts at the first
    alarm of the first qualifying window and lasts while the trailing-window count stays >= flood_end_below."""
    window = timedelta(seconds=cfg.flood_window_seconds)
    episodes: list[FloodEpisode] = []
    for machine_id, lst in sorted(_by_key(alarms, lambda a: a["machine_id"]).items()):
        times = [a["raised_at"] for a in lst]
        n = len(lst)
        # trailing count at alarm j = alarms in (t_j - window, t_j]
        left, counts, lefts = 0, [], []
        for j in range(n):
            while times[left] <= times[j] - window:
                left += 1
            counts.append(j - left + 1)
            lefts.append(left)
        j = 0
        while j < n:
            if counts[j] >= cfg.flood_threshold:
                start = lefts[j]
                k = j
                while k + 1 < n and counts[k + 1] >= cfg.flood_end_below:
                    k += 1
                episodes.append(FloodEpisode(machine_id, times[start], times[k],
                                             [a["alarm_event_id"] for a in lst[start:k + 1]]))
                j = k + 1
            else:
                j += 1
    episodes.sort(key=lambda e: (e.start, e.machine_id))
    return episodes


def detect_nuisance_clusters(alarms: Iterable[Mapping], cfg: RulesConfig = DEFAULT_RULES) -> list[NuisanceCluster]:
    """AL-6. N1 chattering: >= chatter_count raises of the same (machine, definition) within chatter_window where
    every raise in the window cleared within chatter_clear_within. N2 repeating: >= repeating_per_day raises in one
    UTC day at severity <= MEDIUM."""
    window = timedelta(seconds=cfg.chatter_window_seconds)
    quick = timedelta(seconds=cfg.chatter_clear_within_seconds)
    out: list[NuisanceCluster] = []
    for (machine_id, def_id), lst in sorted(_by_key(
            alarms, lambda a: (a["machine_id"], a["alarm_definition_id"])).items()):
        members: dict[str, set[str]] = {}
        # N1: split into runs of consecutive quick-cleared alarms, slide the window inside each run
        run: list[Mapping] = []
        runs: list[list[Mapping]] = []
        for a in lst:
            if a["cleared_at"] is not None and a["cleared_at"] - a["raised_at"] <= quick:
                run.append(a)
            else:
                if run:
                    runs.append(run)
                run = []
        if run:
            runs.append(run)
        for r in runs:
            times = [a["raised_at"] for a in r]
            for i in range(len(r)):
                j = bisect_right(times, times[i] + window) - 1  # last alarm within the window starting at i
                if j - i + 1 >= cfg.chatter_count:
                    for a in r[i:j + 1]:
                        members.setdefault(a["alarm_event_id"], set()).add("N1")
        # N2: low-value alarms per UTC day
        per_day: dict = defaultdict(list)
        for a in lst:
            if a["severity"] in ("LOW", "MEDIUM"):
                per_day[a["raised_at"].astimezone(timezone.utc).date()].append(a)   # UTC day, whatever the input offset
        for day_alarms in per_day.values():
            if len(day_alarms) >= cfg.repeating_per_day:
                for a in day_alarms:
                    members.setdefault(a["alarm_event_id"], set()).add("N2")
        if members:
            chosen = [a for a in lst if a["alarm_event_id"] in members]
            out.append(NuisanceCluster(machine_id, def_id, set().union(*members.values()),
                                       [a["alarm_event_id"] for a in chosen], chosen[0]["raised_at"],
                                       chosen[-1]["raised_at"]))
    return out


def detect_repeated_alarms(alarms: Iterable[Mapping], cfg: RulesConfig = DEFAULT_RULES) -> list[RepeatedPattern]:
    """AL-7: same (machine, definition) raised >= repeated_alarm_count times within repeated_alarm_window."""
    window = timedelta(seconds=cfg.repeated_alarm_window_seconds)
    out: list[RepeatedPattern] = []
    for (machine_id, def_id), lst in sorted(_by_key(
            alarms, lambda a: (a["machine_id"], a["alarm_definition_id"])).items()):
        times = [a["raised_at"] for a in lst]
        member: set[int] = set()
        best = 0
        for i in range(len(lst)):
            j = bisect_right(times, times[i] + window - timedelta(microseconds=1)) - 1
            c = j - i + 1
            if c >= cfg.repeated_alarm_count:
                member.update(range(i, j + 1))
                best = max(best, c)
        if member:
            out.append(RepeatedPattern(machine_id, def_id, best, [lst[k]["alarm_event_id"] for k in sorted(member)]))
    return out
