"""Alarm severity classification (AL-1), deadlines (AL-2), prioritisation (AL-3), acknowledgement delay (AL-4)."""
from datetime import datetime, timedelta

from .config import DEFAULT_RULES, RulesConfig

SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]  # ascending


def escalate(severity: str, steps: int = 1) -> str:
    return SEVERITY_ORDER[min(SEVERITY_ORDER.index(severity) + steps, len(SEVERITY_ORDER) - 1)]


def at_least(severity: str, floor: str) -> str:
    return severity if SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(floor) else floor


def classify_severity(default_severity: str, state_assessment: str | None, is_safety_related: bool,
                      zone_risk_level: str | None) -> str:
    """AL-1: escalate one level if raised while the machine is UNSAFE; safety alarms in HIGH-risk zones are >= HIGH."""
    sev = default_severity
    if state_assessment == "UNSAFE":
        sev = escalate(sev)
    if is_safety_related and zone_risk_level == "HIGH":
        sev = at_least(sev, "HIGH")
    return sev


def priority_rank(severity: str, cfg: RulesConfig = DEFAULT_RULES) -> int:
    return cfg.severity_policy[severity].rank


def ack_deadline_seconds(severity: str, cfg: RulesConfig = DEFAULT_RULES) -> int:
    return cfg.severity_policy[severity].ack_deadline_seconds


def ack_deadline_at(raised_at: datetime, severity: str, cfg: RulesConfig = DEFAULT_RULES) -> datetime:
    return raised_at + timedelta(seconds=ack_deadline_seconds(severity, cfg))


def ack_delay_seconds(raised_at: datetime, acknowledged_at: datetime) -> float:
    """AL-4: delay from actual timestamps, rounded to 0.1 s. Negative = invalid (ack before alarm)."""
    return round((acknowledged_at - raised_at).total_seconds(), 1)


def ack_status(acknowledged_at: datetime, deadline_at: datetime) -> str:
    return "ON_TIME" if acknowledged_at <= deadline_at else "LATE"


def is_missed(has_ack: bool, deadline_at: datetime, data_cutoff: datetime) -> bool:
    """No acknowledgement and the deadline already passed at the data cut-off."""
    return (not has_ack) and deadline_at < data_cutoff
