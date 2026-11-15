"""Milestone 3 tests: starter dataset structure, IDs, joins, timelines, rule consistency, reproducibility, loading."""
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.business_rules.alarm_rules import ack_deadline_at, ack_delay_seconds, ack_status
from app.business_rules.corrective_action_rules import (
    action_closed_properly, can_close_incident, valid_action_transition, valid_incident_transition,
)
from app.business_rules.safety_state import (
    DOOR_TYPES, InterlockReading, assess_state, is_valid_transition, summarize_interlocks,
)
from app.models import Base
from data_generator.config import STARTER
from data_generator.export import TABLE_ORDER, table_columns, write_exports, write_ground_truth
from data_generator.generate import generate
from data_generator.loader import load_dataset

ID_PATTERNS = {
    "plant": ("plant_id", r"^P\d{2}$"),
    "machine": ("machine_id", r"^P\d{2}-M\d{2,3}$"),
    "safety_zone": ("safety_zone_id", r"^SAFE-P\d{2}-\d{3}$"),
    "interlock": ("interlock_id", r"^ILK-P\d{2}-M\d{2,3}-\d{2}$"),
    "alarm_definition": ("alarm_definition_id", r"^ADEF-\d{4}$"),
    "operator_shift": ("shift_id", r"^SHF-P\d{2}-\d{8}-[ABC]-\d{2}$"),
    "machine_state_event": ("machine_state_event_id", r"^MSE-P\d{2}-M\d{2,3}-\d{6}$"),
    "interlock_state_event": ("interlock_state_event_id", r"^ISE-ILK-P\d{2}-M\d{2,3}-\d{2}-\d{6}$"),
    "alarm_event": ("alarm_event_id", r"^ALM-P\d{2}-M\d{2,3}-\d{6}$"),
    "acknowledgement": ("acknowledgement_id", r"^ACK-ALM-P\d{2}-M\d{2,3}-\d{6}-\d{2}$"),
    "incident": ("incident_id", r"^INC-2026-\d{6}$"),
    "incident_status_event": ("incident_status_event_id", r"^ISTAT-INC-2026-\d{6}-\d{2}$"),
    "corrective_action": ("corrective_action_id", r"^ACT-INC-2026-\d{6}-\d{2}$"),
    "corrective_action_status_event": ("action_status_event_id", r"^ASTAT-ACT-INC-2026-\d{6}-\d{2}-\d{2}$"),
}


@pytest.fixture(scope="module")
def ds():
    return generate(STARTER)


def idx(rows, key):
    return {r[key]: r for r in rows}


# ---------------------------------------------------------------- structure / IDs / joins
def test_starter_volumes(ds):
    t = ds.tables
    assert len(t["plant"]) == STARTER.n_plants
    assert len(t["machine"]) == STARTER.n_machines
    assert len(t["alarm_definition"]) == STARTER.n_alarm_defs
    assert len(t["alarm_event"]) == STARTER.n_alarm_events
    assert set(t) == set(TABLE_ORDER)


def test_all_ids_match_conventions_and_are_unique(ds):
    for table, (col, pattern) in ID_PATTERNS.items():
        ids = [r[col] for r in ds.tables[table]]
        assert ids, table
        assert len(ids) == len(set(ids)), f"duplicate ids in {table}"
        bad = [i for i in ids if not re.match(pattern, i)]
        assert not bad, (table, bad[:3])


def test_zero_padded_counters_are_gapless_per_parent(ds):
    per = defaultdict(list)
    for a in ds.tables["alarm_event"]:
        per[a["machine_id"]].append(int(a["alarm_event_id"].rsplit("-", 1)[1]))
    for mid, nums in per.items():
        assert sorted(nums) == list(range(1, len(nums) + 1)), mid


def test_every_foreign_key_resolves(ds):
    pk = {t: {tuple(r[c.name] for c in Base.metadata.tables[t].primary_key.columns) for r in rows}
          for t, rows in ds.tables.items()}
    checked = 0
    for table in TABLE_ORDER:
        for fk in Base.metadata.tables[table].foreign_key_constraints:
            parent = fk.referred_table.name
            cols = [e.parent.name for e in fk.elements]
            for r in ds.tables[table]:
                vals = tuple(r[c] for c in cols)
                if any(v is None for v in vals):
                    continue
                assert vals in pk[parent], f"{table}.{cols} -> {parent}: {vals}"
                checked += 1
    assert checked > 10_000


