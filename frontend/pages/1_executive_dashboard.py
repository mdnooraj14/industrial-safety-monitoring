"""Page 1 - Executive dashboard: headline KPIs and where the risk is."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from frontend.components import charts
from frontend.components.data import api
from frontend.components.filters import kpi_params, render_filters
from frontend.components.format import hours, num, pct
from frontend.components.layout import chart, cutoff_caption, setup, severity_style, table, tiles

setup("Executive Dashboard", "Plant-wide safety performance computed by the API from the event history.",
      ":material/dashboard:")
f = render_filters()
k = api("/api/v1/analytics/kpis", **kpi_params(f))
o, a, ak, inc, ca = k["overview"], k["alarms"], k["acknowledgements"], k["incidents"], k["corrective_actions"]

tiles([
    ("Total plants", num(o["plants"])),
    ("Total machines", num(o["machines"])),
    ("Total alarm events", num(o["alarm_events_stored"]),
     f"{num(o['alarm_events_analysed'])} analysed after excluding {num(o['replay_duplicates_excluded'])} replay duplicates"),
    ("Critical incidents", num(o["critical_incidents"]), f"of {num(o['incidents'])} incidents"),
    ("Open incidents", num(o["open_incidents"]), f"{num(o['unresolved_incidents'])} not yet resolved"),
    ("Delayed acknowledgements", num(o["delayed_acknowledgements"]),
     f"{pct(o['delayed_acknowledgement_pct'])} of alarms whose deadline has passed (late + missed)"),
    ("Repeated unsafe conditions", num(o["repeated_unsafe_conditions"]),
     "same unsafe rule at least 3 times on one machine within 7 days"),
])
st.divider()
tiles([
    ("Mean time to resolution", hours(inc["mttr_hours"]), f"{inc['resolved_count']} resolved incidents"),
    ("Alarm floods", num(o["alarm_floods"]), "10+ alarms on one machine within 10 minutes"),
    ("Nuisance alarm clusters", num(o["nuisance_clusters"])),
    ("Action completion / verification", f"{pct(ca['completion_rate_pct'])} / {pct(ca['verification_rate_pct'])}",
     "corrective actions completed, and completed actions verified"),
])
cutoff_caption(k)

left, right = st.columns(2)
with left:
    days = pd.DataFrame(k["alarms_per_day"])
    if len(days):
        chart(charts.severity_stacked_bar(days, "day", "Alarms per day by severity"), "exec_per_day")
    else:
        st.caption("No alarms for the current filters.")
with right:
    plants = pd.DataFrame(a["by_plant"])
    if len(plants):
        chart(charts.severity_stacked_bar(plants, "plant_id", "Alarms by plant"), "exec_plant")

left, right = st.columns(2)
with left:
    machines = pd.DataFrame(a["by_machine"][:10])
    if len(machines):
        chart(charts.severity_stacked_bar(machines, "machine_id", "Top 10 machines by alarms", horizontal=True), "exec_machines")
with right:
    by_m = pd.DataFrame(inc["by_machine"][:10])
    if len(by_m):
        chart(charts.simple_bar(by_m, "machine_id", "incidents", "Incidents by machine (top 10)", horizontal=True), "exec_inc_machine")
    else:
        st.caption("No incidents for the current filters.")

st.subheader("Open incidents")
table(inc["open_incidents"], ["incident_id", "machine_id", "severity", "status", "opened_at", "age_hours"], height=300,
      style=severity_style)
