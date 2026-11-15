"""Milestone 9 tests: every dashboard page runs against a LIVE API (real HTTP) and shows the API's numbers."""
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import requests
import uvicorn
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from data_generator.config import STARTER
from data_generator.export import write_exports, write_ground_truth
from data_generator.generate import generate_with_anomalies
from data_generator.loader import load_dataset, load_ground_truth

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "frontend" / "pages"
PAGE_FILES = ["1_executive_dashboard.py", "2_plant_machine_monitoring.py", "3_alarm_monitoring.py", "4_incident_management.py",
              "5_corrective_actions.py", "6_data_quality_anomalies.py"]
API = "/api/v1"


@pytest.fixture(scope="module")
def pair():
    return generate_with_anomalies(STARTER)


@pytest.fixture(scope="module")
def live_api(engine, pair, tmp_path_factory):
    """Starter data in the test database + a real uvicorn server (own thread) wired to it."""
    from app.api.deps import get_db
    from app.main import app
    from app.repositories.bulk_repository import clear_cache

    d = tmp_path_factory.mktemp("frontend")
    write_exports(pair[1].tables, d / "out")
    write_ground_truth(pair[1].ground_truth, d / "gt")
    load_dataset(engine, d / "out" / "csv", truncate=True)
    load_ground_truth(engine, d / "gt" / "ground_truth.csv", truncate=True)

    def _db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = _db
    clear_cache()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        raise RuntimeError("API did not start")
    yield url
    server.should_exit = True
    thread.join(timeout=10)
    app.dependency_overrides.clear()
    clear_cache()


@pytest.fixture()
def env(live_api, monkeypatch):
    import streamlit as st
    monkeypatch.setenv("API_BASE_URL", live_api)
    st.cache_data.clear()
    return live_api


