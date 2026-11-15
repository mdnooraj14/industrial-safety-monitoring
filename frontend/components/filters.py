"""Sidebar filters shared by all pages (plant, machine, severity, date, incident status). Values live in session state,
so a selection made on one page carries over to the others. Filtering itself is done by the API."""
from __future__ import annotations

from datetime import date

import streamlit as st

from frontend.components.data import api

ALL = "All"
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
INCIDENT_STATUSES = ["OPEN", "INVESTIGATING", "ACTION_IN_PROGRESS", "RESOLVED", "CLOSED"]


def _choice(value: str):
    return None if value == ALL else value


def render_filters(show: tuple[str, ...] = ("plant", "machine", "severity", "date", "incident_status")) -> dict:
    """Draw the filter widgets; returns {plant_id, machine_id, severity, date_from, date_to, incident_status} (None = all)."""
    f: dict = dict(plant_id=None, machine_id=None, severity=None, date_from=None, date_to=None, incident_status=None)
    with st.sidebar:
        st.subheader("Filters")
        if "plant" in show:
            plants = api("/api/v1/plants", page_size=100)["items"]
            label_to_id = {f"{p['plant_id']} - {p['plant_name']}": p["plant_id"] for p in plants}
            sel = st.selectbox("Plant", [ALL] + list(label_to_id), key="flt_plant")
            f["plant_id"] = label_to_id.get(sel)
        if "machine" in show:
            machines = api("/api/v1/machines", plant_id=f["plant_id"], page_size=500)["items"]
            label_to_id = {f"{m['machine_id']} - {m['machine_name']}": m["machine_id"] for m in machines}
            if st.session_state.get("flt_machine") not in [ALL] + list(label_to_id):
                st.session_state["flt_machine"] = ALL          # the plant changed: forget a machine of another plant
            sel = st.selectbox("Machine", [ALL] + list(label_to_id), key="flt_machine")
            f["machine_id"] = label_to_id.get(sel)
        if "severity" in show:
            f["severity"] = _choice(st.selectbox("Severity", [ALL] + SEVERITIES, key="flt_severity"))
        if "date" in show:
            days = api("/api/v1/analytics/kpis/alarms_per_day")
            if days:
                lo, hi = date.fromisoformat(days[0]["day"]), date.fromisoformat(days[-1]["day"])
                picked = st.date_input("Date range (UTC)", value=(lo, hi), min_value=lo, max_value=hi, key="flt_dates")
                if isinstance(picked, (list, tuple)) and len(picked) == 2 and (picked[0], picked[1]) != (lo, hi):
                    f["date_from"], f["date_to"] = picked
        if "incident_status" in show:
            f["incident_status"] = _choice(st.selectbox("Incident status", [ALL] + INCIDENT_STATUSES, key="flt_status"))
        if any(v for v in f.values()):
            st.caption("Active filters: " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in f.items() if v))
    return f


def kpi_params(f: dict) -> dict:
    return {k: v for k, v in f.items() if v}