def test_same_plant_joins(ds):
    plant_of = {m["machine_id"]: m["plant_id"] for m in ds.tables["machine"]}
    zone_plant = {z["safety_zone_id"]: z["plant_id"] for z in ds.tables["safety_zone"]}
    shift_plant = {s["shift_id"]: s["plant_id"] for s in ds.tables["operator_shift"]}
    for r in ds.tables["machine_safety_zone"]:
        assert plant_of[r["machine_id"]] == zone_plant[r["safety_zone_id"]]
    for r in ds.tables["shift_machine_assignment"]:
        assert plant_of[r["machine_id"]] == shift_plant[r["shift_id"]]
    for r in ds.tables["alarm_event"]:
        if r["shift_id"]:
            assert shift_plant[r["shift_id"]] == plant_of[r["machine_id"]]
        if r["safety_zone_id"]:
            assert zone_plant[r["safety_zone_id"]] == plant_of[r["machine_id"]]


def test_every_machine_has_required_interlock_and_zone(ds):
    req = {i["machine_id"] for i in ds.tables["interlock"] if i["is_required"]}
    zones = {r["machine_id"] for r in ds.tables["machine_safety_zone"] if r["is_primary"]}
    for m in ds.tables["machine"]:
        assert m["machine_id"] in req and m["machine_id"] in zones


# ---------------------------------------------------------------- machine state timelines
def test_machine_state_timeline_is_ordered_and_valid(ds):
    by_m = defaultdict(list)
    for r in ds.tables["machine_state_event"]:
        by_m[r["machine_id"]].append(r)
    assert len(by_m) == STARTER.n_machines
    for mid, rows in by_m.items():
        rows.sort(key=lambda r: r["sequence_no"])
        assert [r["sequence_no"] for r in rows] == list(range(1, len(rows) + 1))
        assert rows[0]["previous_state"] is None
        for prev, cur in zip(rows, rows[1:]):
            assert cur["event_ts"] > prev["event_ts"], mid
            assert cur["previous_state"] == prev["machine_state"]
            assert is_valid_transition(prev["machine_state"], cur["machine_state"])
        assert all(STARTER.start <= r["event_ts"] < STARTER.end for r in rows)


def test_state_assessment_is_derived_from_interlock_history(ds):
    """Replay interlock events and re-derive summary/assessment with the rules engine."""
    il = idx(ds.tables["interlock"], "interlock_id")
    ise_by_m = defaultdict(list)
    for r in ds.tables["interlock_state_event"]:
        ise_by_m[il[r["interlock_id"]]["machine_id"]].append(r)
    il_by_m = defaultdict(list)
    for i in ds.tables["interlock"]:
        il_by_m[i["machine_id"]].append(i)
    mse_by_m = defaultdict(list)
    for r in ds.tables["machine_state_event"]:
        mse_by_m[r["machine_id"]].append(r)
    for mid, mses in mse_by_m.items():
        events = sorted(ise_by_m[mid], key=lambda r: (r["event_ts"], r["interlock_id"], r["sequence_no"]))
        status = {i["interlock_id"]: None for i in il_by_m[mid]}
        obs = {}
        k = 0
        for m in sorted(mses, key=lambda r: r["sequence_no"]):
            while k < len(events) and events[k]["event_ts"] <= m["event_ts"]:
                status[events[k]["interlock_id"]] = events[k]["interlock_status"]
                obs[events[k]["interlock_id"]] = events[k]["door_state_observed"]
                k += 1
            readings = [InterlockReading(i["interlock_type"], status[i["interlock_id"]], i["bypass_allowed"],
                                         obs.get(i["interlock_id"]), i["is_required"]) for i in il_by_m[mid]]
            assert m["interlock_status_summary"] == summarize_interlocks(readings)
            assessment, code = assess_state(m["machine_state"], m["door_state"], readings)
            assert (m["safety_assessment"], m["unsafe_rule_code"]) == (assessment, code), m["machine_state_event_id"]


def test_baseline_has_no_invalid_states(ds):
    assert Counter(r["safety_assessment"] for r in ds.tables["machine_state_event"])["INVALID"] == 0
    assert Counter(r["safety_assessment"] for r in ds.tables["machine_state_event"])["UNSAFE"] > 0
    for r in ds.tables["interlock_state_event"]:
        assert not (r["interlock_status"] == "ENGAGED" and r["door_state_observed"] == "OPEN")


