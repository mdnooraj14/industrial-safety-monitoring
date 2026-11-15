"""Page 3 - Alarm monitoring: timeline, severity, acknowledgement delays, floods, nuisance alarms, drill-down."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from frontend.components import charts
from frontend.components.data import api, fetch_all
from frontend.components.filters import kpi_params, render_filters
from frontend.components.format import ts, num, pct, seconds
from frontend.components.layout import chart, cutoff_caption, setup, severity_style, table, tiles

setup("Alarm Monitoring", "Alarm events, severity, acknowledgement delays, floods and nuisance alarms.",
      ":material/notifications_active:")
f = render_filters(("plant", "machine", "severity", "date"))
k = api("/api/v1/analytics/kpis", **kpi_params(f))
ak, a, fl, nu = k["acknowledgements"], k["alarms"], k["floods"], k["nuisance"]

tiles([("Alarms analysed", num(a["total"])), ("Critical + high", num(a["critical_and_high"]), f"{a['critical']} critical, {a['high']} high"),
       ("Average acknowledgement time", seconds(ak["ack_seconds"]["mean"]), f"median {seconds(ak['ack_seconds']['median'])}, p90 {seconds(ak['ack_seconds']['p90'])}"),
       ("Delayed acknowledgements", pct(ak["delayed_pct"]), ak["definition"])])
cutoff_caption(k)

tab_time, tab_sev, tab_ack, tab_flood, tab_nuis, tab_list = st.tabs(
    ["Timeline", "Severity", "Acknowledgement delays", "Alarm floods", "Nuisance alarms", "Alarm list and drill-down"])

alarm_filters = dict(plant_id=f["plant_id"], machine_id=f["machine_id"], severity=f["severity"], from_ts=f["date_from"],
                     to_ts=f["date_to"] and f"{f['date_to']}T23:59:59")

with tab_time:
    rows, total = fetch_all("/api/v1/alarm-events", limit=1500, **alarm_filters)
    if rows:
        chart(charts.alarm_timeline(pd.DataFrame(rows)), "alarm_timeline")
        st.caption(f"Showing the newest {len(rows)} of {total} matching alarm events.")
    else:
        st.caption("No alarm events for the current filters.")
    days = pd.DataFrame(k["alarms_per_day"])
    if len(days):
        chart(charts.severity_stacked_bar(days, "day", "Alarms per day by severity"), "alarm_per_day")

with tab_sev:
    sev = a["by_severity"]
    left, right = st.columns(2)
    with left:
        chart(charts.donut(list(sev), list(sev.values()), "Severity distribution", charts.SEVERITY_COLORS), "sev_donut")
    with right:
        table([dict(severity=s, alarms=n, priority_rank=i + 1) for i, (s, n) in enumerate(sev.items())])
        policy = api("/api/v1/alarm-definitions/severity-policy")
        st.markdown("**Acknowledgement deadlines (severity policy)**")
        table([dict(severity=p["severity"], priority_rank=p["priority_rank"], deadline=seconds(p["ack_deadline_seconds"]),
                    escalation=seconds(p["escalation_after_seconds"])) for p in policy])
    plants = pd.DataFrame(a["by_machine"][:15])
    if len(plants):
        chart(charts.severity_stacked_bar(plants, "machine_id", "Machines with the most alarms", horizontal=True, height=420), "sev_machines")

with tab_ack:
    tiles([("Acknowledged", num(ak["acknowledged"])), ("On time", num(ak["on_time"])), ("Late", num(ak["late"])),
           ("Missed", num(ak["missed"]), "no acknowledgement and the deadline has passed")], per_row=4)
    left, right = st.columns(2)
    with left:
        chart(charts.simple_bar(pd.DataFrame(ak["delay_buckets"]), "bucket", "count", "Acknowledgement time distribution"), "ack_buckets")
    with right:
        st.markdown("**By severity**")
        table([dict(severity=s, **v) for s, v in ak["by_severity"].items()],
              ["severity", "alarms", "acknowledged", "avg_ack_seconds", "late", "missed", "delayed_pct"])
    st.markdown("**Slowest acknowledgements (60 minutes or more)**")
    slow = api("/api/v1/acknowledgements", min_delay_seconds=3600, page_size=25, **{k_: v for k_, v in dict(
        machine_id=f["machine_id"]).items() if v})
    table(slow["items"], ["acknowledgement_id", "alarm_event_id", "acknowledged_at", "ack_delay_seconds", "ack_status"],
          style=severity_style)
    st.caption(f"{slow['total']} acknowledgements took 60 minutes or longer.")

with tab_flood:
    tiles([("Flood episodes", num(fl["episodes"])), ("Alarms inside floods", num(fl["alarms_in_floods"]), pct(fl["alarms_in_floods_pct"])),
           ("Floods per day", num(fl["floods_per_day"], 2))], per_row=3)
    st.caption("A flood is 10 or more alarms on one machine within 10 minutes; it lasts until fewer than 5 remain in the window.")
    det = pd.DataFrame(fl["episodes_detail"])
    if len(det):
        chart(charts.simple_bar(pd.DataFrame(dict(machine=list(fl["by_machine"]), floods=list(fl["by_machine"].values()))),
                                "machine", "floods", "Floods by machine"), "flood_machines")
        table(det.assign(start=det["start"].map(ts), end=det["end"].map(ts)))

with tab_nuis:
    tiles([("Nuisance clusters", num(nu["clusters"])), ("Alarms in clusters", num(nu["alarms_in_clusters"]), pct(nu["alarms_in_clusters_pct"]))], per_row=2)
    st.caption("N1 chattering: 5+ raises within 30 minutes, each cleared within 2 minutes. N2 repeating: 20+ low-severity raises in a day.")
    det = pd.DataFrame(nu["detail"])
    if len(det):
        det["rules"] = det["rules"].map(", ".join)
        table(det.assign(first=det["first"].map(ts), last=det["last"].map(ts)))
    rep = k["repeated_alarms"]
    st.markdown(f"**Repeated alarm patterns:** {rep['patterns']} (same alarm 3+ times within 24 h on one machine)")
    table(rep["top"])

with tab_list:
    sort = st.radio("Order", ["priority", "time"], horizontal=True, key="alarm_sort",
                    help="priority puts CRITICAL first, then newest (rule AL-3)")
    acked = st.selectbox("Acknowledgement", ["any", "acknowledged", "not acknowledged"], key="alarm_acked")
    page = api("/api/v1/alarm-events", sort=sort, page_size=100,
               acknowledged={"any": None, "acknowledged": True, "not acknowledged": False}[acked], **alarm_filters)
    st.caption(f"{num(page['total'])} alarm events match; showing the first {len(page['items'])}.")
    table(page["items"], ["alarm_event_id", "machine_id", "severity", "alarm_definition_id", "raised_at", "cleared_at",
                          "ack_deadline_at", "lifecycle_status"], height=320, style=severity_style)
    if page["items"]:
        pick = st.selectbox("Drill down into an alarm", [r["alarm_event_id"] for r in page["items"]], key="alarm_pick")
        t = api(f"/api/v1/alarm-events/{pick}/trace", cutoff=k["meta"]["cutoff"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Severity", t["alarm"]["severity"])
        c2.metric("Acknowledgement", t["ack_outcome"])
        c3.metric("Machine state at raise", (t["machine_state"] or {}).get("safety_assessment", "n/a"))
        st.markdown(f"**{t['definition']['alarm_code']}** - {t['definition']['alarm_name']} on {t['machine']['machine_name']} "
                    f"({t['machine']['machine_id']}); raised {ts(t['alarm']['raised_at'])} UTC, acknowledgement due "
                    f"{ts(t['alarm']['ack_deadline_at'])}.")
        if t["machine_state"]:
            s = t["machine_state"]
            st.markdown(f"Machine state: **{s['machine_state']}**, door {s['door_state']}, interlocks {s['interlock_status_summary']} "
                        f"({s['safety_assessment']}{' ' + s['unsafe_rule_code'] if s['unsafe_rule_code'] else ''}).")
        if t["shift"]:
            st.markdown(f"Operator on duty: **{t['shift']['operator_name']}** ({t['shift']['employee_id']}), shift {t['shift']['shift_id']}.")
        if t["acknowledgement"]:
            ack = t["acknowledgement"]
            st.markdown(f"Acknowledged {ts(ack['acknowledged_at'])} UTC after {seconds(ack['ack_delay_seconds'])} ({ack['ack_status']}).")
        st.markdown("**Linked incidents**")
        table(t["incidents"])
