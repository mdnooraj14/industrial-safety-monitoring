"""Ground-truth labels, kept OUT of every feature table (own files / own DB schema).

Milestone 3 writes BASELINE_SCENARIO labels (alarm floods, nuisance clusters, repeated unsafe conditions) that the
generator planted. Milestone 5 appends INJECTED_ANOMALY labels via the anomaly injector.
"""
from __future__ import annotations

from app.business_rules.incident_rules import find_repeated_unsafe, find_unsafe_episodes

GT_COLUMNS = ["label_id", "label_class", "issue_type", "record_table", "record_id", "group_id",
              "expected_decision", "description"]


def baseline_labels(cfg, states, alarm_result) -> list[dict]:
    rows: list[dict] = []

    def add(issue, table, rid, group, decision, desc):
        rows.append(dict(label_id=f"GT-{len(rows) + 1:06d}", label_class="BASELINE_SCENARIO", issue_type=issue,
                         record_table=table, record_id=rid, group_id=group, expected_decision=decision,
                         description=desc))

    for gid in sorted(alarm_result.scenarios):
        g = alarm_result.scenarios[gid]
        desc = ("alarm belongs to a flood episode" if g["issue_type"] == "FLOOD"
                else "alarm belongs to a nuisance (chattering / repeating) cluster")
        issue = "ALARM_FLOOD" if g["issue_type"] == "FLOOD" else "NUISANCE_CLUSTER"
        for aid in sorted(g["alarm_ids"]):
            add(issue, "alarm_event", aid, gid, "FLAG", desc)
    episodes = find_unsafe_episodes(states.machine_states, cfg.end)
    for n, group in enumerate(find_repeated_unsafe(episodes), start=1):
        for ep in group:
            add("REPEATED_UNSAFE_CONDITION", "machine_state_event", ep.start_event_id, f"REPEAT-{n:04d}", "FLAG",
                f"unsafe episode {ep.rule_code} is part of a repeated condition on {ep.machine_id}")
    return rows
