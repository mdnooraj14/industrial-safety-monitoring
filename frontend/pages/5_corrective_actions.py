"""Page 5 - Corrective-action tracking: engineer, description, status, completion timeline, verification."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from frontend.components import charts
from frontend.components.data import api, fetch_all
from frontend.components.filters import kpi_params, render_filters
from frontend.components.format import num, pct, ts
from frontend.components.layout import chart, cutoff_caption, setup, severity_style, table, tiles

setup("Corrective Action Tracking", "Who is doing what, how far it has got, and whether it was verified.",
      ":material/build:")
f = render_filters(("plant", "machine", "date"))
k = api("/api/v1/analytics/kpis", **kpi_params(f))
ca = k["corrective_actions"]
tiles([("Actions (live)", f"{num(ca['total'])} ({num(ca['live'])})", "live = not cancelled"),
       ("Completion rate", pct(ca["completion_rate_pct"]), ca["definition"]),
       ("Verification rate", pct(ca["verification_rate_pct"])),
       ("Overdue", num(ca["overdue"]), "due date passed and not closed")])
tiles([("Failed verifications", num(ca["failed_verifications"])),
       ("Closed without verification", num(ca["closed_without_verification"]), "violates rule CA-3 - a data-quality finding"),
       ("Average days to close", num(ca["avg_days_to_close"], 1))], per_row=3)
cutoff_caption(k)

if ca["by_status"]:
    df = pd.DataFrame(dict(status=list(ca["by_status"]), actions=list(ca["by_status"].values())))
    chart(charts.simple_bar(df, "status", "actions", "Actions by status", height=280), "act_status")

st.subheader("Actions")
c1, c2, c3, c4 = st.columns(4)
status = c1.selectbox("Status", ["All", "PLANNED", "IN_PROGRESS", "COMPLETED", "VERIFIED", "CLOSED", "CANCELLED"], key="act_status_filter")
verification = c2.selectbox("Verification", ["All", "PASSED", "FAILED"], key="act_ver_filter")
overdue = c3.selectbox("Overdue", ["All", "overdue only", "not overdue"], key="act_overdue_filter")
engineer = c4.text_input("Engineer id (EMP-nnnnn)", key="act_engineer")
rows, total = fetch_all("/api/v1/corrective-actions", limit=500, plant_id=f["plant_id"], machine_id=f["machine_id"],
                        status=None if status == "All" else status, verification_result=None if verification == "All" else verification,
                        overdue={"All": None, "overdue only": True, "not overdue": False}[overdue],
                        assigned_engineer_id=engineer.strip() or None)
st.caption(f"{num(total)} actions match" + (f"; showing {len(rows)}." if total > len(rows) else "."))
for r in rows:
    r["verified"] = "yes" if r["verification_result"] == "PASSED" else ("FAILED" if r["verification_result"] else "no")
table(rows, ["corrective_action_id", "incident_id", "assigned_engineer_name", "assigned_engineer_id", "action_type", "action_description",
             "status", "planned_at", "due_at", "completed_at", "verified", "verified_by_id", "closed_at"], height=320, style=severity_style)
if not rows:
    st.stop()

st.subheader("Completion timeline")
chart(charts.action_gantt(rows[:40]), "act_gantt")
st.caption("Each bar runs from planning to the latest recorded milestone (start, completion, verification or closure).")

st.subheader("Action details")
pick = st.selectbox("Action", [r["corrective_action_id"] for r in rows], key="action_pick")
a = api(f"/api/v1/corrective-actions/{pick}")
st.markdown(f"**{a['action_type']}** on incident `{a['incident_id']}` - assigned to **{a['assigned_engineer_name']}** ({a['assigned_engineer_id']})")
st.write(a["action_description"])
if a["status"] == "CLOSED" and a["verification_result"] != "PASSED":
    st.error("This action is CLOSED without a PASSED verification (rule CA-3).")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Status", a["status"])
m2.metric("Verification", a["verification_result"] or "none")
m3.metric("Due", ts(a["due_at"]))
m4.metric("Closed", ts(a["closed_at"]))
table(a["history"], ["sequence_no", "from_status", "to_status", "changed_at", "note"])
