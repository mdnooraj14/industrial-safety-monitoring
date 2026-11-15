"""Stage 10-11: incidents (rule IN-1/IN-2), their lifecycle history, corrective actions and their history.

Everything is derived from timestamps drawn from documented per-severity ranges and truncated at the data cut-off:
a milestone later than the cut-off simply has not happened yet, so open / unresolved items arise naturally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.business_rules.corrective_action_rules import VERIFICATION_FAIL_RATE
from app.business_rules.incident_rules import find_unsafe_episodes, incident_candidates

from ..common import action_id, incident_id, rng_for

RULE_TITLE = {"R-U1": "Safety door open while running", "R-U2": "Required interlock inactive while running",
              "R-U3": "Required interlock bypassed while running"}
ACTION_TYPES = {"R-U1": ["REPAIR_DOOR_SWITCH", "INSTALL_GUARD", "OPERATOR_TRAINING"],
                "R-U2": ["REPLACE_INTERLOCK", "ADJUST_ALARM_LIMIT", "REVISE_PROCEDURE"],
                "R-U3": ["REVISE_PROCEDURE", "OPERATOR_TRAINING", "REPLACE_INTERLOCK"]}
ACTION_TEXT = {
    "REPAIR_DOOR_SWITCH": "Inspect and repair the door switch and wiring on {m}; retest the interlock chain.",
    "INSTALL_GUARD": "Install an additional fixed guard on {m} to prevent access while running.",
    "OPERATOR_TRAINING": "Retrain operators of {m} on safe door and interlock procedures.",
    "REPLACE_INTERLOCK": "Replace the faulty safety interlock on {m} and perform a functional safety test.",
    "ADJUST_ALARM_LIMIT": "Review and adjust alarm limits and response times for {m}.",
    "REVISE_PROCEDURE": "Revise the lock-out / bypass procedure for {m} and brief the shift.",
}
# (min, max) in hours unless noted. SLA = action due date after planning.
PARAMS = {
    "CRITICAL": dict(inv=(0.25, 2), plan=(1, 4), start=(1, 12), work=(24, 96), verify=(12, 48), close=(2, 12),
                     inc_close=(12, 48), sla_days=3, p_two=0.40),
    "HIGH": dict(inv=(1, 8), plan=(2, 8), start=(2, 24), work=(48, 192), verify=(24, 72), close=(4, 24),
                 inc_close=(24, 96), sla_days=7, p_two=0.20),
}
MAX_FOLLOW_UPS = 2


@dataclass
class IncidentResult:
    incidents: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    incident_events: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    action_events: list[dict] = field(default_factory=list)


def _h(rng, lo: float, hi: float) -> timedelta:
    return timedelta(hours=float(rng.uniform(lo, hi)))


def _le(ts: datetime | None, cutoff: datetime) -> datetime | None:
    return ts if ts is not None and ts <= cutoff else None


def build_incidents(cfg, master, shifts, states, alarms: list[dict]) -> IncidentResult:
    cutoff = cfg.end
    res = IncidentResult()
    machines = {m["machine_id"]: m for m in master.machines}
    primary_zone = {r["machine_id"]: r["safety_zone_id"] for r in master.machine_zone if r["is_primary"]}
    alarm_by_id = {a["alarm_event_id"]: a for a in alarms}
    episodes = find_unsafe_episodes(states.machine_states, cfg.end)
    cands = incident_candidates(episodes, alarms)
    cands.sort(key=lambda c: (alarm_by_id[c.trigger_alarm_id]["raised_at"], c.episode.machine_id))

    for n, c in enumerate(cands, start=1):
        rng = rng_for(cfg.seed, "incidents", n)
        trig = alarm_by_id[c.trigger_alarm_id]
        mid = c.episode.machine_id
        mach = machines[mid]
        pid = mach["plant_id"]
        opened = trig["raised_at"]
        inc_id = incident_id(opened.year, n)
        p = PARAMS[c.severity]
        # ---- links ----
        for aid in c.linked_alarm_ids:
            a = alarm_by_id[aid]
            res.links.append(dict(incident_id=inc_id, alarm_event_id=aid,
                                  link_role="TRIGGER" if aid == c.trigger_alarm_id else "CONTRIBUTING",
                                  linked_at=max(opened, a["raised_at"])))
        # ---- corrective actions (chains: primary action -> follow-up on failed verification) ----
        inv_at = opened + _h(rng, *p["inv"])
        engineers = [e for e in shifts.engineers[pid] if not e.get("quality")]
        quality = [e for e in shifts.engineers[pid] if e.get("quality")]
        types = list(ACTION_TYPES[c.episode.rule_code])
        n_chains = 2 if rng.random() < p["p_two"] else 1
        chain_types = [types[int(i)] for i in rng.permutation(len(types))[:n_chains]]
        chains: list[list[dict]] = []
        for atype in chain_types:
            planned = inv_at + _h(rng, *p["plan"])
            depth = 0
            chain: list[dict] = []
            while True:
                eng = engineers[int(rng.integers(len(engineers)))]
                ver = quality[int(rng.integers(len(quality)))]
                started = planned + _h(rng, *p["start"])
                completed = started + _h(rng, *p["work"])
                verified = completed + _h(rng, *p["verify"])
                fail = depth < MAX_FOLLOW_UPS and rng.random() < VERIFICATION_FAIL_RATE[atype]
                closed = None if fail else verified + _h(rng, *p["close"])
                act = dict(
                    corrective_action_id=None, incident_id=inc_id, assigned_engineer_id=eng["employee_id"],
                    assigned_engineer_name=eng["name"], action_type=atype,
                    action_description=ACTION_TEXT[atype].format(m=mach["machine_name"]),
                    planned_at=planned, due_at=planned + timedelta(days=p["sla_days"]),
                    started_at=started, completed_at=completed, verified_at=verified,
                    verified_by_id=ver["employee_id"], verification_result="FAILED" if fail else "PASSED",
                    closed_at=closed, _fail=fail)
                chain.append(act)
                if not fail:
                    break
                planned = verified + _h(rng, 4, 24)  # follow-up planned after the failed verification
                act["_superseded_at"] = planned
                depth += 1
            # an action planned after the cut-off does not exist yet
            chains.append([a for a in chain if a["planned_at"] <= cutoff])
        actions = [a for ch in chains for a in ch]
        # number actions by planning time
        actions.sort(key=lambda a: (a["planned_at"], a["action_type"]))
        for k, a in enumerate(actions, start=1):
            a["corrective_action_id"] = action_id(inc_id, k)
        # ---- truncate at cut-off, derive status and history ----
        for a in actions:
            ev = [(None, "PLANNED", a["planned_at"])]
            for field_, status in (("started_at", "IN_PROGRESS"), ("completed_at", "COMPLETED"),
                                   ("verified_at", "VERIFIED"), ("closed_at", "CLOSED")):
                ts = _le(a[field_], cutoff)
                a[field_] = ts
                if ts:
                    ev.append((None, status, ts))
            sup = _le(a.pop("_superseded_at", None), cutoff) if a["_fail"] else None
            if sup:
                ev.append((None, "CANCELLED", sup))
            if a["verified_at"] is None:
                a["verified_by_id"] = a["verification_result"] = None
            a["status"] = ev[-1][1]
            prev = None
            for seq, (_, status, ts) in enumerate(ev, start=1):
                res.action_events.append(dict(
                    action_status_event_id=f"ASTAT-{a['corrective_action_id']}-{seq:02d}",
                    corrective_action_id=a["corrective_action_id"], sequence_no=seq, from_status=prev,
                    to_status=status, changed_at=ts,
                    note="superseded by follow-up action after failed verification" if status == "CANCELLED" else None))
                prev = status
            res.actions.append(a)
        # ---- incident lifecycle ----
        first_start = min((a["started_at"] for a in actions if a["started_at"]), default=None)
        finals = [ch[-1] if ch and not ch[-1]["_fail"] else None for ch in chains]
        complete = all(f is not None and f["verified_at"] for f in finals)
        resolved = max(f["verified_at"] for f in finals) if complete else None
        closed = None
        if complete and all(f["closed_at"] for f in finals):
            closed = max(f["closed_at"] for f in finals) + _h(rng, *p["inc_close"])
        for a in actions:
            a.pop("_fail")
        milestones = [("INVESTIGATING", _le(inv_at, cutoff), "ENGINEER"),
                      ("ACTION_IN_PROGRESS", _le(first_start, cutoff), "ENGINEER"),
                      ("RESOLVED", _le(resolved, cutoff), "ENGINEER"),
                      ("CLOSED", _le(closed, cutoff), "SUPERVISOR")]
        events = [("OPEN", opened, "SYSTEM")] + [m for m in milestones if m[1]]
        inc = dict(
            incident_id=inc_id, machine_id=mid,
            safety_zone_id=trig["safety_zone_id"] or primary_zone.get(mid),
            trigger_machine_state_event_id=c.episode.start_event_id, trigger_rule_code=c.episode.rule_code,
            shift_id=shifts.shift_for(pid, mid, opened), severity=c.severity, status=events[-1][0],
            title=f"{RULE_TITLE[c.episode.rule_code]} on {mach['machine_name']}",
            description=(f"{RULE_TITLE[c.episode.rule_code]} ({c.episode.rule_code}) lasted "
                         f"{c.episode.duration_seconds:.0f} s on {mach['machine_name']} ({mid}); "
                         f"{len(c.linked_alarm_ids)} related alarm(s)."),
            opened_at=opened, investigation_started_at=_le(inv_at, cutoff), resolved_at=_le(resolved, cutoff),
            closed_at=_le(closed, cutoff))
        res.incidents.append(inc)
        prev = None
        for seq, (status, ts, role) in enumerate(events, start=1):
            res.incident_events.append(dict(
                incident_status_event_id=f"ISTAT-{inc_id}-{seq:02d}", incident_id=inc_id, sequence_no=seq,
                from_status=prev, to_status=status, changed_at=ts, changed_by_role=role, note=None))
            prev = status
    return res
