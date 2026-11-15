"""Page scaffolding: page config, API health banner, KPI tiles, table helper."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException



def setup(title: str, subtitle: str = "", icon: str = ":material/shield:") -> None:
    try:
        st.set_page_config(page_title=f"{title} - Industrial Safety", page_icon=icon, layout="wide")
    except StreamlitAPIException:       # already configured by the navigation entry point
        pass
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def tiles(items: list[tuple], per_row: int = 4) -> None:
    """items: (label, value, help_text) tuples shown as metric tiles."""
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, item in zip(cols, items[i:i + per_row]):
            label, value, *rest = item
            col.metric(label, value, help=rest[0] if rest else None)


def table(rows: list[dict] | pd.DataFrame, columns: list[str] | None = None, height: int | None = None,
          style=None, key: str | None = None):
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        st.caption("No rows for the current filters.")
        return
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    data = df.style.map(style, subset=[c for c in df.columns if c in ("safety_assessment", "status", "severity", "ack_status")]) \
        if style is not None else df
    st.dataframe(data, width="stretch", hide_index=True, height=height or min(38 + 35 * len(df), 460), key=key)


def chart(fig, key: str) -> None:
    st.plotly_chart(fig, width="stretch", key=key)


def severity_style(value) -> str:
    colors = {"CRITICAL": "#b42318", "HIGH": "#e8590c", "UNSAFE": "#b42318", "INVALID": "#e8590c", "LATE": "#e8590c",
              "OPEN": "#b42318", "SAFE": "#2f9e44", "CLOSED": "#5b7c99", "ON_TIME": "#2f9e44"}
    return f"color: {colors[value]}; font-weight: 600" if value in colors else ""


def cutoff_caption(kpis: dict) -> None:
    m = kpis["meta"]
    st.caption(f"Data snapshot (cut-off): {m['cutoff'].replace('T', ' ')[:19]} UTC. Replay duplicates and invalid "
               "acknowledgements are excluded from the KPIs and counted separately.")