# ---------------------------------------------------------------- alarms
def test_alarm_derived_fields(ds):
    defs = idx(ds.tables["alarm_definition"], "alarm_definition_id")
    mse = idx(ds.tables["machine_state_event"], "machine_state_event_id")
    il = idx(ds.tables["interlock"], "interlock_id")
    mse_ts = defaultdict(list)
    for r in ds.tables["machine_state_event"]:
        mse_ts[r["machine_id"]].append(r["event_ts"])
    for a in ds.tables["alarm_event"]:
        assert defs[a["alarm_definition_id"]]["lifecycle_status"] == "ACTIVE"
        assert a["ack_deadline_at"] == ack_deadline_at(a["raised_at"], a["severity"])
        assert STARTER.start <= a["raised_at"] < STARTER.end
        assert (a["cleared_at"] is None) == (a["lifecycle_status"] == "ACTIVE")
        if a["cleared_at"]:
            assert a["cleared_at"] > a["raised_at"]
        s = mse[a["machine_state_event_id"]]
        assert s["machine_id"] == a["machine_id"] and s["event_ts"] <= a["raised_at"]
        assert max(t for t in mse_ts[a["machine_id"]] if t <= a["raised_at"]) == s["event_ts"]
        if a["interlock_id"]:
            assert il[a["interlock_id"]]["machine_id"] == a["machine_id"]
        d = defs[a["alarm_definition_id"]]
        assert d["machine_type"] in (None, next(m["machine_type"] for m in ds.tables["machine"]
                                                if m["machine_id"] == a["machine_id"]))


def test_ids_follow_event_order_per_machine(ds):
    by_m = defaultdict(list)
    for a in ds.tables["alarm_event"]:
        by_m[a["machine_id"]].append(a)
    for rows in by_m.values():
        rows.sort(key=lambda a: a["alarm_event_id"])
        times = [a["raised_at"] for a in rows]
        assert times == sorted(times)


def test_escalation_while_unsafe(ds):
    unsafe = {r["machine_state_event_id"] for r in ds.tables["machine_state_event"] if r["safety_assessment"] == "UNSAFE"}
    defs = idx(ds.tables["alarm_definition"], "alarm_definition_id")
    sev_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    seen = 0
    for a in ds.tables["alarm_event"]:
        if a["machine_state_event_id"] in unsafe:
            seen += 1
            assert sev_rank[a["severity"]] >= min(3, sev_rank[defs[a["alarm_definition_id"]]["default_severity"]] + 1)
    assert seen > 0


# ---------------------------------------------------------------- acknowledgements
def test_acknowledgement_rules(ds):
    alarms = idx(ds.tables["alarm_event"], "alarm_event_id")
    shifts = idx(ds.tables["operator_shift"], "shift_id")
    plant_of = {m["machine_id"]: m["plant_id"] for m in ds.tables["machine"]}
    acks = ds.tables["acknowledgement"]
    assert len({a["alarm_event_id"] for a in acks}) == len(acks)  # 0..1 per alarm
    for k in acks:
        a = alarms[k["alarm_event_id"]]
        assert k["acknowledgement_id"] == f"ACK-{a['alarm_event_id']}-01"
        assert k["acknowledged_at"] > a["raised_at"], "baseline has no ack-before-alarm"
        assert k["acknowledged_at"] < STARTER.end
        assert k["ack_delay_seconds"] == ack_delay_seconds(a["raised_at"], k["acknowledged_at"])
        assert k["ack_status"] == ack_status(k["acknowledged_at"], a["ack_deadline_at"])
        assert shifts[k["acknowledged_by_shift_id"]]["plant_id"] == plant_of[a["machine_id"]]


def test_ack_delay_distribution_is_meaningful(ds):
    alarms = idx(ds.tables["alarm_event"], "alarm_event_id")
    delays = defaultdict(list)
    for k in ds.tables["acknowledgement"]:
        delays[alarms[k["alarm_event_id"]]["severity"]].append(k["ack_delay_seconds"])
    assert statistics.median(delays["HIGH"]) < statistics.median(delays["MEDIUM"]) < statistics.median(delays["LOW"])
    if delays["CRITICAL"]:
        assert statistics.median(delays["CRITICAL"]) < statistics.median(delays["HIGH"])
    statuses = Counter(k["ack_status"] for k in ds.tables["acknowledgement"])
    assert statuses["LATE"] > 0 and statuses["ON_TIME"] > statuses["LATE"]
    assert len(ds.tables["acknowledgement"]) < len(alarms)  # some alarms never acknowledged


