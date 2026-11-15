"""The five anomaly detectors (docs/business_rules.md section 5). Pure functions over feature-table rows.

SEPARATION OF CONCERNS: nothing in this module (or its imports) touches ground-truth labels, the `gt` schema or any
`issue_type` column. Detectors recompute facts from raw events (timestamps, interlock history, links) and do not trust
stored derived columns such as safety_assessment or ack_status. They are compared with ground truth only by the
evaluation code (Milestone 7).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.business_rules.corrective_action_rules import action_closed_properly
from app.validation.replay import replay_machine_states

ISSUE_TYPES = ["ACK_BEFORE_ALARM", "IMPOSSIBLE_INTERLOCK_STATE", "INCIDENT_NO_CONTEXT", "REPLAY_DUPLICATE_FLOOD",
               "ACTION_CLOSED_UNVERIFIED"]


@dataclass(frozen=True)
class Finding:
    issue_type: str
    record_table: str
    record_id: str
    reason: str


def detect_ack_before_alarm(acks: Iterable[Mapping], alarms: Iterable[Mapping]) -> list[Finding]:
    """AL-4: an acknowledgement timestamp earlier than the alarm it acknowledges (negative delay)."""
    raised = {a["alarm_event_id"]: a["raised_at"] for a in alarms}
    out = []
    for k in acks:
        r = raised.get(k["alarm_event_id"])
        if r is not None and k["acknowledged_at"] < r:
            delay = (k["acknowledged_at"] - r).total_seconds()
            out.append(Finding("ACK_BEFORE_ALARM", "acknowledgement", k["acknowledgement_id"],
                               f"acknowledged {abs(delay):.0f} s before the alarm was raised"))
    return sorted(out, key=lambda f: f.record_id)


def detect_impossible_interlock_states(machine_states: Iterable[Mapping], interlock_states: Iterable[Mapping],
                                       interlocks: Iterable[Mapping]) -> list[Finding]:
    """R-I1..R-I4: replay each machine's interlock history and re-derive every state event."""
    out = []
    for r in replay_machine_states(machine_states, interlock_states, interlocks):
        reasons = []
        if r.assessed[0] == "INVALID":
            reasons.append(r.assessed[1])
        if not r.summary_ok:
            reasons.append("R-I2")
        if not r.transition_ok:
            reasons.append("R-I3")
        if reasons:
            out.append(Finding("IMPOSSIBLE_INTERLOCK_STATE", "machine_state_event", r.row["machine_state_event_id"],
                               "impossible interlock/state combination: " + ", ".join(reasons)))
    return sorted(out, key=lambda f: f.record_id)


def detect_incidents_without_context(incidents: Iterable[Mapping], links: Iterable[Mapping]) -> list[Finding]:
    """IN-1: a safety incident needs a TRIGGER alarm link and a trigger machine state."""
    has_trigger = {l["incident_id"] for l in links if l["link_role"] == "TRIGGER"}
    out = []
    for i in incidents:
        missing = []
        if i["incident_id"] not in has_trigger:
            missing.append("no trigger alarm link")
        if i["trigger_machine_state_event_id"] is None:
            missing.append("no trigger machine state")
        if missing:
            out.append(Finding("INCIDENT_NO_CONTEXT", "incident", i["incident_id"], "; ".join(missing)))
    return sorted(out, key=lambda f: f.record_id)


def detect_replay_duplicates(alarms: Iterable[Mapping]) -> list[Finding]:
    """AL-8: same (machine, definition, raised_at to the second) ingested under different batches -> later copies."""
    groups = defaultdict(list)
    for a in alarms:
        groups[(a["machine_id"], a["alarm_definition_id"], a["raised_at"].replace(microsecond=0))].append(a)
    out = []
    for members in groups.values():
        if len({m["ingest_batch_id"] for m in members}) < 2:
            continue
        members.sort(key=lambda m: (m["ingest_batch_id"], m["alarm_event_id"]))
        first_batch = members[0]["ingest_batch_id"]
        for m in members:
            if m["ingest_batch_id"] != first_batch:
                out.append(Finding("REPLAY_DUPLICATE_FLOOD", "alarm_event", m["alarm_event_id"],
                                   f"duplicate of a reading already ingested in {first_batch}"))
    return sorted(out, key=lambda f: f.record_id)


def detect_unverified_closures(actions: Iterable[Mapping]) -> list[Finding]:
    """CA-3: an action that is CLOSED without a PASSED, independent, correctly ordered verification."""
    return sorted((Finding("ACTION_CLOSED_UNVERIFIED", "corrective_action", a["corrective_action_id"],
                           "closed without a valid verification")
                   for a in actions if not action_closed_properly(a)), key=lambda f: f.record_id)


def detect_all(tables: Mapping[str, Sequence[Mapping]]) -> list[Finding]:
    """Run all five detectors on a dict of feature tables."""
    return (detect_ack_before_alarm(tables["acknowledgement"], tables["alarm_event"])
            + detect_impossible_interlock_states(tables["machine_state_event"], tables["interlock_state_event"],
                                                 tables["interlock"])
            + detect_incidents_without_context(tables["incident"], tables["incident_alarm_link"])
            + detect_replay_duplicates(tables["alarm_event"])
            + detect_unverified_closures(tables["corrective_action"]))
