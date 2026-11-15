"""The validation checks (V01..V17). Each takes the dict of feature tables and returns a CheckResult.

Structural checks (V01-V05) prove the data can be trusted as a relational model. Business-rule checks (V06-V17)
verify timelines, lifecycles, derived values and reconciliation against docs/business_rules.md. Business-rule failures
are exactly what injected anomalies cause; the clean baseline must pass every check.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from typing import Mapping, Sequence

from app.business_rules.alarm_rules import ack_delay_seconds, ack_status, classify_severity
from app.business_rules.corrective_action_rules import (
    action_closed_properly, can_close_incident, valid_action_transition, valid_incident_transition,
)
from app.business_rules.incident_rules import incident_severity
from app.business_rules.safety_state import is_valid_transition
from app.models import Base, enums as E
from app.validation.anomaly_detectors import detect_impossible_interlock_states
from app.validation.framework import CheckResult, Failure
from app.validation.replay import replay_machine_states

Tables = Mapping[str, Sequence[Mapping]]


def _pk_cols(table: str) -> list[str]:
    return [c.name for c in Base.metadata.tables[table].primary_key.columns]


def _pk(table: str, row: Mapping) -> str:
    return "|".join(str(row[c]) for c in _pk_cols(table))


def _idx(rows, key):
    return {r[key]: r for r in rows}


def _group(rows, key):
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return g


def _num(v) -> float:
    return float(v)


# ============================================================ structural
def v01_primary_keys(t: Tables) -> CheckResult:
    res = CheckResult("V01", "structural", "Primary-key uniqueness", "Every row has a unique, non-null primary key.", 0)
    for table, rows in t.items():
        cols = _pk_cols(table)
        seen = Counter()
        for r in rows:
            res.records_checked += 1
            key = tuple(r[c] for c in cols)
            if any(v is None for v in key):
                res.failures.append(Failure(table, "|".join(map(str, key)), "null primary key"))
            seen[key] += 1
        res.failures += [Failure(table, "|".join(map(str, k)), f"primary key appears {n} times")
                         for k, n in seen.items() if n > 1]
    return res


def v02_foreign_keys(t: Tables) -> CheckResult:
    res = CheckResult("V02", "structural", "Foreign-key integrity",
                      "Every non-null foreign key resolves to an existing parent row.", 0)
    pk = {tb: {tuple(r[c] for c in _pk_cols(tb)) for r in rows} for tb, rows in t.items()}
    for table, rows in t.items():
        for fk in Base.metadata.tables[table].foreign_key_constraints:
            parent, cols = fk.referred_table.name, [e.parent.name for e in fk.elements]
            for r in rows:
                vals = tuple(r[c] for c in cols)
                if any(v is None for v in vals):
                    continue
                res.records_checked += 1
                if vals not in pk[parent]:
                    res.failures.append(Failure(table, _pk(table, r), f"{cols}={vals} not found in {parent}"))
    return res


_ENUMS = {
    "plant": {"industry_type": E.IndustryType, "lifecycle_status": E.PlantStatus},
    "machine": {"machine_type": E.MachineType, "criticality_class": E.Criticality, "lifecycle_status": E.MachineStatus},
    "safety_zone": {"zone_type": E.ZoneType, "risk_level": E.RiskLevel, "lifecycle_status": E.ZoneStatus},
    "interlock": {"interlock_type": E.InterlockType, "lifecycle_status": E.InterlockLifecycle},
    "alarm_severity_policy": {"severity": E.Severity},
    "alarm_definition": {"machine_type": E.MachineType, "trigger_type": E.TriggerType, "interlock_type": E.InterlockType,
                         "default_severity": E.Severity, "lifecycle_status": E.AlarmDefStatus},
    "operator_shift": {"shift_code": E.ShiftCode, "operator_role": E.OperatorRole, "lifecycle_status": E.ShiftStatus},
    "machine_state_event": {"previous_state": E.MachineState, "machine_state": E.MachineState, "door_state": E.DoorState,
                            "interlock_status_summary": E.InterlockSummary, "safety_assessment": E.SafetyAssessment},
    "interlock_state_event": {"interlock_status": E.InterlockStatus, "door_state_observed": E.DoorObserved},
    "alarm_event": {"severity": E.Severity, "lifecycle_status": E.AlarmLifecycle},
    "acknowledgement": {"ack_method": E.AckMethod, "ack_status": E.AckStatus},
    "incident": {"severity": E.IncidentSeverity, "status": E.IncidentStatus},
    "incident_alarm_link": {"link_role": E.LinkRole},
    "incident_status_event": {"from_status": E.IncidentStatus, "to_status": E.IncidentStatus,
                              "changed_by_role": E.ChangedByRole},
    "corrective_action": {"action_type": E.ActionType, "status": E.ActionStatus,
                          "verification_result": E.VerificationResult},
    "corrective_action_status_event": {"from_status": E.ActionStatus, "to_status": E.ActionStatus},
}


def v03_enums(t: Tables) -> CheckResult:
    res = CheckResult("V03", "structural", "Allowed enum values",
                      "Every categorical column holds one of its documented values.", 0)
    for table, cols in _ENUMS.items():
        for col, enum in cols.items():
            allowed = {e.value for e in enum}
            for r in t.get(table, []):
                if r[col] is None:
                    continue
                res.records_checked += 1
                if r[col] not in allowed:
                    res.failures.append(Failure(table, _pk(table, r), f"{col}={r[col]!r} is not an allowed value"))
    return res


def _expected_prefix(table: str, r: Mapping) -> str | None:
    return {
        "machine": f"{r.get('plant_id')}-M",
        "safety_zone": f"SAFE-{r.get('plant_id')}-",
        "interlock": f"ILK-{r.get('machine_id')}-",
        "operator_shift": f"SHF-{r.get('plant_id')}-{getattr(r.get('shift_date'), 'strftime', lambda f: '')('%Y%m%d')}-{r.get('shift_code')}-",
        "machine_state_event": f"MSE-{r.get('machine_id')}-",
        "interlock_state_event": f"ISE-{r.get('interlock_id')}-",
        "alarm_event": f"ALM-{r.get('machine_id')}-",
        "acknowledgement": f"ACK-{r.get('alarm_event_id')}-",
        "incident": f"INC-{getattr(r.get('opened_at'), 'year', '')}-",
        "incident_status_event": f"ISTAT-{r.get('incident_id')}-",
        "corrective_action": f"ACT-{r.get('incident_id')}-",
        "corrective_action_status_event": f"ASTAT-{r.get('corrective_action_id')}-",
    }.get(table)


_ID_REGEX = {
    "plant": r"^P\d{2}$", "machine": r"^P\d{2}-M\d{2,3}$", "safety_zone": r"^SAFE-P\d{2}-\d{3}$",
    "interlock": r"^ILK-P\d{2}-M\d{2,3}-\d{2}$", "alarm_definition": r"^ADEF-\d{4}$",
    "operator_shift": r"^SHF-P\d{2}-\d{8}-[ABC]-\d{2}$", "machine_state_event": r"^MSE-P\d{2}-M\d{2,3}-\d{6}$",
    "interlock_state_event": r"^ISE-ILK-P\d{2}-M\d{2,3}-\d{2}-\d{6}$", "alarm_event": r"^ALM-P\d{2}-M\d{2,3}-\d{6}$",
    "acknowledgement": r"^ACK-ALM-P\d{2}-M\d{2,3}-\d{6}-\d{2}$", "incident": r"^INC-\d{4}-\d{6}$",
    "incident_status_event": r"^ISTAT-INC-\d{4}-\d{6}-\d{2}$", "corrective_action": r"^ACT-INC-\d{4}-\d{6}-\d{2}$",
    "corrective_action_status_event": r"^ASTAT-ACT-INC-\d{4}-\d{6}-\d{2}-\d{2}$",
}


def v04_id_conventions(t: Tables) -> CheckResult:
    res = CheckResult("V04", "structural", "ID conventions",
                      "IDs match their documented pattern and embed the correct parent / year / shift.", 0)
    for table, pattern in _ID_REGEX.items():
        col = _pk_cols(table)[0]
        rx = re.compile(pattern)
        for r in t.get(table, []):
            res.records_checked += 1
            rid = r[col]
            if not rx.match(rid):
                res.failures.append(Failure(table, rid, f"ID does not match {pattern}"))
                continue
            prefix = _expected_prefix(table, r)
            if prefix and not rid.startswith(prefix):
                res.failures.append(Failure(table, rid, f"ID should start with {prefix!r}"))
    return res


def v05_timestamp_ordering(t: Tables) -> CheckResult:
    res = CheckResult("V05", "structural", "Timestamp ordering",
                      "Dates and timestamps inside a record and along each event history are in a valid order.", 0)

    def seq(table, r, fields, strict=()):
        vals = [(f, r[f]) for f in fields if r.get(f) is not None]
        res.records_checked += 1
        for (fa, a), (fb, b) in zip(vals, vals[1:]):
            bad = a >= b if (fa, fb) in strict else a > b
            if bad:
                res.failures.append(Failure(table, _pk(table, r), f"{fa} ({a}) must not be after {fb} ({b})"
                                            if (fa, fb) not in strict else f"{fa} ({a}) must be before {fb} ({b})"))

    for table in ("plant", "machine", "safety_zone", "interlock", "alarm_severity_policy", "alarm_definition",
                  "machine_safety_zone"):
        for r in t.get(table, []):
            seq(table, r, ["effective_from", "effective_to"])
    for r in t["operator_shift"]:
        seq("operator_shift", r, ["shift_start", "shift_end"], {("shift_start", "shift_end")})
    for r in t["alarm_event"]:
        seq("alarm_event", r, ["raised_at", "cleared_at"])
        seq("alarm_event", r, ["raised_at", "ack_deadline_at"], {("raised_at", "ack_deadline_at")})
    for r in t["incident"]:
        seq("incident", r, ["opened_at", "investigation_started_at", "resolved_at", "closed_at"])
    incidents = _idx(t["incident"], "incident_id")
    for r in t["corrective_action"]:
        seq("corrective_action", r, ["planned_at", "started_at", "completed_at", "verified_at", "closed_at"])
        seq("corrective_action", r, ["planned_at", "due_at"], {("planned_at", "due_at")})
        inc = incidents.get(r["incident_id"])
        if inc and r["planned_at"] < inc["opened_at"]:
            res.failures.append(Failure("corrective_action", r["corrective_action_id"], "planned before its incident opened"))
    for table, parent, ts in (("interlock_state_event", "interlock_id", "event_ts"),
                              ("incident_status_event", "incident_id", "changed_at"),
                              ("corrective_action_status_event", "corrective_action_id", "changed_at")):
        for pid, rows in _group(t[table], parent).items():
            rows.sort(key=lambda r: r["sequence_no"])
            res.records_checked += len(rows)
            for a, b in zip(rows, rows[1:]):
                if b[ts] < a[ts]:
                    res.failures.append(Failure(table, _pk(table, b), f"{ts} earlier than previous event of {pid}"))
    return res


# ============================================================ business-rule checks
def v06_machine_state_timeline(t: Tables) -> CheckResult:
    res = CheckResult("V06", "business rule", "Machine-state timeline and transitions",
                      "Per machine: sequence 1..n without gaps, strictly increasing time, previous_state chains, "
                      "every transition is in the allowed graph (R-I3).", 0)
    for mid, rows in _group(t["machine_state_event"], "machine_id").items():
        rows.sort(key=lambda r: r["sequence_no"])
        prev = None
        for n, r in enumerate(rows, start=1):
            res.records_checked += 1
            rid, problems = r["machine_state_event_id"], []
            if r["sequence_no"] != n:
                problems.append(f"sequence_no {r['sequence_no']} expected {n}")
            if prev is None and r["previous_state"] is not None:
                problems.append("first event has a previous_state")
            if prev is not None:
                if r["previous_state"] != prev["machine_state"]:
                    problems.append(f"previous_state {r['previous_state']} != {prev['machine_state']}")
                if r["event_ts"] <= prev["event_ts"]:
                    problems.append("event_ts is not later than the previous event")
                if not is_valid_transition(prev["machine_state"], r["machine_state"]):
                    problems.append(f"illegal transition {prev['machine_state']} -> {r['machine_state']}")
            res.failures += [Failure("machine_state_event", rid, p) for p in problems]
            prev = r
    return res


def v07_possible_interlock_states(t: Tables) -> CheckResult:
    res = CheckResult("V07", "business rule", "Interlock / state combinations are possible",
                      "No door interlock ENGAGED while the door is OPEN (R-I1), no bypass where it is not allowed "
                      "(R-I4), stored interlock summary equals the summary derived from interlock history (R-I2).",
                      len(t["machine_state_event"]))
    res.failures = [Failure(f.record_table, f.record_id, f.reason) for f in detect_impossible_interlock_states(
        t["machine_state_event"], t["interlock_state_event"], t["interlock"])]
    return res


def v08_acknowledgement_timelines(t: Tables) -> CheckResult:
    res = CheckResult("V08", "business rule", "Acknowledgement timelines",
                      "Acknowledgement is never earlier than its alarm, at most one per alarm, and by a shift of the "
                      "machine's own plant.", 0)
    alarms = _idx(t["alarm_event"], "alarm_event_id")
    machine_plant = {m["machine_id"]: m["plant_id"] for m in t["machine"]}
    shift_plant = {s["shift_id"]: s["plant_id"] for s in t["operator_shift"]}
    per_alarm = Counter(k["alarm_event_id"] for k in t["acknowledgement"])
    for k in t["acknowledgement"]:
        res.records_checked += 1
        a = alarms.get(k["alarm_event_id"])
        if a is None:
            continue  # reported by V02
        if k["acknowledged_at"] < a["raised_at"]:
            res.failures.append(Failure("acknowledgement", k["acknowledgement_id"],
                                        f"acknowledged {(a['raised_at'] - k['acknowledged_at']).total_seconds():.0f} s "
                                        "before the alarm was raised"))
        if per_alarm[k["alarm_event_id"]] > 1:
            res.failures.append(Failure("acknowledgement", k["acknowledgement_id"], "alarm has more than one acknowledgement"))
        if shift_plant.get(k["acknowledged_by_shift_id"]) != machine_plant.get(a["machine_id"]):
            res.failures.append(Failure("acknowledgement", k["acknowledgement_id"], "acknowledged by another plant's shift"))
    return res


def v09_alarm_derived_values(t: Tables) -> CheckResult:
    res = CheckResult("V09", "business rule", "Alarm derived values",
                      "Deadline = raised_at + policy(severity); lifecycle matches cleared_at; the referenced machine state is "
                      "the latest one at raised_at; severity equals the AL-1 classification.", len(t["alarm_event"]))
    policy = {p["severity"]: p["ack_deadline_seconds"] for p in t["alarm_severity_policy"]}
    defs = _idx(t["alarm_definition"], "alarm_definition_id")
    zones = _idx(t["safety_zone"], "safety_zone_id")
    mse = _idx(t["machine_state_event"], "machine_state_event_id")
    per_machine_ts, per_machine_rows = defaultdict(list), defaultdict(list)
    for r in sorted(t["machine_state_event"], key=lambda r: (r["machine_id"], r["sequence_no"])):
        per_machine_ts[r["machine_id"]].append(r["event_ts"])
        per_machine_rows[r["machine_id"]].append(r)
    for a in t["alarm_event"]:
        rid, problems = a["alarm_event_id"], []
        dl = policy.get(a["severity"])
        if dl is None or (a["ack_deadline_at"] - a["raised_at"]).total_seconds() != dl:
            problems.append("ack_deadline_at != raised_at + policy deadline")
        if (a["lifecycle_status"] == "CLEARED") != (a["cleared_at"] is not None):
            problems.append("lifecycle_status inconsistent with cleared_at")
        ts = per_machine_ts[a["machine_id"]]
        i = bisect_right(ts, a["raised_at"]) - 1
        latest = per_machine_rows[a["machine_id"]][i] if i >= 0 else None
        if latest is None or a["machine_state_event_id"] != latest["machine_state_event_id"]:
            problems.append("machine_state_event_id is not the state in effect at raised_at")
        d = defs.get(a["alarm_definition_id"])
        st = mse.get(a["machine_state_event_id"])
        if d and st:
            zone = zones.get(a["safety_zone_id"])
            expect = classify_severity(d["default_severity"], st["safety_assessment"], d["is_safety_related"],
                                       zone["risk_level"] if zone else None)
            if expect != a["severity"]:
                problems.append(f"severity {a['severity']} != classified {expect}")
        res.failures += [Failure("alarm_event", rid, p) for p in problems]
    return res


def v10_acknowledgement_derived_values(t: Tables) -> CheckResult:
    res = CheckResult("V10", "business rule", "Acknowledgement derived values",
                      "ack_delay_seconds and ack_status are recomputed from the timestamps and deadline.",
                      len(t["acknowledgement"]))
    alarms = _idx(t["alarm_event"], "alarm_event_id")
    for k in t["acknowledgement"]:
        a = alarms.get(k["alarm_event_id"])
        if a is None:
            continue
        if abs(_num(k["ack_delay_seconds"]) - ack_delay_seconds(a["raised_at"], k["acknowledged_at"])) > 0.051:
            res.failures.append(Failure("acknowledgement", k["acknowledgement_id"], "ack_delay_seconds != acknowledged_at - raised_at"))
        if k["ack_status"] != ack_status(k["acknowledged_at"], a["ack_deadline_at"]):
            res.failures.append(Failure("acknowledgement", k["acknowledgement_id"], "ack_status inconsistent with the deadline"))
    return res


def v11_incident_traceability(t: Tables) -> CheckResult:
    res = CheckResult("V11", "business rule", "Incident traceability and trigger context",
                      "Each incident has exactly one TRIGGER alarm (HIGH/CRITICAL, on the same machine, raised at opening) and "
                      "an UNSAFE trigger machine state with the matching rule code; all linked alarms belong to the machine; "
                      "stored severity follows IN-2.", len(t["incident"]))
    alarms = _idx(t["alarm_event"], "alarm_event_id")
    mse = _idx(t["machine_state_event"], "machine_state_event_id")
    links = _group(t["incident_alarm_link"], "incident_id")
    for inc in t["incident"]:
        rid, problems = inc["incident_id"], []
        ls = links.get(rid, [])
        triggers = [l for l in ls if l["link_role"] == "TRIGGER"]
        if len(triggers) != 1:
            problems.append(f"{len(triggers)} TRIGGER alarm links (expected 1)")
        else:
            a = alarms.get(triggers[0]["alarm_event_id"])
            if a:
                if a["severity"] not in ("HIGH", "CRITICAL"):
                    problems.append("trigger alarm is not HIGH/CRITICAL")
                if a["raised_at"] != inc["opened_at"]:
                    problems.append("incident not opened at the trigger alarm's raised_at")
        if inc["trigger_machine_state_event_id"] is None:
            problems.append("no trigger machine state")
        else:
            st = mse.get(inc["trigger_machine_state_event_id"])
            if st:
                if st["machine_id"] != inc["machine_id"]:
                    problems.append("trigger state belongs to another machine")
                if st["safety_assessment"] != "UNSAFE" or st["unsafe_rule_code"] != inc["trigger_rule_code"]:
                    problems.append("trigger state is not the UNSAFE state named by trigger_rule_code")
        linked = [alarms[l["alarm_event_id"]] for l in ls if l["alarm_event_id"] in alarms]
        if any(a["machine_id"] != inc["machine_id"] for a in linked):
            problems.append("linked alarm belongs to another machine")
        if linked and inc["severity"] != incident_severity([a["severity"] for a in linked]):
            problems.append("severity differs from IN-2 derived from linked alarms")
        res.failures += [Failure("incident", rid, p) for p in problems]
    return res


def v12_incident_lifecycle(t: Tables) -> CheckResult:
    res = CheckResult("V12", "business rule", "Incident lifecycle consistency",
                      "Status history is gapless and uses allowed transitions; status and milestone timestamps equal the "
                      "history; CLOSED needs >= 1 corrective action and all live actions properly closed (IN-4).",
                      len(t["incident"]))
    events = _group(t["incident_status_event"], "incident_id")
    actions = _group(t["corrective_action"], "incident_id")
    for inc in t["incident"]:
        rid, problems = inc["incident_id"], []
        evs = sorted(events.get(rid, []), key=lambda e: e["sequence_no"])
        if not evs:
            problems.append("no status history")
        else:
            prev = None
            for n, e in enumerate(evs, start=1):
                if e["sequence_no"] != n:
                    problems.append(f"status sequence_no {e['sequence_no']} expected {n}")
                if e["from_status"] != prev or not valid_incident_transition(prev, e["to_status"]):
                    problems.append(f"invalid status transition {prev} -> {e['to_status']}")
                prev = e["to_status"]
            if evs[0]["changed_at"] != inc["opened_at"]:
                problems.append("first status event is not at opened_at")
            if inc["status"] != evs[-1]["to_status"]:
                problems.append(f"status {inc['status']} != latest history {evs[-1]['to_status']}")
            stamps = {e["to_status"]: e["changed_at"] for e in evs}
            for col, status in (("investigation_started_at", "INVESTIGATING"), ("resolved_at", "RESOLVED"),
                                ("closed_at", "CLOSED")):
                if inc[col] != stamps.get(status):
                    problems.append(f"{col} does not equal the {status} history event")
        if inc["status"] == "CLOSED" and not can_close_incident(actions.get(rid, [])):
            problems.append("CLOSED without all live corrective actions properly closed (IN-4)")
        res.failures += [Failure("incident", rid, p) for p in problems]
    return res


def v13_corrective_actions(t: Tables) -> CheckResult:
    res = CheckResult("V13", "business rule", "Corrective-action verification and lifecycle",
                      "A CLOSED action has a PASSED, independent, correctly ordered verification (CA-3); status history uses "
                      "allowed transitions and matches status and timestamps; action numbers per incident have no gaps.",
                      len(t["corrective_action"]))
    events = _group(t["corrective_action_status_event"], "corrective_action_id")
    per_inc = defaultdict(list)
    for a in t["corrective_action"]:
        per_inc[a["incident_id"]].append(int(a["corrective_action_id"].rsplit("-", 1)[1]))
    for a in t["corrective_action"]:
        rid, problems = a["corrective_action_id"], []
        if not action_closed_properly(a):
            problems.append("closed without a valid verification (CA-3)")
        if (a["verified_at"] is None) != (a["verification_result"] is None):
            problems.append("verified_at and verification_result must be set together")
        evs = sorted(events.get(rid, []), key=lambda e: e["sequence_no"])
        if not evs:
            problems.append("no status history")
        else:
            prev = None
            for n, e in enumerate(evs, start=1):
                if e["sequence_no"] != n:
                    problems.append(f"status sequence_no {e['sequence_no']} expected {n}")
                if e["from_status"] != prev:
                    problems.append("from_status does not chain")
                elif not valid_action_transition(prev, e["to_status"]):
                    problems.append(f"invalid status transition {prev} -> {e['to_status']}")
                prev = e["to_status"]
            if a["status"] != evs[-1]["to_status"]:
                problems.append(f"status {a['status']} != latest history {evs[-1]['to_status']}")
            stamps = {e["to_status"]: e["changed_at"] for e in evs}
            for col, status in (("started_at", "IN_PROGRESS"), ("completed_at", "COMPLETED"),
                                ("verified_at", "VERIFIED"), ("closed_at", "CLOSED")):
                if a[col] != stamps.get(status):
                    problems.append(f"{col} does not equal the {status} history event")
        res.failures += [Failure("corrective_action", rid, p) for p in problems]
    for iid, nums in per_inc.items():
        if sorted(nums) != list(range(1, len(nums) + 1)):
            res.failures.append(Failure("corrective_action", iid, f"action numbers {sorted(nums)} have gaps"))
    return res


def v14_duplicates(t: Tables) -> CheckResult:
    res = CheckResult("V14", "business rule", "Duplicate records",
                      "No alarm reading is ingested twice under different batches (replay), and natural keys "
                      "(names, codes, shift staffing) are unique.", 0)
    groups = defaultdict(list)
    for a in t["alarm_event"]:
        res.records_checked += 1
        groups[(a["machine_id"], a["alarm_definition_id"], a["raised_at"].replace(microsecond=0))].append(a)
    for members in groups.values():
        if len({m["ingest_batch_id"] for m in members}) > 1:
            members.sort(key=lambda m: (m["ingest_batch_id"], m["alarm_event_id"]))
            first = members[0]["ingest_batch_id"]
            res.failures += [Failure("alarm_event", m["alarm_event_id"], f"duplicate of a reading already ingested in {first}")
                             for m in members if m["ingest_batch_id"] != first]
    natural = {
        "plant": ["plant_name"], "machine": ["plant_id", "machine_name"], "safety_zone": ["plant_id", "zone_name"],
        "alarm_definition": ["alarm_code"], "alarm_severity_policy": ["priority_rank"],
        "operator_shift": ["plant_id", "shift_date", "shift_code", "employee_id"], "acknowledgement": ["alarm_event_id"],
    }
    for table, cols in natural.items():
        seen = defaultdict(list)
        for r in t[table]:
            res.records_checked += 1
            seen[tuple(r[c] for c in cols)].append(r)
        for key, rows in seen.items():
            if len(rows) > 1:
                res.failures += [Failure(table, _pk(table, r), f"duplicate natural key {cols}={key}") for r in rows[1:]]
    return res


def v15_reconciliation(t: Tables) -> CheckResult:
    res = CheckResult("V15", "business rule", "Arithmetic reconciliation",
                      "Totals reconcile across dimensions: alarms by severity / machine / plant equal the total; acknowledgement "
                      "statuses recounted from timestamps equal the stored statuses; acknowledgements do not exceed alarms; "
                      "incident links reconcile.", 0)
    alarms = t["alarm_event"]
    machine_plant = {m["machine_id"]: m["plant_id"] for m in t["machine"]}

    def expect(name, left, right):
        res.records_checked += 1
        if left != right:
            res.failures.append(Failure("reconciliation", name, f"stored/aggregate value {left} != recomputed value {right}"))

    total = len(alarms)
    expect("alarms by severity sum", sum(Counter(a["severity"] for a in alarms).values()), total)
    expect("alarms by machine sum", sum(Counter(a["machine_id"] for a in alarms).values()), total)
    expect("alarms by plant sum", sum(Counter(machine_plant.get(a["machine_id"]) for a in alarms).values()), total)
    acks = t["acknowledgement"]
    expect("acknowledgements <= alarms (shortfall)", max(0, len(acks) - total), 0)
    by_alarm = _idx(alarms, "alarm_event_id")
    stored = Counter(k["ack_status"] for k in acks)
    recount = Counter(ack_status(k["acknowledged_at"], by_alarm[k["alarm_event_id"]]["ack_deadline_at"])
                      for k in acks if k["alarm_event_id"] in by_alarm)
    expect("ON_TIME acknowledgements recount", stored["ON_TIME"], recount["ON_TIME"])
    expect("LATE acknowledgements recount", stored["LATE"], recount["LATE"])
    expect("ON_TIME + LATE = acknowledgements", stored["ON_TIME"] + stored["LATE"], len(acks))
    links = t["incident_alarm_link"]
    expect("incident links by incident sum", sum(Counter(l["incident_id"] for l in links).values()), len(links))
    expect("incident links by alarm role sum", sum(Counter(l["link_role"] for l in links).values()), len(links))
    return res


def v16_cardinality(t: Tables) -> CheckResult:
    res = CheckResult("V16", "business rule", "Relationship cardinality",
                      "Each machine has exactly one primary safety zone and >= 1 required interlock; each machine has exactly one "
                      "operator per shift; interlock zones are linked to their machine; bridge rows join rows of the same "
                      "plant; each incident / action has a status history.", 0)
    machines = t["machine"]
    plant_of = {m["machine_id"]: m["plant_id"] for m in machines}
    primaries = Counter(r["machine_id"] for r in t["machine_safety_zone"] if r["is_primary"])
    required = Counter(i["machine_id"] for i in t["interlock"] if i["is_required"])
    for m in machines:
        res.records_checked += 1
        mid = m["machine_id"]
        if primaries[mid] != 1:
            res.failures.append(Failure("machine", mid, f"{primaries[mid]} primary safety zones (expected 1)"))
        if required[mid] < 1:
            res.failures.append(Failure("machine", mid, "no required interlock"))
    zone_plant = {z["safety_zone_id"]: z["plant_id"] for z in t["safety_zone"]}
    linked = {(r["machine_id"], r["safety_zone_id"]) for r in t["machine_safety_zone"]}
    for r in t["machine_safety_zone"]:
        res.records_checked += 1
        if plant_of.get(r["machine_id"]) != zone_plant.get(r["safety_zone_id"]):
            res.failures.append(Failure("machine_safety_zone", _pk("machine_safety_zone", r), "machine and zone are in different plants"))
    for i in t["interlock"]:
        res.records_checked += 1
        if i["safety_zone_id"] and (i["machine_id"], i["safety_zone_id"]) not in linked:
            res.failures.append(Failure("interlock", i["interlock_id"], "zone is not linked to the machine"))
    shifts = _idx(t["operator_shift"], "shift_id")
    per = Counter()
    for r in t["shift_machine_assignment"]:
        res.records_checked += 1
        s = shifts.get(r["shift_id"])
        if s is None:
            continue
        if s["plant_id"] != plant_of.get(r["machine_id"]):
            res.failures.append(Failure("shift_machine_assignment", _pk("shift_machine_assignment", r), "machine and shift are in different plants"))
        per[(s["plant_id"], s["shift_date"], s["shift_code"], r["machine_id"])] += 1
    for key, n in per.items():
        if n != 1:
            res.failures.append(Failure("shift_machine_assignment", "|".join(map(str, key)), f"{n} operators assigned (expected 1)"))
    inc_events = Counter(e["incident_id"] for e in t["incident_status_event"])
    for i in t["incident"]:
        res.records_checked += 1
        if inc_events[i["incident_id"]] < 1:
            res.failures.append(Failure("incident", i["incident_id"], "no status history"))
    act_events = Counter(e["corrective_action_id"] for e in t["corrective_action_status_event"])
    for a in t["corrective_action"]:
        res.records_checked += 1
        if act_events[a["corrective_action_id"]] < 1:
            res.failures.append(Failure("corrective_action", a["corrective_action_id"], "no status history"))
    return res


def v17_state_derived_values(t: Tables) -> CheckResult:
    res = CheckResult("V17", "business rule", "Machine-state derived values",
                      "Stored interlock summary, safety assessment and rule code equal the values re-derived by replaying the "
                      "interlock history with the rules engine.", len(t["machine_state_event"]))
    for r in replay_machine_states(t["machine_state_event"], t["interlock_state_event"], t["interlock"]):
        row = r.row
        problems = []
        if not r.summary_ok:
            problems.append(f"interlock_status_summary {row['interlock_status_summary']} != derived {r.derived_summary}")
        if (row["safety_assessment"], row["unsafe_rule_code"]) != r.derived:
            problems.append(f"assessment {(row['safety_assessment'], row['unsafe_rule_code'])} != derived {r.derived}")
        res.failures += [Failure("machine_state_event", row["machine_state_event_id"], p) for p in problems]
    return res


CHECKS = [v01_primary_keys, v02_foreign_keys, v03_enums, v04_id_conventions, v05_timestamp_ordering,
          v06_machine_state_timeline, v07_possible_interlock_states, v08_acknowledgement_timelines,
          v09_alarm_derived_values, v10_acknowledgement_derived_values, v11_incident_traceability,
          v12_incident_lifecycle, v13_corrective_actions, v14_duplicates, v15_reconciliation, v16_cardinality,
          v17_state_derived_values]