# ---------------------------------------------------------------- incidents & actions
def test_incident_creation_and_context(ds):
    alarms = idx(ds.tables["alarm_event"], "alarm_event_id")
    mse = idx(ds.tables["machine_state_event"], "machine_state_event_id")
    links = defaultdict(list)
    for l in ds.tables["incident_alarm_link"]:
        links[l["incident_id"]].append(l)
    assert ds.tables["incident"]
    for inc in ds.tables["incident"]:
        ls = links[inc["incident_id"]]
        triggers = [l for l in ls if l["link_role"] == "TRIGGER"]
        assert len(triggers) == 1
        trig = alarms[triggers[0]["alarm_event_id"]]
        assert trig["severity"] in ("HIGH", "CRITICAL") and trig["raised_at"] == inc["opened_at"]
        assert all(alarms[l["alarm_event_id"]]["machine_id"] == inc["machine_id"] for l in ls)
        st = mse[inc["trigger_machine_state_event_id"]]
        assert st["safety_assessment"] == "UNSAFE" and st["unsafe_rule_code"] == inc["trigger_rule_code"]
        assert st["machine_id"] == inc["machine_id"]
        sev = {alarms[l["alarm_event_id"]]["severity"] for l in ls}
        assert inc["severity"] == ("CRITICAL" if "CRITICAL" in sev else "HIGH")


def test_incident_lifecycle_history(ds):
    events = defaultdict(list)
    for e in ds.tables["incident_status_event"]:
        events[e["incident_id"]].append(e)
    actions = defaultdict(list)
    for a in ds.tables["corrective_action"]:
        actions[a["incident_id"]].append(a)
    statuses = Counter()
    for inc in ds.tables["incident"]:
        evs = sorted(events[inc["incident_id"]], key=lambda e: e["sequence_no"])
        assert [e["sequence_no"] for e in evs] == list(range(1, len(evs) + 1))
        assert evs[0]["changed_at"] == inc["opened_at"]
        prev = None
        for e in evs:
            assert e["from_status"] == prev and valid_incident_transition(prev, e["to_status"])
            assert e["changed_at"] < STARTER.end
            prev = e["to_status"]
        assert inc["status"] == evs[-1]["to_status"]
        statuses[inc["status"]] += 1
        stamps = {e["to_status"]: e["changed_at"] for e in evs}
        assert inc["investigation_started_at"] == stamps.get("INVESTIGATING")
        assert inc["resolved_at"] == stamps.get("RESOLVED")
        assert inc["closed_at"] == stamps.get("CLOSED")
        if inc["status"] == "CLOSED":
            assert can_close_incident(actions[inc["incident_id"]])  # IN-4
    assert len(statuses) >= 2  # open and finished incidents both exist


def test_corrective_actions_follow_rules(ds):
    incidents = idx(ds.tables["incident"], "incident_id")
    events = defaultdict(list)
    for e in ds.tables["corrective_action_status_event"]:
        events[e["corrective_action_id"]].append(e)
    per_inc = defaultdict(list)
    for a in ds.tables["corrective_action"]:
        assert a["incident_id"] in incidents
        assert a["planned_at"] >= incidents[a["incident_id"]]["opened_at"]
        assert action_closed_properly(a), a["corrective_action_id"]  # CA-3: baseline never closes unverified
        assert (a["verified_at"] is None) == (a["verification_result"] is None)
        if a["verified_by_id"]:
            assert a["verified_by_id"] != a["assigned_engineer_id"]
        evs = sorted(events[a["corrective_action_id"]], key=lambda e: e["sequence_no"])
        prev = None
        for e in evs:
            assert e["from_status"] == prev and valid_action_transition(prev, e["to_status"])
            prev = e["to_status"]
        assert a["status"] == evs[-1]["to_status"]
        per_inc[a["incident_id"]].append(int(a["corrective_action_id"].rsplit("-", 1)[1]))
    for nums in per_inc.values():
        assert sorted(nums) == list(range(1, len(nums) + 1))


def test_failed_verifications_get_follow_up(ds):
    failed = [a for a in ds.tables["corrective_action"] if a["verification_result"] == "FAILED"]
    for a in failed:
        assert a["status"] in ("VERIFIED", "CANCELLED") and a["closed_at"] is None


# ---------------------------------------------------------------- scenarios & ground truth
def test_required_scenarios_exist(ds):
    gt = Counter(r["issue_type"] for r in ds.ground_truth)
    assert gt["ALARM_FLOOD"] >= 10 and gt["NUISANCE_CLUSTER"] >= 5 and gt["REPEATED_UNSAFE_CONDITION"] >= 3
    assert ds.meta["alarm_sources"]["state_driven"] > 0


