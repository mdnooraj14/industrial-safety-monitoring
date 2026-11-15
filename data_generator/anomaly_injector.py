"""Milestone 5: inject the five labelled negative scenarios into a COPY of the valid baseline.

Rules of the injector
* Baseline rows are never modified, deleted or reordered: every injected record is APPENDED, and injected IDs continue
  the existing counters (so baseline IDs are identical with and without anomalies).
* Injected rows look like ordinary rows (no marker text or columns). The only record of what was injected is the
  separate ground-truth list returned here and written to its own files / database schema.
* Derived values on injected rows (assessment, delay, status) are computed by the same business rules as the baseline.

Scenarios (docs/business_rules.md section 5):
  1 ACK_BEFORE_ALARM            new alarm whose acknowledgement timestamp precedes the alarm
  2 IMPOSSIBLE_INTERLOCK_STATE  new end-of-timeline machine/interlock event violating R-I1 or R-I4
  3 INCIDENT_NO_CONTEXT         new incident with no alarm links and no trigger machine state
  4 REPLAY_DUPLICATE_FLOOD      a baseline flood re-ingested under a new batch (new IDs, same readings)
  5 ACTION_CLOSED_UNVERIFIED    new action on an open incident closed with no verification
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from app.business_rules.alarm_rules import ack_delay_seconds, ack_status
from app.business_rules.state_processing import InterlockSpec, PreviousState, process_state_event

from .common import ack_id, action_id, alarm_id, incident_id, rng_for
from .generators.alarms import _Builder
from .generators.incidents import ACTION_TEXT, ACTION_TYPES, PARAMS

NEUTRAL_REASON = "sensor update"


@dataclass
class InjectionResult:
    tables: dict[str, list[dict]]
    labels: list[dict]
    counts: dict[str, int] = field(default_factory=dict)


def inject_anomalies(ds) -> InjectionResult:
    cfg, ctx = ds.cfg, ds.ctx
    master, shifts, states = ctx["master"], ctx["shifts"], ctx["states"]
    t = {name: list(rows) for name, rows in ds.tables.items()}       # new lists; baseline rows are shared, never edited
    labels = list(ds.ground_truth)
    counts: dict[str, int] = defaultdict(int)
    machines = sorted(ds.tables["machine"], key=lambda m: m["machine_id"])
    by_id = {m["machine_id"]: m for m in machines}
    primary_zone = {r["machine_id"]: r["safety_zone_id"] for r in master.machine_zone if r["is_primary"]}

    def label(issue, table, rid, group, desc):
        labels.append(dict(label_id=f"GT-{len(labels) + 1:06d}", label_class="INJECTED_ANOMALY", issue_type=issue,
                           record_table=table, record_id=rid, group_id=group, expected_decision="FLAG",
                           description=desc))
        counts[issue] += 1

    # next alarm counter per machine (injected alarms continue the numbering)
    next_alarm: dict[str, int] = defaultdict(int)
    for a in ds.tables["alarm_event"]:
        next_alarm[a["machine_id"]] = max(next_alarm[a["machine_id"]], int(a["alarm_event_id"].rsplit("-", 1)[1]))

    def new_alarm_id(mid: str) -> str:
        next_alarm[mid] += 1
        return alarm_id(mid, next_alarm[mid])

    # ------------------------------------------------------------ 1. acknowledgement before alarm
    rng = rng_for(cfg.seed, "anomalies", 1)
    builder = _Builder(cfg, master, shifts, states)
    for n in range(cfg.n_ack_before_alarm):
        mid = machines[int(rng.integers(len(machines)))]["machine_id"]
        pool = [d for d in builder.applicable(mid)
                if d["trigger_type"] in ("PROCESS_LIMIT", "COMMUNICATION", "MAINTENANCE_DUE")]
        d = pool[int(rng.integers(len(pool)))]
        raised = cfg.start + timedelta(seconds=float(rng.uniform(3600, cfg.days * 86400 - 7200)))
        row = builder.make(mid, d, raised, rng)
        row.pop("_scenario")
        row["alarm_event_id"] = new_alarm_id(mid)
        t["alarm_event"].append(row)
        ack_at = (row["raised_at"] - timedelta(seconds=float(rng.uniform(5, 600)))).replace(microsecond=0)
        plant = by_id[mid]["plant_id"]
        ack = dict(acknowledgement_id=ack_id(row["alarm_event_id"]), alarm_event_id=row["alarm_event_id"],
                   acknowledged_by_shift_id=shifts.shift_for(plant, mid, ack_at), acknowledged_at=ack_at,
                   ack_method="HMI", ack_delay_seconds=ack_delay_seconds(row["raised_at"], ack_at),
                   ack_status=ack_status(ack_at, row["ack_deadline_at"]), comment=None)
        t["acknowledgement"].append(ack)
        label("ACK_BEFORE_ALARM", "acknowledgement", ack["acknowledgement_id"], f"ACKB-{n + 1:04d}",
              "acknowledged_at is earlier than the alarm's raised_at")

    # ------------------------------------------------------------ 2. impossible interlock state
    rng = rng_for(cfg.seed, "anomalies", 2)
    last_mse: dict[str, dict] = {}
    for r in ds.tables["machine_state_event"]:
        if r["machine_id"] not in last_mse or r["sequence_no"] > last_mse[r["machine_id"]]["sequence_no"]:
            last_mse[r["machine_id"]] = r
    last_ise: dict[str, dict] = {}
    for r in ds.tables["interlock_state_event"]:
        if r["interlock_id"] not in last_ise or r["sequence_no"] > last_ise[r["interlock_id"]]["sequence_no"]:
            last_ise[r["interlock_id"]] = r
    il_by_machine = defaultdict(list)
    for i in ds.tables["interlock"]:
        il_by_machine[i["machine_id"]].append(i)
    # the new event must come after every alarm already on the machine, so no existing alarm's "state in effect"
    # reference changes (injected records must not alter unrelated rows)
    last_ref: dict[str, object] = {mid: r["event_ts"] for mid, r in last_mse.items()}
    for a in t["alarm_event"]:
        if a["raised_at"] > last_ref[a["machine_id"]]:
            last_ref[a["machine_id"]] = a["raised_at"]
    eligible = [m["machine_id"] for m in machines
                if last_mse[m["machine_id"]]["safety_assessment"] == "SAFE"
                and cfg.end - last_ref[m["machine_id"]] > timedelta(seconds=10)]
    order = rng.permutation(len(eligible))
    for n, k in enumerate(order[:cfg.n_impossible_interlock]):
        mid = eligible[int(k)]
        il = il_by_machine[mid]
        door_il = [i for i in il if i["interlock_type"] in ("DOOR_SWITCH", "GUARD_LOCK")]
        non_bypassable = [i for i in il if not i["bypass_allowed"]]
        use_r_i1 = bool(door_il) and n % 2 == 0
        last = last_mse[mid]
        specs = [InterlockSpec(i["interlock_id"], i["interlock_type"], i["is_required"], i["bypass_allowed"]) for i in il]
        prev = PreviousState(last["machine_state"], last["door_state"], last["event_ts"], last["sequence_no"],
                             {i["interlock_id"]: last_ise[i["interlock_id"]]["interlock_status"] for i in il})
        ref = last_ref[mid]
        gap = min(timedelta(minutes=30), (cfg.end - ref) / 2)
        ts = (ref + max(gap, timedelta(seconds=2))).replace(microsecond=0)
        if use_r_i1:   # door open while its door interlock still reports ENGAGED
            target, new_status, door = door_il[0], "ENGAGED", "OPEN"
        else:          # bypass on an interlock that does not allow bypass
            target, new_status, door = non_bypassable[0], "BYPASSED", last["door_state"]
        res = process_state_event(prev, ts, last["machine_state"], door, {target["interlock_id"]: new_status}, specs)
        mse_id = f"MSE-{mid}-{res.sequence_no:06d}"
        t["machine_state_event"].append(dict(
            machine_state_event_id=mse_id, machine_id=mid, sequence_no=res.sequence_no, event_ts=ts,
            previous_state=res.previous_state, machine_state=res.machine_state, door_state=door,
            interlock_status_summary=res.summary, safety_assessment=res.assessment, unsafe_rule_code=res.rule_code,
            shift_id=shifts.shift_for(by_id[mid]["plant_id"], mid, ts), transition_reason=NEUTRAL_REASON))
        for iid in res.changed_interlocks:
            is_door = any(i["interlock_id"] == iid and i["interlock_type"] in ("DOOR_SWITCH", "GUARD_LOCK") for i in il)
            seq = last_ise[iid]["sequence_no"] + 1
            t["interlock_state_event"].append(dict(
                interlock_state_event_id=f"ISE-{iid}-{seq:06d}", interlock_id=iid, sequence_no=seq, event_ts=ts,
                interlock_status=res.interlock_statuses[iid],
                door_state_observed=(("OPEN" if door == "OPEN" else "CLOSED") if is_door else None),
                machine_state_event_id=mse_id))
        assert res.assessment == "INVALID" and res.rule_code == ("R-I1" if use_r_i1 else "R-I4"), res
        label("IMPOSSIBLE_INTERLOCK_STATE", "machine_state_event", mse_id, f"IMPI-{n + 1:04d}",
              f"impossible interlock combination ({res.rule_code})")

    # ------------------------------------------------------------ 3. incident without triggering context
    rng = rng_for(cfg.seed, "anomalies", 3)
    year = cfg.start.year
    n_inc = len(ds.tables["incident"])
    for n in range(cfg.n_incident_no_context):
        mid = machines[int(rng.integers(len(machines)))]["machine_id"]
        m = by_id[mid]
        opened = (cfg.start + timedelta(seconds=float(rng.uniform(86400, cfg.days * 86400 - 3 * 86400)))).replace(microsecond=0)
        inc_id = incident_id(year, n_inc + n + 1)
        status_events = [("OPEN", opened, "OPERATOR")]
        inv = opened + timedelta(hours=float(rng.uniform(1, 8)))
        if rng.random() < 0.5 and inv < cfg.end:
            status_events.append(("INVESTIGATING", inv, "ENGINEER"))
        t["incident"].append(dict(
            incident_id=inc_id, machine_id=mid, safety_zone_id=primary_zone.get(mid),
            trigger_machine_state_event_id=None, trigger_rule_code=None,
            shift_id=shifts.shift_for(m["plant_id"], mid, opened), severity=str(rng.choice(["HIGH", "CRITICAL"])),
            status=status_events[-1][0], title=f"Safety event reported on {m['machine_name']}",
            description="Safety incident reported by operator.", opened_at=opened,
            investigation_started_at=inv if len(status_events) > 1 else None, resolved_at=None, closed_at=None))
        prev = None
        for seq, (status, ts, role) in enumerate(status_events, start=1):
            t["incident_status_event"].append(dict(
                incident_status_event_id=f"ISTAT-{inc_id}-{seq:02d}", incident_id=inc_id, sequence_no=seq,
                from_status=prev, to_status=status, changed_at=ts, changed_by_role=role, note=None))
            prev = status
        label("INCIDENT_NO_CONTEXT", "incident", inc_id, f"NOCTX-{n + 1:04d}",
              "incident has no linked alarm and no trigger machine state")

    # ------------------------------------------------------------ 4. alarm flood duplicated by replay
    rng = rng_for(cfg.seed, "anomalies", 4)
    alarm_by_id = {a["alarm_event_id"]: a for a in ds.tables["alarm_event"]}
    floods = defaultdict(list)
    for g in ds.ground_truth:
        if g["issue_type"] == "ALARM_FLOOD":
            floods[g["group_id"]].append(g["record_id"])
    flood_ids = sorted(floods)
    for n, k in enumerate(rng.permutation(len(flood_ids))[:cfg.n_replay_floods], start=1):
        originals = sorted((alarm_by_id[i] for i in floods[flood_ids[int(k)]]),
                           key=lambda a: (a["raised_at"], a["alarm_event_id"]))
        # an ordinary-looking later batch of the same day (baseline batches are "-01"); no label in the name
        batch = f"BATCH-{originals[0]['raised_at']:%Y%m%d}-{n + 1:02d}"
        for a in originals:
            copy = dict(a)
            copy["alarm_event_id"] = new_alarm_id(a["machine_id"])
            copy["ingest_batch_id"] = batch
            t["alarm_event"].append(copy)
            label("REPLAY_DUPLICATE_FLOOD", "alarm_event", copy["alarm_event_id"], f"REPL-{n:04d}",
                  f"re-ingested copy of {a['alarm_event_id']}")

    # ------------------------------------------------------------ 5. action closed without verification
    rng = rng_for(cfg.seed, "anomalies", 5)
    open_incidents = sorted((i for i in ds.tables["incident"]
                             if i["status"] != "CLOSED" and i["opened_at"] + timedelta(hours=10) <= cfg.end),
                            key=lambda i: i["incident_id"])
    max_suffix: dict[str, int] = defaultdict(int)
    for a in ds.tables["corrective_action"]:
        max_suffix[a["incident_id"]] = max(max_suffix[a["incident_id"]], int(a["corrective_action_id"].rsplit("-", 1)[1]))
    for n, k in enumerate(rng.permutation(len(open_incidents))[:cfg.n_action_closed_unverified], start=1):
        inc = open_incidents[int(k)]
        mach = by_id[inc["machine_id"]]
        engineers = [e for e in shifts.engineers[mach["plant_id"]] if not e.get("quality")]
        eng = engineers[int(rng.integers(len(engineers)))]
        atype = ACTION_TYPES[inc["trigger_rule_code"]][int(rng.integers(3))]
        planned = inc["opened_at"] + timedelta(hours=1)
        started, completed = planned + timedelta(hours=2), planned + timedelta(hours=8)
        closed = completed + timedelta(hours=1)
        max_suffix[inc["incident_id"]] += 1
        aid = action_id(inc["incident_id"], max_suffix[inc["incident_id"]])
        t["corrective_action"].append(dict(
            corrective_action_id=aid, incident_id=inc["incident_id"], assigned_engineer_id=eng["employee_id"],
            assigned_engineer_name=eng["name"], action_type=atype,
            action_description=ACTION_TEXT[atype].format(m=mach["machine_name"]),
            planned_at=planned, due_at=planned + timedelta(days=PARAMS[inc["severity"]]["sla_days"]),
            started_at=started, completed_at=completed, verified_at=None, verified_by_id=None,
            verification_result=None, closed_at=closed, status="CLOSED"))
        prev = None
        for seq, (status, ts) in enumerate([("PLANNED", planned), ("IN_PROGRESS", started), ("COMPLETED", completed),
                                            ("CLOSED", closed)], start=1):
            t["corrective_action_status_event"].append(dict(
                action_status_event_id=f"ASTAT-{aid}-{seq:02d}", corrective_action_id=aid, sequence_no=seq,
                from_status=prev, to_status=status, changed_at=ts, note=None))
            prev = status
        label("ACTION_CLOSED_UNVERIFIED", "corrective_action", aid, f"UNVR-{n:04d}",
              "action CLOSED with no verification")

    return InjectionResult(t, labels, dict(counts))