def api_get(base, path, **params):
    r = requests.get(f"{base}{path}", params=params, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()


def run(page, timeout=90):
    at = AppTest.from_file(str(PAGES / page), default_timeout=timeout)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def metrics(at):
    return {m.label: m.value for m in at.metric}


def num(v):
    return f"{v:,}"


def choose(selectbox, key_prefix):
    """Select the option whose displayed label starts with `key_prefix` (labels are 'P01 - Pune Works')."""
    label = next(o for o in selectbox.options if o == key_prefix or o.startswith(key_prefix + " "))
    return selectbox.select(label)


def ids_of(selectbox):
    return {o.split(" - ")[0] for o in selectbox.options}


# ---------------------------------------------------------------- every page renders against the live API
@pytest.mark.parametrize("page", PAGE_FILES)
def test_page_runs_without_errors(env, page):
    at = run(page)
    assert at.title and len(at.metric) + len(at.dataframe) > 0
    assert not at.error, [e.value for e in at.error]


def test_navigation_entry_point_runs(env):
    at = AppTest.from_file(str(ROOT / "frontend" / "streamlit_app.py"), default_timeout=90)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "Executive Dashboard"


# ---------------------------------------------------------------- page 1: executive dashboard
def test_executive_tiles_equal_the_api(env):
    o = api_get(env, f"{API}/analytics/kpis/overview")
    m = metrics(run("1_executive_dashboard.py"))
    assert m["Total plants"] == num(o["plants"]) == "2" and m["Total machines"] == num(o["machines"]) == "8"
    assert m["Total alarm events"] == num(o["alarm_events_stored"])
    assert m["Critical incidents"] == num(o["critical_incidents"]) and m["Open incidents"] == num(o["open_incidents"])
    assert m["Delayed acknowledgements"] == num(o["delayed_acknowledgements"])
    assert m["Repeated unsafe conditions"] == num(o["repeated_unsafe_conditions"])
    assert m["Alarm floods"] == num(o["alarm_floods"]) and m["Nuisance alarm clusters"] == num(o["nuisance_clusters"])


def test_filters_change_the_numbers_and_the_machine_choices(env):
    at = run("1_executive_dashboard.py")
    all_machines = int(metrics(at)["Total machines"])
    choose(at.sidebar.selectbox(key="flt_plant"), "P01").run()
    assert not at.exception
    expect = api_get(env, f"{API}/analytics/kpis/overview", plant_id="P01")
    m = metrics(at)
    assert m["Total plants"] == "1" and m["Total machines"] == num(expect["machines"]) and int(m["Total machines"]) < all_machines
    assert m["Total alarm events"] == num(expect["alarm_events_stored"])
    machines = api_get(env, f"{API}/machines", plant_id="P01", page_size=100)["items"]
    assert ids_of(at.sidebar.selectbox(key="flt_machine")) == {"All"} | {x["machine_id"] for x in machines}
    choose(at.sidebar.selectbox(key="flt_machine"), machines[0]["machine_id"]).run()
    one = api_get(env, f"{API}/analytics/kpis/overview", plant_id="P01", machine_id=machines[0]["machine_id"])
    assert metrics(at)["Total machines"] == "1" and metrics(at)["Total alarm events"] == num(one["alarm_events_stored"])
    choose(at.sidebar.selectbox(key="flt_machine"), "All").run()
    at.sidebar.selectbox(key="flt_severity").select("CRITICAL").run()
    crit = api_get(env, f"{API}/analytics/kpis/overview", plant_id="P01", severity="CRITICAL")
    assert metrics(at)["Total alarm events"] == num(crit["alarm_events_stored"])
    assert not at.exception


def test_changing_the_plant_resets_a_machine_of_another_plant(env):
    at = run("1_executive_dashboard.py")
    choose(at.sidebar.selectbox(key="flt_plant"), "P01").run()
    first = [o for o in at.sidebar.selectbox(key="flt_machine").options if o != "All"][0]
    at.sidebar.selectbox(key="flt_machine").select(first).run()
    assert at.sidebar.selectbox(key="flt_machine").value != "All"
    choose(at.sidebar.selectbox(key="flt_plant"), "P02").run()
    assert at.sidebar.selectbox(key="flt_machine").value == "All" and not at.exception


def test_date_filter_narrows_the_kpis(env):
    import datetime as dt
    at = run("1_executive_dashboard.py")
    at.sidebar.date_input(key="flt_dates").set_value((dt.date(2026, 10, 3), dt.date(2026, 10, 3))).run()
    assert not at.exception
    expect = api_get(env, f"{API}/analytics/kpis/overview", date_from="2026-10-03", date_to="2026-10-03")
    assert metrics(at)["Total alarm events"] == num(expect["alarm_events_stored"])
    assert expect["alarm_events_stored"] < api_get(env, f"{API}/analytics/kpis/overview")["alarm_events_stored"]


# ---------------------------------------------------------------- page 2: plants and machines
def test_plant_and_machine_page(env):
    at = run("2_plant_machine_monitoring.py")
    status = api_get(env, f"{API}/machines/status", page_size=100)["items"]
    m = metrics(at)
    assert m["Machines shown"] == str(len(status))
    assert m["Currently UNSAFE"] == str(sum(s["safety_assessment"] == "UNSAFE" for s in status))
    assert m["Flagged INVALID"] == str(sum(s["safety_assessment"] == "INVALID" for s in status))
    assert len(at.tabs) == 3 and {t.label for t in at.tabs} == {"Machine-state history", "Interlock status", "Safety zones"}
    ids = at.selectbox(key="detail_machine").options
    assert ids == [s["machine_id"] for s in status]
    at.selectbox(key="detail_machine").select(ids[-1]).run()
    assert not at.exception
    # interlock tab shows the real current statuses
    ilk = api_get(env, f"{API}/machines/{ids[-1]}/interlocks")
    assert any(ilk[0]["interlock_id"] in df.value.to_string() for df in at.dataframe)


def test_plant_page_respects_the_plant_filter(env):
    at = run("2_plant_machine_monitoring.py")
    choose(at.sidebar.selectbox(key="flt_plant"), "P02").run()
    assert not at.exception
    expect = api_get(env, f"{API}/machines/status", plant_id="P02", page_size=100)["total"]
    assert metrics(at)["Machines shown"] == str(expect)


# ---------------------------------------------------------------- page 3: alarms
def test_alarm_page_numbers_and_drill_down(env):
    at = run("3_alarm_monitoring.py")
    k = api_get(env, f"{API}/analytics/kpis")
    m = metrics(at)
    assert m["Alarms analysed"] == num(k["alarms"]["total"])
    assert m["Critical + high"] == num(k["alarms"]["critical_and_high"])
    assert m["Acknowledged"] == num(k["acknowledgements"]["acknowledged"]) and m["Late"] == num(k["acknowledgements"]["late"])
    assert m["Flood episodes"] == num(k["floods"]["episodes"]) and m["Nuisance clusters"] == num(k["nuisance"]["clusters"])
    assert {t.label for t in at.tabs} >= {"Timeline", "Severity", "Acknowledgement delays", "Alarm floods", "Nuisance alarms"}
    pick = at.selectbox(key="alarm_pick")
    assert pick.options and not at.exception
    first = api_get(env, f"{API}/alarm-events", sort="priority", page_size=1)["items"][0]
    assert pick.options[0] == first["alarm_event_id"]                       # priority order puts the most severe first
    trace = api_get(env, f"{API}/alarm-events/{first['alarm_event_id']}/trace", cutoff=k["meta"]["cutoff"])
    assert metrics(at)["Severity"] == trace["alarm"]["severity"] and metrics(at)["Acknowledgement"] == trace["ack_outcome"]


def test_alarm_page_filters_and_sorting(env):
    at = run("3_alarm_monitoring.py")
    at.sidebar.selectbox(key="flt_severity").select("CRITICAL").run()
    assert not at.exception
    expect = api_get(env, f"{API}/analytics/kpis", severity="CRITICAL")
    assert metrics(at)["Alarms analysed"] == num(expect["alarms"]["total"])
    at.radio(key="alarm_sort").set_value("time").run()
    at.selectbox(key="alarm_acked").select("not acknowledged").run()
    assert not at.exception
    un = api_get(env, f"{API}/alarm-events", severity="CRITICAL", acknowledged="false", sort="time", page_size=1)
    if un["items"]:
        assert at.selectbox(key="alarm_pick").options[0] == un["items"][0]["alarm_event_id"]


# ---------------------------------------------------------------- page 4: incidents
def test_incident_page_summary_and_detail(env):
    at = run("4_incident_management.py")
    inc = api_get(env, f"{API}/analytics/kpis/incidents")
    m = metrics(at)
    assert m["Incidents"] == num(inc["total"]) and m["Open (not closed)"] == num(inc["open"])
    ids = at.selectbox(key="incident_pick").options
    assert len(ids) == inc["total"]
    good = next(i for i in api_get(env, f"{API}/incidents", page_size=100)["items"] if i["trigger_machine_state_event_id"])
    at.selectbox(key="incident_pick").select(good["incident_id"]).run()
    assert not at.exception and metrics(at)["Severity"] == good["severity"] and metrics(at)["Status"] == good["status"]
    assert {t.label for t in at.tabs} >= {"Machine-state context", "Triggering alarms", "Operator", "Lifecycle", "Corrective actions"}
    assert not at.warning


def test_incident_without_context_is_flagged_on_screen(env):
    at = run("4_incident_management.py")
    bad = next(i for i in api_get(env, f"{API}/incidents", page_size=100)["items"] if i["trigger_machine_state_event_id"] is None)
    at.selectbox(key="incident_pick").select(bad["incident_id"]).run()
    assert not at.exception
    assert any("traceability is incomplete" in w.value for w in at.warning)


def test_incident_status_filter(env):
    at = run("4_incident_management.py")
    at.sidebar.selectbox(key="flt_status").select("RESOLVED").run()
    assert not at.exception
    expect = api_get(env, f"{API}/incidents", status="RESOLVED", page_size=100)
    assert at.selectbox(key="incident_pick").options == [i["incident_id"] for i in expect["items"]]


# ---------------------------------------------------------------- page 5: corrective actions
def test_corrective_action_page(env):
    at = run("5_corrective_actions.py")
    ca = api_get(env, f"{API}/analytics/kpis/corrective_actions")
    m = metrics(at)
    assert m["Completion rate"] == f"{ca['completion_rate_pct']:,.1f} %" and m["Verification rate"] == f"{ca['verification_rate_pct']:,.1f} %"
    assert m["Closed without verification"] == num(ca["closed_without_verification"]) and ca["closed_without_verification"] == STARTER.n_action_closed_unverified
    bad = next(a for a in api_get(env, f"{API}/corrective-actions", status="CLOSED", page_size=100)["items"] if not a["verification_result"])
    at.selectbox(key="action_pick").select(bad["corrective_action_id"]).run()
    assert not at.exception and any("without a PASSED verification" in e.value for e in at.error)
    at.selectbox(key="act_status_filter").select("PLANNED").run()
    planned = api_get(env, f"{API}/corrective-actions", status="PLANNED", page_size=100)
    assert at.selectbox(key="action_pick").options == [a["corrective_action_id"] for a in planned["items"]]


# ---------------------------------------------------------------- page 6: data quality
def test_data_quality_page(env):
    at = run("6_data_quality_anomalies.py")
    rep = api_get(env, f"{API}/validation/report", max_errors=1)["summary"]
    m = metrics(at)
    assert m["Records checked"] == num(rep["total_records_checked"]) and m["Validations run"] == "17"
    assert m["Passed"] == str(rep["validations_passed"]) and m["Failed"] == str(rep["validations_failed"]) == "5"
    assert len(at.expander) >= 5                                              # one per failed validation + evaluation details
    ev = api_get(env, f"{API}/anomalies/evaluation")
    assert any("DETECTION EVALUATION" in w.value for w in at.warning)
    assert f"{ev['anomalies']['micro']['f1']:.3f}" in [m_.value for m_ in at.metric if m_.label == "F1 (micro)"]
    assert f"{ev['patterns']['micro']['f1']:.3f}" in [m_.value for m_ in at.metric if m_.label == "F1 (micro)"]
    summary = api_get(env, f"{API}/anomalies/summary")
    at.radio(key="anom_kind").set_value("pattern").run()
    assert not at.exception
    assert set(at.selectbox(key="anom_issue").options) == {"All"} | set(summary["pattern"]["by_issue_type"])
    at.selectbox(key="anom_issue").select("NUISANCE_CLUSTER").run()
    assert not at.exception


# ---------------------------------------------------------------- behaviour
def test_api_outage_is_reported_not_crashed(monkeypatch):
    import streamlit as st
    monkeypatch.setenv("API_BASE_URL", "http://127.0.0.1:9")
    st.cache_data.clear()
    at = AppTest.from_file(str(PAGES / "1_executive_dashboard.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("UNREACHABLE" in e.value for e in at.error)


def test_frontend_has_no_backend_or_database_dependencies():
    """Tier separation: the dashboard may only use the REST client."""
    banned = re.compile(r"^\s*(from|import)\s+(app|backend|sqlalchemy|psycopg|data_generator)\b", re.M)
    for path in (ROOT / "frontend").rglob("*.py"):
        assert not banned.search(path.read_text(encoding="utf-8")), path
    http = [p.name for p in (ROOT / "frontend").rglob("*.py") if re.search(r"^\s*import requests", p.read_text(encoding="utf-8"), re.M)]
    assert http == ["api_client.py"]


def test_dashboard_shows_new_data_without_any_code_change(env):
    """Nothing is hard-coded: a new alarm written through the API changes the tile after a refresh."""
    import streamlit as st
    machine = api_get(env, f"{API}/machines/P01-M01")
    defs = api_get(env, f"{API}/alarm-definitions", trigger_type="PROCESS_LIMIT", machine_type=machine["machine_type"], page_size=50)["items"]
    assert defs
    before = int(metrics(run("1_executive_dashboard.py"))["Total alarm events"].replace(",", ""))
    r = requests.post(f"{env}{API}/alarm-events", json=dict(machine_id="P01-M01", alarm_definition_id=defs[0]["alarm_definition_id"],
                                                           raised_at="2026-10-07T12:34:56+00:00"), timeout=60)
    assert r.status_code == 201, r.text
    st.cache_data.clear()
    after = int(metrics(run("1_executive_dashboard.py"))["Total alarm events"].replace(",", ""))
    assert after == before + 1