def test_flood_labels_match_rule_definition(ds):
    alarms = idx(ds.tables["alarm_event"], "alarm_event_id")
    groups = defaultdict(list)
    for r in ds.ground_truth:
        if r["issue_type"] == "ALARM_FLOOD":
            groups[r["group_id"]].append(alarms[r["record_id"]]["raised_at"])
    assert groups
    for g, times in groups.items():
        times.sort()
        best = max(sum(1 for t in times if t0 <= t < t0 + timedelta(minutes=10)) for t0 in times)
        assert best >= 10, g


def test_ground_truth_is_separate_from_features(ds):
    for table, rows in ds.tables.items():
        cols = set(rows[0])
        assert not cols & {"issue_type", "ground_truth", "label_id", "is_anomaly"}, table
    ids = {t: {tuple(r[c.name] for c in Base.metadata.tables[t].primary_key.columns)[0] for r in ds.tables[t]}
           for t in ("alarm_event", "machine_state_event")}
    for r in ds.ground_truth:
        assert r["label_class"] == "BASELINE_SCENARIO" and r["record_id"] in ids[r["record_table"]]
    assert len({r["label_id"] for r in ds.ground_truth}) == len(ds.ground_truth)


# ---------------------------------------------------------------- exports & reproducibility
def test_export_files_and_formats(ds, tmp_path):
    sums = write_exports(ds.tables, tmp_path)
    for t in TABLE_ORDER:
        header = (tmp_path / "csv" / f"{t}.csv").read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",") == table_columns(t)
        assert len(json.loads((tmp_path / "json" / f"{t}.json").read_text(encoding="utf-8"))) == len(ds.tables[t])
    sql = (tmp_path / "sql" / "seed.sql").read_text(encoding="utf-8")
    assert sql.startswith("-- Synthetic seed data") and "BEGIN;" in sql and sql.rstrip().endswith("COMMIT;")
    assert sql.count("INSERT INTO alarm_event ") == -(-STARTER.n_alarm_events // 500)
    assert "checksums.json" not in sums and "row_counts.json" in sums


def test_reproducible_with_fixed_seed(tmp_path):
    runs = []
    for k in range(2):
        d = generate(STARTER)
        sums = write_exports(d.tables, tmp_path / f"run{k}")
        sums.update({f"gt/{n}": h for n, h in write_ground_truth(d.ground_truth, tmp_path / f"gt{k}").items()})
        runs.append((sums, {t: len(r) for t, r in d.tables.items()}, [r["alarm_event_id"] for r in d.tables["alarm_event"]]))
    assert runs[0][0] == runs[1][0]       # file checksums identical
    assert runs[0][1] == runs[1][1]       # row counts identical
    assert runs[0][2] == runs[1][2]       # deterministic IDs identical


def test_different_seed_changes_data(tmp_path):
    from dataclasses import replace
    a = write_exports(generate(STARTER).tables, tmp_path / "a")
    b = write_exports(generate(replace(STARTER, seed=1)).tables, tmp_path / "b")
    assert a["csv/alarm_event.csv"] != b["csv/alarm_event.csv"]


# ---------------------------------------------------------------- loading into PostgreSQL
def test_load_into_postgres_and_trace(ds, engine, tmp_path):
    write_exports(ds.tables, tmp_path)
    counts = load_dataset(engine, tmp_path / "csv", truncate=True)
    assert counts == {t: len(ds.tables[t]) for t in TABLE_ORDER}
    with engine.connect() as c:
        # traceability: alarm -> state -> incident -> actions all join
        n = c.execute(text("""
            SELECT count(*) FROM incident i
            JOIN incident_alarm_link l ON l.incident_id = i.incident_id AND l.link_role = 'TRIGGER'
            JOIN alarm_event a ON a.alarm_event_id = l.alarm_event_id
            JOIN machine_state_event s ON s.machine_state_event_id = i.trigger_machine_state_event_id
            JOIN corrective_action ca ON ca.incident_id = i.incident_id""")).scalar()
        assert n >= len(ds.tables["incident"])
        late = c.execute(text("SELECT count(*) FROM acknowledgement WHERE ack_status='LATE'")).scalar()
        assert late == sum(1 for k in ds.tables["acknowledgement"] if k["ack_status"] == "LATE")
