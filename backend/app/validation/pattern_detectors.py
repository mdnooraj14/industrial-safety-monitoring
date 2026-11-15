"""Pattern detectors (flood, nuisance cluster, repeated unsafe condition) expressed as record-level Findings.

They run on the alarms that remain after replay duplicates are set aside (a real pipeline de-duplicates before flood
analysis). Like the anomaly detectors they never read ground truth.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.business_rules.alarm_patterns import detect_floods, detect_nuisance_clusters
from app.business_rules.config import DEFAULT_RULES, RulesConfig
from app.business_rules.incident_rules import find_repeated_unsafe, find_unsafe_episodes
from app.validation.anomaly_detectors import Finding, detect_replay_duplicates

PATTERN_TYPES = ["ALARM_FLOOD", "NUISANCE_CLUSTER", "REPEATED_UNSAFE_CONDITION"]


def detect_patterns(tables: Mapping[str, Sequence[Mapping]], rules: RulesConfig = DEFAULT_RULES) -> list[Finding]:
    dup = {f.record_id for f in detect_replay_duplicates(tables["alarm_event"])}
    alarms = [a for a in tables["alarm_event"] if a["alarm_event_id"] not in dup]
    out: list[Finding] = []
    for e in detect_floods(alarms, rules):
        out += [Finding("ALARM_FLOOD", "alarm_event", aid,
                        f"part of a flood of {e.alarm_count} alarms on {e.machine_id}") for aid in e.alarm_ids]
    for c in detect_nuisance_clusters(alarms, rules):
        out += [Finding("NUISANCE_CLUSTER", "alarm_event", aid,
                        f"nuisance cluster {'/'.join(sorted(c.rules))} on {c.machine_id}") for aid in c.alarm_ids]
    states = tables["machine_state_event"]
    data_end = max((s["event_ts"] for s in states), default=None)
    if data_end is not None:
        episodes = find_unsafe_episodes(states, data_end)
        for group in find_repeated_unsafe(episodes, rules):
            out += [Finding("REPEATED_UNSAFE_CONDITION", "machine_state_event", ep.start_event_id,
                            f"{ep.rule_code} repeated {len(group)} times on {ep.machine_id}") for ep in group]
    return sorted(out, key=lambda f: (f.issue_type, f.record_id))
