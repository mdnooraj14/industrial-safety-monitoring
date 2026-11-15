"""Industrial Safety Monitoring - Streamlit dashboard (Tier 1). Talks to the FastAPI backend over REST only.

    streamlit run frontend/streamlit_app.py            (API_BASE_URL defaults to http://localhost:8000)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

st.set_page_config(page_title="Industrial Safety Monitoring", page_icon=":material/shield:", layout="wide")

PAGES = [
    ("pages/1_executive_dashboard.py", "Executive Dashboard", ":material/dashboard:", True),
    ("pages/2_plant_machine_monitoring.py", "Plants and Machines", ":material/precision_manufacturing:", False),
    ("pages/3_alarm_monitoring.py", "Alarm Monitoring", ":material/notifications_active:", False),
    ("pages/4_incident_management.py", "Incident Management", ":material/report:", False),
    ("pages/5_corrective_actions.py", "Corrective Actions", ":material/build:", False),
    ("pages/6_data_quality_anomalies.py", "Data Quality and Anomalies", ":material/fact_check:", False),
]
st.navigation([st.Page(p, title=t, icon=i, default=d) for p, t, i, d in PAGES]).run()
