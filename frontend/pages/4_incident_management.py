"""Page 4 - Incident management: list, details, triggering alarms, machine-state context, operator, lifecycle."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from frontend.components import charts
from frontend.components.data import api, fetch_all
from frontend.components.filters import kpi_params, render_filters
from frontend.components.format import hours, num, seconds, ts
from frontend.components.layout import chart, cutoff_caption, setup, severity_style, table, tiles

setup("Incident Management", "From unsafe machine state to alarms, operator, lifecycle and corrective actions.",
      ":material/report:")
f = render_filters()
k = api("/api/v1/analytics/kpis", **kpi_params(f))
inc = k["incidents"]
tiles([("Incidents", num(inc["total"]), f"{inc['critical']} critical"), ("Open (not closed)", num(inc["open"]), f"{inc['unresolved']} not yet resolved"),
       ("Mean time to resolution", hours(inc["mttr_hours"]), inc["definition"]),
       ("Mean time to start investigation", hours(inc["mean_time_to_investigate_hours"]))])
cutoff_caption(k)

left, right = st.columns(2)
with left:
    if inc["by_status"]:
        df = pd.DataFrame(dict(status=list(inc["by_status"]), incidents=list(inc["by_status"].values())))
        chart(charts.simple_bar(df, "status", "incidents", "Incidents by status", color="status", colors=charts.STATUS_COLORS), "inc_status")
with right:
    if inc["by_machine"]:
        chart(charts.simple_bar(pd.DataFrame(inc["by_machine"][:10]), "machine_id", "incidents", "Incidents by machine", horizontal=True), "inc_machine")

st.subheader("Incidents")
rows, total = fetch_all("/api/v1/incidents", limit=500, plant_id=f["plant_id"], machine_id=f["machine_id"], severity=f["severity"],
                        status=f["incident_status"], from_ts=f["date_from"], to_ts=f["date_to"] and f"{f['date_to']}T23:59:59")
st.caption(f"{num(total)} incidents match the filters" + (f"; showing {len(rows)}." if total > len(rows) else "."))
table(rows, ["incident_id", "machine_id", "severity", "status", "trigger_rule_code", "title", "opened_at", "investigation_started_at",
             "resolved_at", "closed_at"], height=300, style=severity_style)
if not rows:
    st.stop()

st.subheader("Incident details")
pick = st.selectbox("Incident", [r["incident_id"] for r in rows], key="incident_pick")
d = api(f"/api/v1/incidents/{pick}")
i = d["incident"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Severity", i["severity"])
c2.metric("Status", i["status"])
c3.metric("Rule", i["trigger_rule_code"] or "none")
c4.metric("Open for", seconds((pd.Timestamp(i["closed_at"] or k["meta"]["cutoff"]) - pd.Timestamp(i["opened_at"])).total_seconds()))
st.markdown(f"**{i['title']}** - machine `{i['machine_id']}`, zone `{i['safety_zone_id'] or 'n/a'}`, opened {ts(i['opened_at'])} UTC")
if i["description"]:
    st.caption(i["description"])
if not d["context_complete"]:
    st.warning("This incident has no trigger machine state and/or no linked trigger alarm - its traceability is incomplete "
               "(a data-quality finding, see the Data Quality page).")

tab_state, tab_alarm, tab_op, tab_life, tab_act = st.tabs(
    ["Machine-state context", "Triggering alarms", "Operator", "Lifecycle", "Corrective actions"])
with tab_state:
    s = d["trigger_state"]
    if s:
        st.markdown(f"State event `{s['machine_state_event_id']}` at {ts(s['event_ts'])} UTC: **{s['machine_state']}**, door "
                    f"{s['door_state']}, interlocks {s['interlock_status_summary']} - assessment **{s['safety_assessment']}** "
                    f"({s['unsafe_rule_code']}).")
        table(s["interlock_events"], ["interlock_id", "interlock_status", "door_state_observed", "event_ts"])
    else:
        st.caption("No trigger machine state is recorded for this incident.")
with tab_alarm:
    table([dict(role=a["link_role"], **a["alarm"]) for a in d["alarms"]],
          ["role", "alarm_event_id", "severity", "alarm_definition_id", "raised_at", "cleared_at", "ack_deadline_at"], style=severity_style)
    if not d["alarms"]:
        st.caption("No alarms are linked to this incident.")
with tab_op:
    sh = d["shift"]
    if sh:
        st.markdown(f"**{sh['operator_name']}** ({sh['employee_id']}), {sh['operator_role']} on shift {sh['shift_code']} - "
                    f"{ts(sh['shift_start'])} to {ts(sh['shift_end'])} UTC (`{sh['shift_id']}`).")
    else:
        st.caption("No operator shift is recorded for this incident.")
with tab_life:
    chart(charts.lifecycle_timeline(d["status_history"], "Incident lifecycle"), "inc_life")
    table(d["status_history"], ["sequence_no", "from_status", "to_status", "changed_at", "changed_by_role", "note"])
with tab_act:
    table([{k_: v for k_, v in a.items() if k_ != "history"} for a in d["corrective_actions"]],
          ["corrective_action_id", "action_type", "status", "assigned_engineer_name", "planned_at", "due_at", "completed_at",
           "verification_result", "verified_by_id", "closed_at"])
    for a in d["corrective_actions"]:
        with st.expander(f"History of {a['corrective_action_id']}"):
            table(a["history"], ["sequence_no", "from_status", "to_status", "changed_at", "note"])
