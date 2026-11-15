"""Milestone 8 tests: read endpoints, pagination, filters, traceability, analytics, validation, anomalies."""
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.analytics.kpis import compute_kpis
from app.services.evaluation_service import evaluate_tables
from data_generator.config import STARTER
from data_generator.export import TABLE_ORDER, write_exports, write_ground_truth
from data_generator.generate import generate_with_anomalies
from data_generator.loader import load_dataset, load_ground_truth

API = "/api/v1"


@pytest.fixture(scope="module")
def pair():
    return generate_with_anomalies(STARTER)


@pytest.fixture(scope="module")
def loaded(engine, pair, tmp_path_factory):
    d = tmp_path_factory.mktemp("api_read")
    write_exports(pair[1].tables, d / "out")
    write_ground_truth(pair[1].ground_truth, d / "gt")
    load_dataset(engine, d / "out" / "csv", truncate=True)
    load_ground_truth(engine, d / "gt" / "ground_truth.csv", truncate=True)
    return pair[1]


@pytest.fixture()
def api(client, loaded):
    return client


def items(r):
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ------------------------------------------------------------------------ system
def test_health_and_openapi(api):
    assert api.get("/health").json() == {"status": "ok", "database": "up"}
    spec = api.get("/openapi.json").json()
    ops = [(m, p) for p, v in spec["paths"].items() for m in v if m in ("get", "post")]
    assert len(ops) >= 50
    tags = {t["name"] for t in spec["tags"]}
    assert {"plants", "machines", "alarm events", "incidents", "analytics", "validation", "anomaly detection"} <= tags
    assert api.get("/docs").status_code == 200


# ------------------------------------------------------------------------ lists, counts, pagination
@pytest.mark.parametrize("path,table", [
    ("plants", "plant"), ("machines", "machine"), ("safety-zones", "safety_zone"), ("interlocks", "interlock"),
    ("alarm-definitions", "alarm_definition"), ("operator-shifts", "operator_shift"),
    ("machine-state-events", "machine_state_event"), ("alarm-events", "alarm_event"), ("acknowledgements", "acknowledgement"),
    ("incidents", "incident"), ("corrective-actions", "corrective_action")])
def test_list_endpoints_return_all_rows_in_pages(api, loaded, path, table):
    total = len(loaded.tables[table])
    r = api.get(f"{API}/{path}", params={"page_size": 7})
    body = r.json()
    assert r.status_code == 200 and body["total"] == total and body["page"] == 1 and body["page_size"] == 7
    assert body["pages"] == max(1, math.ceil(total / 7)) and len(body["items"]) == min(7, total)
    last = api.get(f"{API}/{path}", params={"page_size": 7, "page": body["pages"]}).json()
    assert len(last["items"]) == total - 7 * (body["pages"] - 1)
    assert api.get(f"{API}/{path}", params={"page_size": 7, "page": body["pages"] + 1}).json()["items"] == []


