"""Page 2 - Plant and machine monitoring: plant overview, machine status, state history, interlocks, safety zones."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from frontend.components import charts
from frontend.components.data import api, fetch_all
from frontend.components.filters import ALL, kpi_params, render_filters
from frontend.components.format import num, ts
from frontend.components.layout import chart, setup, severity_style, table, tiles

setup("Plant and Machine Monitoring", "Operational status, machine-state history, interlocks and safety zones.",
      ":material/precision_manufacturing:")
f = render_filters(("plant", "machine", "date"))

# ------------------------------------------------------------------ plant overview
st.subheader("Plant overview")
plants = api("/api/v1/plants", page_size=100)["items"]
kpis = api("/api/v1/analytics/kpis", **kpi_params(f))
alarms_by_plant = {r["plant_id"]: r for r in kpis["alarms"]["by_plant"]}
rows = []
for p in plants:
    if f["plant_id"] and p["plant_id"] != f["plant_id"]:
        continue
    n_machines = api("/api/v1/machines", plant_id=p["plant_id"], page_size=1)["total"]
    n_zones = api("/api/v1/safety-zones", plant_id=p["plant_id"], page_size=1)["total"]
    al = alarms_by_plant.get(p["plant_id"], {})
    rows.append(dict(plant=f"{p['plant_id']} {p['plant_name']}", city=p["city"], industry=p["industry_type"],
                     status=p["lifecycle_status"], machines=n_machines, safety_zones=n_zones, alarms=al.get("alarms", 0),
                     critical=al.get("critical", 0), high=al.get("high", 0)))
table(rows)

# ------------------------------------------------------------------ machine operational status
st.subheader("Machine operational status")
status_rows = api("/api/v1/machines/status", plant_id=f["plant_id"], machine_id=f["machine_id"], page_size=500)["items"]
latest = pd.DataFrame(status_rows)
if latest.empty:
    st.caption("No machines for the current filters.")
    st.stop()
by_assessment = latest["safety_assessment"].value_counts()
tiles([("Machines shown", num(len(latest))), ("Currently UNSAFE", num(int(by_assessment.get("UNSAFE", 0))),
                                              "latest machine state is an unsafe combination"),
       ("Currently RUNNING", num(int((latest["machine_state"] == "RUNNING").sum()))),
       ("Flagged INVALID", num(int(by_assessment.get("INVALID", 0))), "latest reading is an impossible interlock state")])
table(status_rows, ["machine_id", "machine_name", "plant_id", "machine_type", "machine_state", "door_state",
                    "interlock_status_summary", "safety_assessment", "unsafe_rule_code", "last_event_ts", "state_events"],
      height=300, style=severity_style)

# ------------------------------------------------------------------ one machine in detail
st.subheader("Machine detail")
ids = [r["machine_id"] for r in status_rows]
default = ids.index(f["machine_id"]) if f["machine_id"] in ids else 0
machine_id = st.selectbox("Machine", ids, index=default, key="detail_machine")
machine = api(f"/api/v1/machines/{machine_id}")
st.caption(f"{machine['machine_name']} - {machine['machine_type']} - criticality {machine['criticality_class']} - "
           f"{machine['lifecycle_status']}")

tab_hist, tab_ilk, tab_zone = st.tabs(["Machine-state history", "Interlock status", "Safety zones"])
with tab_hist:
    dates = {"from_ts": f["date_from"], "to_ts": f["date_to"] and f"{f['date_to']}T23:59:59"}
    events, total = fetch_all(f"/api/v1/machines/{machine_id}/state-events", limit=1500, order="asc", **dates)
    if events:
        chart(charts.state_timeline(pd.DataFrame(events), f"{machine_id}: operating state over time (UTC)"), "machine_states")
        if total > len(events):
            st.caption(f"Showing the first {len(events)} of {total} state events; narrow the date range to see the rest.")
        unsafe = [e for e in events if e["safety_assessment"] != "SAFE"]
        st.markdown(f"**Unsafe or invalid events ({len(unsafe)})**")
        table(unsafe, ["machine_state_event_id", "event_ts", "machine_state", "door_state", "interlock_status_summary",
                       "safety_assessment", "unsafe_rule_code", "transition_reason"], height=240, style=severity_style)
        with st.expander("Latest 30 state events"):
            table(list(reversed(events))[:30], ["machine_state_event_id", "event_ts", "previous_state", "machine_state",
                                                "door_state", "interlock_status_summary", "safety_assessment"], style=severity_style)
    else:
        st.caption("No state events in this range.")
with tab_ilk:
    ilk_rows = []
    for i in api(f"/api/v1/machines/{machine_id}/interlocks"):
        last = api(f"/api/v1/interlocks/{i['interlock_id']}/state-events", page_size=1)
        cur = last["items"][0] if last["items"] else {}
        ilk_rows.append(dict(interlock_id=i["interlock_id"], type=i["interlock_type"], required=i["is_required"],
                             bypass_allowed=i["bypass_allowed"], zone=i["safety_zone_id"], current_status=cur.get("interlock_status"),
                             door_observed=cur.get("door_state_observed"), since=ts(cur.get("event_ts")),
                             status_changes=last["total"]))
    table(ilk_rows)
with tab_zone:
    zone_rows = api(f"/api/v1/machines/{machine_id}/safety-zones")
    table(zone_rows, ["safety_zone_id", "zone_name", "zone_type", "risk_level", "lifecycle_status", "effective_from"])
