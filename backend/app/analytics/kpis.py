"""BUSINESS KPIs - they describe the plant's safety performance, not how well a detector works.

(Detector quality - precision / recall / F1 - lives in analytics/evaluation.py and is deliberately separate.)
Pure functions over the feature tables; every number is computed from rows, nothing is hard-coded.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from app.analytics.filters import Filters, apply_filters
from app.analytics.prep import Prepared, infer_cutoff, prepare
from app.business_rules.alarm_patterns import detect_floods, detect_nuisance_clusters, detect_repeated_alarms
from app.business_rules.config import DEFAULT_RULES, RulesConfig
from app.business_rules.corrective_action_rules import completion_rate, verification_rate
from app.business_rules.incident_rules import find_repeated_unsafe, find_unsafe_episodes

Tables = Mapping[str, Sequence[Mapping]]
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
OPEN_STATES = {"OPEN", "INVESTIGATING", "ACTION_IN_PROGRESS"}          # not yet resolved
BUCKETS = [("<= 1 min", 60), ("1-5 min", 300), ("5-15 min", 900), ("15-60 min", 3600), ("> 60 min", math.inf)]


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _pct(num, den):
    return None if not den else round(100.0 * num / den, 2)


def _iso(dt: datetime) -> str:
    """Timestamps leave the KPI layer in UTC whatever time zone the database session used."""
    return dt.astimezone(timezone.utc).isoformat()


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p90": None, "max": None}
    v = sorted(values)
    p90 = v[min(len(v) - 1, math.ceil(0.9 * len(v)) - 1)]
    return {"count": len(v), "mean": _r(statistics.fmean(v)), "median": _r(statistics.median(v)), "p90": _r(p90),
            "max": _r(v[-1])}


# ------------------------------------------------------------------ alarms
def alarm_counts(prep: Prepared) -> dict:
    t = prep.tables
    machines = {m["machine_id"]: m for m in t["machine"]}
    plants = {p["plant_id"]: p for p in t["plant"]}
    by_plant, by_machine = defaultdict(Counter), defaultdict(Counter)
    for a in prep.alarms:
        pid = machines[a["machine_id"]]["plant_id"]
        for key, bucket in ((pid, by_plant), (a["machine_id"], by_machine)):
            bucket[key]["alarms"] += 1
            bucket[key][a["severity"]] += 1
    total = len(prep.alarms)
    plant_rows = [dict(plant_id=pid, plant_name=plants[pid]["plant_name"], alarms=c["alarms"],
                       **{s.lower(): c[s] for s in SEVERITIES}) for pid, c in sorted(by_plant.items())]
    machine_rows = [dict(machine_id=mid, machine_name=machines[mid]["machine_name"], plant_id=machines[mid]["plant_id"],
                         alarms=c["alarms"], **{s.lower(): c[s] for s in SEVERITIES},
                         share_pct=_pct(c["alarms"], total)) for mid, c in by_machine.items()]
    machine_rows.sort(key=lambda r: (-r["alarms"], r["machine_id"]))
    sev = Counter(a["severity"] for a in prep.alarms)
    return {"total": total, "by_severity": {s: sev[s] for s in SEVERITIES}, "critical": sev["CRITICAL"],
            "high": sev["HIGH"], "critical_and_high": sev["CRITICAL"] + sev["HIGH"], "by_plant": plant_rows,
            "by_machine": machine_rows}


# ------------------------------------------------------------------ acknowledgements
def acknowledgement_kpis(prep: Prepared) -> dict:
    """on_time / late / missed / pending use the AL-4 rules; invalid (pre-alarm) acknowledgements are excluded."""
    rows = []
    for a in prep.alarms:
        k = prep.ack_by_alarm.get(a["alarm_event_id"])
        if k is not None:
            state = "ON_TIME" if k["acknowledged_at"] <= a["ack_deadline_at"] else "LATE"
            delay = (k["acknowledged_at"] - a["raised_at"]).total_seconds()
        else:
            state, delay = ("MISSED" if a["ack_deadline_at"] < prep.cutoff else "PENDING"), None
        rows.append((a, state, delay))
    c = Counter(s for _, s, _ in rows)
    delays = [d for _, _, d in rows if d is not None]
    due = c["ON_TIME"] + c["LATE"] + c["MISSED"]
    by_sev = {}
    for s in SEVERITIES:
        sub = [(st, d) for a, st, d in rows if a["severity"] == s]
        sc = Counter(st for st, _ in sub)
        sd = [d for _, d in sub if d is not None]
        sdue = sc["ON_TIME"] + sc["LATE"] + sc["MISSED"]
        by_sev[s] = {"alarms": len(sub), "acknowledged": len(sd), "late": sc["LATE"], "missed": sc["MISSED"],
                     "avg_ack_seconds": _r(statistics.fmean(sd)) if sd else None,
                     "delayed_pct": _pct(sc["LATE"] + sc["MISSED"], sdue)}
    buckets, lo = [], 0
    for label, hi in BUCKETS:
        buckets.append({"bucket": label, "count": sum(1 for d in delays if lo < d <= hi or (lo == 0 and d == 0))})
        lo = hi
    return {"alarms": len(rows), "acknowledged": len(delays), "on_time": c["ON_TIME"], "late": c["LATE"],
            "missed": c["MISSED"], "pending": c["PENDING"], "delayed_total": c["LATE"] + c["MISSED"],
            "delayed_pct": _pct(c["LATE"] + c["MISSED"], due), "late_pct_of_acknowledged": _pct(c["LATE"], len(delays)),
            "unacknowledged_pct": _pct(c["MISSED"], due), "ack_seconds": _stats(delays), "by_severity": by_sev,
            "delay_buckets": buckets, "invalid_acknowledgements_excluded": len(prep.invalid_ack_ids),
            "definition": "delayed % = (late + missed) / (on-time + late + missed); pending alarms (deadline not yet "
                          "reached at the cut-off) are excluded"}


# ------------------------------------------------------------------ alarm patterns
def _span_days(alarms: Sequence[Mapping]) -> float:
    if not alarms:
        return 1.0
    ts = [a["raised_at"] for a in alarms]
    return max(1.0, (max(ts) - min(ts)).total_seconds() / 86400)


def flood_kpis(prep: Prepared, rules: RulesConfig) -> dict:
    machines = {m["machine_id"]: m for m in prep.tables["machine"]}
    eps = detect_floods(prep.alarms, rules)
    per_machine = Counter(e.machine_id for e in eps)
    per_plant = Counter(machines[e.machine_id]["plant_id"] for e in eps)
    days = _span_days(prep.alarms)
    return {"episodes": len(eps), "alarms_in_floods": sum(e.alarm_count for e in eps),
            "alarms_in_floods_pct": _pct(sum(e.alarm_count for e in eps), len(prep.alarms)),
            "floods_per_day": _r(len(eps) / days, 3), "by_machine": dict(sorted(per_machine.items())),
            "by_plant": dict(sorted(per_plant.items())),
            "episodes_detail": [dict(machine_id=e.machine_id, start=_iso(e.start), end=_iso(e.end),
                                     alarm_count=e.alarm_count,
                                     duration_minutes=_r((e.end - e.start).total_seconds() / 60)) for e in eps]}


def nuisance_kpis(prep: Prepared, rules: RulesConfig) -> dict:
    defs = {d["alarm_definition_id"]: d for d in prep.tables["alarm_definition"]}
    clusters = detect_nuisance_clusters(prep.alarms, rules)
    n_alarms = sum(len(c.alarm_ids) for c in clusters)
    return {"clusters": len(clusters), "alarms_in_clusters": n_alarms,
            "alarms_in_clusters_pct": _pct(n_alarms, len(prep.alarms)),
            "by_rule": dict(Counter(r for c in clusters for r in c.rules)),
            "detail": [dict(machine_id=c.machine_id, alarm_definition_id=c.alarm_definition_id,
                            alarm_code=defs[c.alarm_definition_id]["alarm_code"], rules=sorted(c.rules),
                            alarms=len(c.alarm_ids), first=_iso(c.first), last=_iso(c.last))
                       for c in sorted(clusters, key=lambda c: -len(c.alarm_ids))]}


def repeated_alarm_kpis(prep: Prepared, rules: RulesConfig) -> dict:
    pats = detect_repeated_alarms(prep.alarms, rules)
    return {"patterns": len(pats), "alarms_in_patterns": sum(len(p.alarm_ids) for p in pats),
            "top": [dict(machine_id=p.machine_id, alarm_definition_id=p.alarm_definition_id,
                         max_in_24h=p.max_in_window, alarms=len(p.alarm_ids))
                    for p in sorted(pats, key=lambda p: -p.max_in_window)[:10]]}


# ------------------------------------------------------------------ unsafe conditions
def unsafe_kpis(prep: Prepared, rules: RulesConfig) -> dict:
    states = prep.tables["machine_state_event"]
    eps = find_unsafe_episodes(states, prep.cutoff)
    groups = find_repeated_unsafe(eps, rules)
    by_machine, by_rule = Counter(e.machine_id for e in eps), Counter(e.rule_code for e in eps)
    repeated = [dict(machine_id=g[0].machine_id, rule_code=g[0].rule_code, episodes=len(g),
                     first=_iso(g[0].start_ts), last=_iso(g[-1].start_ts)) for g in groups]
    days = _span_days(prep.alarms) if prep.alarms else 1.0
    return {"unsafe_episodes": len(eps), "by_rule": dict(sorted(by_rule.items())),
            "by_machine": dict(by_machine.most_common()), "unsafe_seconds_total": _r(sum(e.duration_seconds for e in eps), 0),
            "repeated_conditions": len(groups), "episodes_in_repeated_conditions": sum(len(g) for g in groups),
            "repeated_conditions_detail": sorted(repeated, key=lambda r: (-r["episodes"], r["machine_id"])),
            "episodes_per_day": _r(len(eps) / days, 3)}


# ------------------------------------------------------------------ incidents
def incident_kpis(prep: Prepared) -> dict:
    t = prep.tables
    machines = {m["machine_id"]: m for m in t["machine"]}
    inc = t["incident"]
    by_machine = defaultdict(Counter)
    for i in inc:
        c = by_machine[i["machine_id"]]
        c["incidents"] += 1
        c[i["severity"]] += 1
        c["open"] += i["status"] != "CLOSED"
    machine_rows = [dict(machine_id=mid, machine_name=machines[mid]["machine_name"], plant_id=machines[mid]["plant_id"],
                         incidents=c["incidents"], critical=c["CRITICAL"], high=c["HIGH"], open=c["open"])
                    for mid, c in by_machine.items()]
    machine_rows.sort(key=lambda r: (-r["incidents"], r["machine_id"]))
    resolved = [i for i in inc if i["resolved_at"]]
    res_h = [_hours(i["resolved_at"] - i["opened_at"]) for i in resolved]
    inv_h = [_hours(i["investigation_started_at"] - i["opened_at"]) for i in inc if i["investigation_started_at"]]
    by_sev_mttr = {}
    for s in ("CRITICAL", "HIGH", "MEDIUM"):
        v = [_hours(i["resolved_at"] - i["opened_at"]) for i in resolved if i["severity"] == s]
        by_sev_mttr[s] = _r(statistics.fmean(v)) if v else None
    open_rows = sorted((i for i in inc if i["status"] != "CLOSED"), key=lambda i: i["opened_at"])
    status = Counter(i["status"] for i in inc)
    return {"total": len(inc), "critical": sum(i["severity"] == "CRITICAL" for i in inc),
            "by_severity": dict(Counter(i["severity"] for i in inc)), "by_status": dict(status),
            "by_trigger_rule": dict(Counter(i["trigger_rule_code"] or "UNKNOWN" for i in inc)),
            "open": len(open_rows), "unresolved": sum(i["status"] in OPEN_STATES for i in inc),
            "mttr_hours": _r(statistics.fmean(res_h)) if res_h else None,
            "mttr_median_hours": _r(statistics.median(res_h)) if res_h else None, "mttr_by_severity_hours": by_sev_mttr,
            "resolved_count": len(resolved), "mean_time_to_investigate_hours": _r(statistics.fmean(inv_h)) if inv_h else None,
            "by_machine": machine_rows,
            "open_incidents": [dict(incident_id=i["incident_id"], machine_id=i["machine_id"], severity=i["severity"],
                                    status=i["status"], opened_at=_iso(i["opened_at"]),
                                    age_hours=_r(_hours(prep.cutoff - i["opened_at"]), 1)) for i in open_rows],
            "definition": "MTTR = mean(resolved_at - opened_at) over incidents with a resolved_at; open = not CLOSED; "
                          "unresolved = OPEN / INVESTIGATING / ACTION_IN_PROGRESS"}


# ------------------------------------------------------------------ corrective actions
def action_kpis(prep: Prepared) -> dict:
    acts = prep.tables["corrective_action"]
    live = [a for a in acts if a["status"] != "CANCELLED"]
    overdue = [a for a in live if a["status"] not in ("CLOSED",) and a["due_at"] < prep.cutoff]
    closed_days = [(a["closed_at"] - a["planned_at"]).total_seconds() / 86400 for a in acts if a["closed_at"]]
    return {"total": len(acts), "live": len(live), "by_status": dict(Counter(a["status"] for a in acts)),
            "completion_rate_pct": _r(100 * completion_rate(acts)), "verification_rate_pct": _r(100 * verification_rate(acts)),
            "failed_verifications": sum(a["verification_result"] == "FAILED" for a in acts),
            "closed_without_verification": sum(a["status"] == "CLOSED" and not a["verification_result"] for a in acts),
            "overdue": len(overdue), "avg_days_to_close": _r(statistics.fmean(closed_days)) if closed_days else None,
            "definition": "completion = status COMPLETED/VERIFIED/CLOSED among non-cancelled actions; verification = "
                          "verification result recorded among completed actions"}


# ------------------------------------------------------------------ time series (dashboard)
def alarms_per_day(prep: Prepared) -> list[dict]:
    per = defaultdict(Counter)
    for a in prep.alarms:
        per[a["raised_at"].astimezone(timezone.utc).date()][a["severity"]] += 1
    return [dict(day=d.isoformat(), total=sum(c.values()), **{s.lower(): c[s] for s in SEVERITIES})
            for d, c in sorted(per.items())]


# ------------------------------------------------------------------ everything
def compute_kpis(tables: Tables, filters: Filters | None = None, cutoff: datetime | None = None,
                 rules: RulesConfig = DEFAULT_RULES) -> dict:
    cutoff = cutoff or infer_cutoff(tables)                  # snapshot time of the whole data set, not of a filter
    sub = apply_filters(tables, filters)
    prep = prepare(sub, cutoff)
    alarms, acks = alarm_counts(prep), acknowledgement_kpis(prep)
    unsafe, inc = unsafe_kpis(prep, rules), incident_kpis(prep)
    floods, nuis = flood_kpis(prep, rules), nuisance_kpis(prep, rules)
    return {
        "meta": {"kpi_type": "business", "cutoff": _iso(cutoff), "filters": (filters.as_dict() if filters else {}),
                 "note": "Replay duplicates and invalid acknowledgements are excluded from the maths and counted separately."},
        "overview": {
            "plants": len(sub["plant"]), "machines": len(sub["machine"]),
            "alarm_events_stored": len(sub["alarm_event"]), "alarm_events_analysed": alarms["total"],
            "replay_duplicates_excluded": len(prep.duplicate_alarm_ids),
            "critical_alarms": alarms["critical"], "high_alarms": alarms["high"],
            "incidents": inc["total"], "critical_incidents": inc["critical"], "open_incidents": inc["open"],
            "unresolved_incidents": inc["unresolved"], "delayed_acknowledgements": acks["delayed_total"],
            "delayed_acknowledgement_pct": acks["delayed_pct"], "repeated_unsafe_conditions": unsafe["repeated_conditions"],
            "alarm_floods": floods["episodes"], "nuisance_clusters": nuis["clusters"]},
        "alarms": alarms, "acknowledgements": acks, "floods": floods, "nuisance": nuis,
        "repeated_alarms": repeated_alarm_kpis(prep, rules), "unsafe_conditions": unsafe, "incidents": inc,
        "corrective_actions": action_kpis(prep), "alarms_per_day": alarms_per_day(prep),
    }