def test_pages_do_not_overlap(api):
    ids = []
    for page in (1, 2, 3):
        ids += [a["alarm_event_id"] for a in items(api.get(f"{API}/alarm-events", params={"page": page, "page_size": 40}))]
    assert len(ids) == len(set(ids)) == 120


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 501}, {"page": "x"}])
def test_bad_pagination_is_a_422_with_error_envelope(api, params):
    r = api.get(f"{API}/plants", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert r.json()["error"]["details"][0]["field"] in ("query.page", "query.page_size")


def test_unknown_records_are_404_envelopes(api):
    for path in ("plants/P99", "machines/NOPE", "alarm-events/ALM-X", "incidents/INC-2026-999999", "safety-zones/SAFE-P01-999",
                 "interlocks/ILK-X", "alarm-definitions/ADEF-9999", "operator-shifts/SHF-X", "acknowledgements/ACK-X",
                 "corrective-actions/ACT-X", "machine-state-events/MSE-X", "alarm-events/ALM-X/trace"):
        r = api.get(f"{API}/{path}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", path
    assert api.get("/no/such/route").status_code == 404


# ------------------------------------------------------------------------ filters
def test_filters_agree_with_the_data(api, loaded):
    t = loaded.tables
    p1 = items(api.get(f"{API}/machines", params={"plant_id": "P01", "page_size": 100}))
    assert {m["machine_id"] for m in p1} == {m["machine_id"] for m in t["machine"] if m["plant_id"] == "P01"}
    mtype = t["machine"][0]["machine_type"]
    assert all(m["machine_type"] == mtype for m in items(api.get(f"{API}/machines", params={"machine_type": mtype})))
    assert {m["machine_id"] for m in items(api.get(f"{API}/machines", params={"q": t["machine"][0]["machine_name"][:6]}))}
    crit = api.get(f"{API}/alarm-events", params={"severity": "CRITICAL", "page_size": 500}).json()
    assert crit["total"] == sum(a["severity"] == "CRITICAL" for a in t["alarm_event"])
    unsafe = api.get(f"{API}/machine-state-events", params={"safety_assessment": "UNSAFE"}).json()
    assert unsafe["total"] == sum(s["safety_assessment"] == "UNSAFE" for s in t["machine_state_event"])
    assert api.get(f"{API}/machine-state-events", params={"safety_assessment": "INVALID"}).json()["total"] == STARTER.n_impossible_interlock
    mid = t["machine"][0]["machine_id"]
    per = api.get(f"{API}/alarm-events", params={"machine_id": mid}).json()["total"]
    assert per == sum(a["machine_id"] == mid for a in t["alarm_event"])
    plant = api.get(f"{API}/alarm-events", params={"plant_id": "P02"}).json()["total"]
    p2 = {m["machine_id"] for m in t["machine"] if m["plant_id"] == "P02"}
    assert plant == sum(a["machine_id"] in p2 for a in t["alarm_event"])


def test_alarm_acknowledged_and_overdue_filters(api, loaded):
    t = loaded.tables
    acked = {k["alarm_event_id"] for k in t["acknowledgement"]}
    yes = api.get(f"{API}/alarm-events", params={"acknowledged": True}).json()["total"]
    no = api.get(f"{API}/alarm-events", params={"acknowledged": False}).json()["total"]
    assert yes == sum(a["alarm_event_id"] in acked for a in t["alarm_event"]) and yes + no == len(t["alarm_event"])
    cutoff = STARTER.end.isoformat()
    overdue = api.get(f"{API}/alarm-events", params={"overdue_before": cutoff, "page_size": 500}).json()
    assert overdue["total"] == sum(a["alarm_event_id"] not in acked and a["ack_deadline_at"] < STARTER.end for a in t["alarm_event"])


def test_alarm_priority_sort_puts_critical_first(api):
    rank = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
    rows = items(api.get(f"{API}/alarm-events", params={"sort": "priority", "page_size": 500}))
    ranks = [rank[a["severity"]] for a in rows]
    assert ranks == sorted(ranks) and ranks[0] == 1
    crit = [a for a in rows if a["severity"] == "CRITICAL"]
    assert [a["raised_at"] for a in crit] == sorted((a["raised_at"] for a in crit), reverse=True)   # newest first inside a rank
    by_time = items(api.get(f"{API}/alarm-events", params={"page_size": 50}))
    assert [a["raised_at"] for a in by_time] == sorted((a["raised_at"] for a in by_time), reverse=True)
    asc = items(api.get(f"{API}/alarm-events", params={"page_size": 50, "order": "asc"}))
    assert [a["raised_at"] for a in asc] == sorted(a["raised_at"] for a in asc)


def test_date_range_filters_and_utc_output(api, loaded):
    lo, hi = "2026-10-03T00:00:00", "2026-10-03T23:59:59"
    d = api.get(f"{API}/alarm-events", params={"from_ts": lo, "to_ts": hi, "page_size": 500}).json()
    assert d["total"] > 0 and all(a["raised_at"].startswith("2026-10-03") for a in d["items"])
    assert all(a["raised_at"].endswith("+00:00") for a in d["items"])                       # UTC regardless of session zone
    exp = sum(datetime(2026, 10, 3, tzinfo=timezone.utc) <= a["raised_at"] < datetime(2026, 10, 4, tzinfo=timezone.utc)
              for a in loaded.tables["alarm_event"])
    assert d["total"] == exp


def test_ack_incident_and_action_filters(api, loaded):
    t = loaded.tables
    late = api.get(f"{API}/acknowledgements", params={"ack_status": "LATE"}).json()["total"]
    assert late == sum(k["ack_status"] == "LATE" for k in t["acknowledgement"])
    slow = api.get(f"{API}/acknowledgements", params={"min_delay_seconds": 3600}).json()
    assert slow["total"] == sum(k["ack_delay_seconds"] >= 3600 for k in t["acknowledgement"])
    assert all(k["ack_delay_seconds"] >= 3600 for k in slow["items"])
    assert api.get(f"{API}/incidents", params={"open": True}).json()["total"] == sum(i["status"] != "CLOSED" for i in t["incident"])
    assert api.get(f"{API}/incidents", params={"status": "RESOLVED"}).json()["total"] == sum(i["status"] == "RESOLVED" for i in t["incident"])
    assert api.get(f"{API}/incidents", params={"severity": "CRITICAL"}).json()["total"] == sum(i["severity"] == "CRITICAL" for i in t["incident"])
    st = Counter(a["status"] for a in t["corrective_action"])
    for status, n in st.items():
        assert api.get(f"{API}/corrective-actions", params={"status": status}).json()["total"] == n
    assert api.get(f"{API}/corrective-actions", params={"verification_result": "PASSED"}).json()["total"] == sum(
        a["verification_result"] == "PASSED" for a in t["corrective_action"])
    assert api.get(f"{API}/corrective-actions", params={"overdue": True}).json()["total"] >= 1   # starter data is in the past


def test_invalid_enum_filter_is_422(api):
    r = api.get(f"{API}/alarm-events", params={"severity": "SEVERE"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------------ machines, zones, interlocks, shifts
def test_machine_subresources_and_status(api, loaded):
    t = loaded.tables
    m = t["machine"][0]["machine_id"]
    il = api.get(f"{API}/machines/{m}/interlocks").json()
    assert {i["interlock_id"] for i in il} == {i["interlock_id"] for i in t["interlock"] if i["machine_id"] == m}
    zones = api.get(f"{API}/machines/{m}/safety-zones").json()
    assert {z["safety_zone_id"] for z in zones} == {r["safety_zone_id"] for r in t["machine_safety_zone"] if r["machine_id"] == m}
    hist = api.get(f"{API}/machines/{m}/state-events", params={"order": "asc", "page_size": 500}).json()
    assert [e["sequence_no"] for e in hist["items"]] == sorted(e["sequence_no"] for e in hist["items"])
    assert hist["total"] == sum(s["machine_id"] == m for s in t["machine_state_event"])
    status = items(api.get(f"{API}/machines/status", params={"page_size": 100}))
    assert len(status) == len(t["machine"])
    last = {}
    for s in t["machine_state_event"]:
        if s["machine_id"] not in last or s["sequence_no"] > last[s["machine_id"]]["sequence_no"]:
            last[s["machine_id"]] = s
    for s in status:
        assert (s["machine_state"], s["safety_assessment"]) == (last[s["machine_id"]]["machine_state"], last[s["machine_id"]]["safety_assessment"])
    assert items(api.get(f"{API}/machines/status", params={"plant_id": "P01", "page_size": 100})) == [s for s in status if s["plant_id"] == "P01"]
    assert api.get(f"{API}/machines/NOPE/interlocks").status_code == 404


def test_zone_interlock_definition_and_shift_endpoints(api, loaded):
    t = loaded.tables
    z = t["safety_zone"][0]["safety_zone_id"]
    links = api.get(f"{API}/safety-zones/{z}/machines").json()
    assert {l["machine_id"] for l in links} == {r["machine_id"] for r in t["machine_safety_zone"] if r["safety_zone_id"] == z}
    assert api.get(f"{API}/safety-zones", params={"machine_id": t["machine"][0]["machine_id"]}).json()["total"] >= 1
    assert all(i["is_required"] for i in items(api.get(f"{API}/interlocks", params={"is_required": True, "page_size": 100})))
    il = t["interlock"][0]["interlock_id"]
    hist = api.get(f"{API}/interlocks/{il}/state-events").json()
    assert hist["total"] == sum(e["interlock_id"] == il for e in t["interlock_state_event"])
    policy = api.get(f"{API}/alarm-definitions/severity-policy").json()
    assert [(p["severity"], p["ack_deadline_seconds"]) for p in policy] == [("CRITICAL", 120), ("HIGH", 600), ("MEDIUM", 1800), ("LOW", 7200)]
    safety = api.get(f"{API}/alarm-definitions", params={"trigger_type": "SAFETY_STATE"}).json()
    assert safety["total"] == 3 and all(d["is_safety_related"] for d in safety["items"])
    s = t["operator_shift"][0]
    detail = api.get(f"{API}/operator-shifts/{s['shift_id']}").json()
    assert detail["machine_ids"] == sorted(r["machine_id"] for r in t["shift_machine_assignment"] if r["shift_id"] == s["shift_id"])
    covering = items(api.get(f"{API}/operator-shifts", params={"covering": "2026-10-03T10:00:00", "plant_id": "P01"}))
    assert covering and all(c["shift_start"] <= "2026-10-03T10:00:00+00:00" < c["shift_end"] for c in covering)


# ------------------------------------------------------------------------ traceability
def test_incident_trace_is_complete_for_a_normal_incident(api, loaded):
    t = loaded.tables
    ok = next(i for i in t["incident"] if i["trigger_machine_state_event_id"] and
              any(a["incident_id"] == i["incident_id"] for a in t["corrective_action"]))
    d = api.get(f"{API}/incidents/{ok['incident_id']}").json()
    assert d["context_complete"] is True and d["incident"]["incident_id"] == ok["incident_id"]
    assert d["trigger_state"]["safety_assessment"] == "UNSAFE" and d["trigger_state"]["interlock_events"]
    assert d["alarms"][0]["link_role"] == "TRIGGER" and d["alarms"][0]["alarm"]["raised_at"] == d["incident"]["opened_at"]
    assert d["shift"]["shift_id"] == ok["shift_id"]
    assert [h["sequence_no"] for h in d["status_history"]] == list(range(1, len(d["status_history"]) + 1))
    assert d["corrective_actions"] and all(a["history"] for a in d["corrective_actions"])
    hist = api.get(f"{API}/incidents/{ok['incident_id']}/status-history").json()
    assert [h["to_status"] for h in hist] == [h["to_status"] for h in d["status_history"]]


def test_incident_without_context_is_marked_incomplete(api, loaded):
    bad = next(i for i in loaded.tables["incident"] if i["trigger_machine_state_event_id"] is None)
    d = api.get(f"{API}/incidents/{bad['incident_id']}").json()
    assert d["context_complete"] is False and d["trigger_state"] is None and d["alarms"] == []


def test_alarm_trace_links_state_ack_incident_and_actions(api, loaded):
    t = loaded.tables
    inc = next(i for i in t["incident"] if i["trigger_machine_state_event_id"])
    trig = next(l["alarm_event_id"] for l in t["incident_alarm_link"] if l["incident_id"] == inc["incident_id"] and l["link_role"] == "TRIGGER")
    r = api.get(f"{API}/alarm-events/{trig}/trace", params={"cutoff": STARTER.end.isoformat()}).json()
    assert r["alarm"]["alarm_event_id"] == trig and r["machine"]["machine_id"] == inc["machine_id"]
    assert r["machine_state"]["machine_state_event_id"] == r["alarm"]["machine_state_event_id"]
    assert r["definition"]["alarm_definition_id"] == r["alarm"]["alarm_definition_id"]
    assert any(x["incident_id"] == inc["incident_id"] and x["link_role"] == "TRIGGER" for x in r["incidents"])
    assert r["ack_outcome"] in ("ON_TIME", "LATE", "MISSED", "PENDING")
    plain = next(a for a in t["alarm_event"] if a["alarm_event_id"] not in {k["alarm_event_id"] for k in t["acknowledgement"]}
                 and a["ack_deadline_at"] < STARTER.end)
    p = api.get(f"{API}/alarm-events/{plain['alarm_event_id']}/trace", params={"cutoff": STARTER.end.isoformat()}).json()
    assert p["ack_outcome"] == "MISSED" and p["acknowledgement"] is None
    ack = t["acknowledgement"][0]
    a = api.get(f"{API}/alarm-events/{ack['alarm_event_id']}").json()
    assert a["acknowledgement"]["acknowledgement_id"] == ack["acknowledgement_id"]


# ------------------------------------------------------------------------ analytics, validation, anomalies
def test_analytics_endpoints_match_the_kpi_module(api, loaded):
    expect = compute_kpis(loaded.tables)
    got = api.get(f"{API}/analytics/kpis").json()
    for section in ("overview", "alarms", "acknowledgements", "incidents", "corrective_actions"):
        assert got[section] == expect[section] or section == "incidents"
    assert got["overview"] == expect["overview"] and got["incidents"]["total"] == expect["incidents"]["total"]
    assert got["meta"]["kpi_type"] == "business"
    ov = api.get(f"{API}/analytics/kpis/overview").json()
    assert ov == got["overview"]
    p1 = api.get(f"{API}/analytics/kpis/overview", params={"plant_id": "P01"}).json()
    assert p1["plants"] == 1 and p1["alarm_events_stored"] < ov["alarm_events_stored"]
    assert api.get(f"{API}/analytics/kpis", params={"date_from": "2026-10-03", "date_to": "2026-10-03"}).json()["meta"]["filters"] == {
        "date_from": "2026-10-03", "date_to": "2026-10-03"}
    assert api.get(f"{API}/analytics/kpis/nonsense").status_code == 404
    assert api.get(f"{API}/analytics/kpis", params={"severity": "BAD"}).status_code == 422
    assert "precision" not in str(got).lower()


def test_validation_endpoints(api):
    checks = api.get(f"{API}/validation/checks").json()
    assert [c["check_id"] for c in checks] == [f"V{n:02d}" for n in range(1, 18)]
    r = api.get(f"{API}/validation/report", params={"max_errors": 3}).json()
    assert r["summary"]["validations_run"] == 17 and set(r["summary"]["failed_validation_ids"]) == {"V07", "V08", "V11", "V13", "V14"}
    assert r["summary"]["anomalies_detected"] == 43
    v14 = next(c for c in r["checks"] if c["check_id"] == "V14")
    assert len(v14["errors"]) == 3 and v14["errors_truncated"] > 0 and v14["status"] == "FAILED"
    full = api.get(f"{API}/validation/report", params={"full": True}).json()
    assert len(next(c for c in full["checks"] if c["check_id"] == "V14")["errors"]) == v14["failure_count"]
    assert next(c for c in full["checks"] if c["check_id"] == "V01")["status"] == "PASSED"


def test_anomaly_endpoints(api, loaded):
    s = api.get(f"{API}/anomalies/summary").json()
    assert s["anomaly"]["total"] == 43 and set(s["anomaly"]["by_issue_type"]) == {
        "ACK_BEFORE_ALARM", "IMPOSSIBLE_INTERLOCK_STATE", "INCIDENT_NO_CONTEXT", "REPLAY_DUPLICATE_FLOOD", "ACTION_CLOSED_UNVERIFIED"}
    assert s["pattern"]["by_issue_type"]["ALARM_FLOOD"] > 0
    lst = api.get(f"{API}/anomalies", params={"issue_type": "ACK_BEFORE_ALARM"}).json()
    assert lst["total"] == STARTER.n_ack_before_alarm and all(f["record_table"] == "acknowledgement" for f in lst["items"])
    page = api.get(f"{API}/anomalies", params={"page_size": 10, "page": 2}).json()
    assert len(page["items"]) == 10 and page["total"] == 43 and page["pages"] == 5
    pat = api.get(f"{API}/anomalies", params={"kind": "pattern", "issue_type": "NUISANCE_CLUSTER", "page_size": 500}).json()
    assert pat["total"] > 0 and api.get(f"{API}/anomalies", params={"kind": "other"}).status_code == 422
    assert api.get(f"{API}/anomalies", params={"record_table": "incident"}).json()["total"] == STARTER.n_incident_no_context


def test_evaluation_endpoint_matches_the_evaluation_module(api, loaded):
    r = api.get(f"{API}/anomalies/evaluation").json()
    expect = evaluate_tables(loaded.tables, loaded.ground_truth)
    assert r["anomalies"]["micro"] == expect["anomalies"].micro and r["patterns"]["micro"] == expect["patterns"].micro
    assert r["anomalies"]["metric_type"].startswith("detection evaluation")
    assert r["anomalies"]["confusion_matrix"]["labels"][-1] == "NONE"


def test_reads_do_not_modify_the_database(api, engine, loaded):
    def counts():
        with engine.connect() as c:
            return [c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLE_ORDER]
    before = counts()
    for path in ("analytics/kpis", "validation/report", "anomalies/evaluation", "machines/status", "incidents"):
        assert api.get(f"{API}/{path}").status_code == 200
    assert counts() == before
