"""Plotly chart builders with one consistent palette (presentation only)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEVERITY_COLORS = {"CRITICAL": "#b42318", "HIGH": "#e8590c", "MEDIUM": "#d9a400", "LOW": "#5b7c99"}
ASSESSMENT_COLORS = {"SAFE": "#2f9e44", "UNSAFE": "#b42318", "INVALID": "#e8590c"}
STATUS_COLORS = {"OPEN": "#b42318", "INVESTIGATING": "#e8590c", "ACTION_IN_PROGRESS": "#d9a400", "RESOLVED": "#2f9e44",
                 "CLOSED": "#5b7c99"}
_LAYOUT = dict(margin=dict(l=10, r=10, t=40, b=10), legend_title_text="", title_x=0.0)


def _fig(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(height=height, **_LAYOUT)
    return fig


def severity_stacked_bar(df: pd.DataFrame, x: str, title: str, horizontal: bool = False, height: int = 340) -> go.Figure:
    cols = [s.lower() for s in SEVERITY_ORDER if s.lower() in df.columns]
    long = df.melt(id_vars=[x], value_vars=cols, var_name="severity", value_name="alarm_count")
    long["severity"] = long["severity"].str.upper()
    fig = px.bar(long, x="alarm_count" if horizontal else x, y=x if horizontal else "alarm_count", color="severity",
                 color_discrete_map=SEVERITY_COLORS, category_orders={"severity": SEVERITY_ORDER}, title=title,
                 orientation="h" if horizontal else "v", labels={"alarm_count": "alarms"})
    if horizontal:
        fig.update_yaxes(autorange="reversed")
    return _fig(fig, height)


def simple_bar(df: pd.DataFrame, x: str, y: str, title: str, color: str | None = None, height: int = 320,
               colors: dict | None = None, horizontal: bool = False) -> go.Figure:
    fig = px.bar(df, x=y if horizontal else x, y=x if horizontal else y, color=color, title=title,
                 color_discrete_map=colors or {}, orientation="h" if horizontal else "v")
    if horizontal:
        fig.update_yaxes(autorange="reversed")
    return _fig(fig, height)


def donut(labels: list[str], values: list[int], title: str, colors: dict) -> go.Figure:
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.55, sort=False,
                           marker=dict(colors=[colors.get(l, "#888") for l in labels])))
    fig.update_layout(title=title)
    return _fig(fig, 320)


def alarm_timeline(df: pd.DataFrame) -> go.Figure:
    df = df.copy()
    df["raised_at"] = pd.to_datetime(df["raised_at"])
    fig = px.scatter(df, x="raised_at", y="machine_id", color="severity", color_discrete_map=SEVERITY_COLORS,
                     category_orders={"severity": SEVERITY_ORDER}, hover_data=["alarm_event_id", "alarm_definition_id"],
                     title="Alarm events over time (UTC)")
    fig.update_traces(marker=dict(size=6, opacity=0.75))
    return _fig(fig, max(320, 60 + 28 * df["machine_id"].nunique()))


def state_timeline(df: pd.DataFrame, title: str) -> go.Figure:
    """Step chart of machine operating state with markers for UNSAFE / INVALID events."""
    df = df.copy()
    df["event_ts"] = pd.to_datetime(df["event_ts"])
    order = ["MAINTENANCE", "STOPPED", "IDLE", "RUNNING", "FAULT", "E_STOPPED"]
    fig = go.Figure(go.Scatter(x=df["event_ts"], y=df["machine_state"], mode="lines", line=dict(shape="hv", color="#5b7c99"),
                               name="state", hoverinfo="skip"))
    for a in ("SAFE", "UNSAFE", "INVALID"):
        sub = df[df["safety_assessment"] == a]
        if len(sub):
            fig.add_trace(go.Scatter(x=sub["event_ts"], y=sub["machine_state"], mode="markers", name=a,
                                     marker=dict(size=9 if a != "SAFE" else 4, color=ASSESSMENT_COLORS[a]),
                                     text=sub["machine_state_event_id"], hovertemplate="%{text}<br>%{x}<extra>" + a + "</extra>"))
    fig.update_yaxes(categoryorder="array", categoryarray=order)
    fig.update_layout(title=title)
    return _fig(fig, 360)


def heatmap(matrix: dict, labels: list[str], title: str) -> go.Figure:
    z = [[matrix[a][p] for p in labels] for a in labels]
    fig = go.Figure(go.Heatmap(z=z, x=labels, y=labels, text=z, texttemplate="%{text}", colorscale="Blues", showscale=False))
    fig.update_xaxes(title="predicted (detector)", side="bottom")
    fig.update_yaxes(title="actual (ground truth)", autorange="reversed")
    fig.update_layout(title=title)
    return _fig(fig, 420)


def action_gantt(rows: list[dict]) -> go.Figure:
    """Planned -> completed span for each action, coloured by status."""
    recs = []
    for a in rows:
        end = a["closed_at"] or a["verified_at"] or a["completed_at"] or a["started_at"] or a["planned_at"]
        recs.append(dict(action=a["corrective_action_id"], start=a["planned_at"], end=end, status=a["status"]))
    df = pd.DataFrame(recs)
    df["start"], df["end"] = pd.to_datetime(df["start"]), pd.to_datetime(df["end"])
    fig = px.timeline(df, x_start="start", x_end="end", y="action", color="status", title="Action timelines (planned to latest milestone)")
    fig.update_yaxes(autorange="reversed")
    return _fig(fig, max(300, 40 + 26 * len(df)))


def lifecycle_timeline(history: list[dict], title: str) -> go.Figure:
    """Incident lifecycle: one marker per status change, joined in time order."""
    df = pd.DataFrame(history)
    df["changed_at"] = pd.to_datetime(df["changed_at"])
    fig = go.Figure(go.Scatter(x=df["changed_at"], y=df["to_status"], mode="lines+markers+text", text=df["changed_by_role"],
                               textposition="top center", line=dict(color="#5b7c99", shape="hv"),
                               marker=dict(size=11, color=[STATUS_COLORS.get(s, "#888") for s in df["to_status"]])))
    fig.update_yaxes(categoryorder="array", categoryarray=["CLOSED", "RESOLVED", "ACTION_IN_PROGRESS", "INVESTIGATING", "OPEN"])
    fig.update_layout(title=title)
    return _fig(fig, 300)
