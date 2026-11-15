"""Configurable business-rule thresholds (defaults documented in docs/business_rules.md).

Override any value with an environment variable (or a line in .env loaded by the app):
  RULE_<FIELD>=value                      e.g. RULE_FLOOD_THRESHOLD=12
  RULE_ACK_DEADLINE_<SEVERITY>=seconds    e.g. RULE_ACK_DEADLINE_CRITICAL=90
  RULE_ESCALATION_<SEVERITY>=seconds
"""
import os
from dataclasses import dataclass, field, fields, replace
from typing import Mapping


@dataclass(frozen=True)
class SeverityPolicy:
    rank: int
    ack_deadline_seconds: int
    escalation_after_seconds: int


def _default_policy() -> dict[str, SeverityPolicy]:
    return {
        "CRITICAL": SeverityPolicy(1, 120, 300),
        "HIGH": SeverityPolicy(2, 600, 1800),
        "MEDIUM": SeverityPolicy(3, 1800, 5400),
        "LOW": SeverityPolicy(4, 7200, 14400),
    }


@dataclass(frozen=True)
class RulesConfig:
    severity_policy: dict[str, SeverityPolicy] = field(default_factory=_default_policy)
    # AL-5 alarm flood
    flood_threshold: int = 10
    flood_window_seconds: int = 600
    flood_end_below: int = 5
    # AL-6 nuisance
    chatter_count: int = 5
    chatter_window_seconds: int = 1800
    chatter_clear_within_seconds: int = 120
    repeating_per_day: int = 20
    # AL-7 repeated alarm pattern
    repeated_alarm_count: int = 3
    repeated_alarm_window_seconds: int = 86400
    # IN-1 incident creation
    incident_min_unsafe_seconds: int = 10
    incident_lookback_seconds: int = 60
    # IN-5 repeated unsafe condition
    repeated_unsafe_count: int = 3
    repeated_unsafe_window_days: int = 7

    def validate(self) -> "RulesConfig":
        pol = self.severity_policy
        ranked = sorted(pol.values(), key=lambda p: p.rank)
        if [p.rank for p in ranked] != [1, 2, 3, 4]:
            raise ValueError("severity ranks must be 1..4")
        for a, b in zip(ranked, ranked[1:]):
            if not a.ack_deadline_seconds < b.ack_deadline_seconds:
                raise ValueError("acknowledgement deadlines must increase as severity decreases")
        for p in ranked:
            if p.escalation_after_seconds < p.ack_deadline_seconds:
                raise ValueError("escalation must not be earlier than the acknowledgement deadline")
        if self.flood_end_below > self.flood_threshold:
            raise ValueError("flood_end_below must not exceed flood_threshold")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RulesConfig":
        env = os.environ if env is None else env
        cfg = cls()
        updates = {}
        for f in fields(cls):
            if f.name == "severity_policy":
                continue
            raw = env.get(f"RULE_{f.name.upper()}")
            if raw is not None:
                updates[f.name] = type(getattr(cfg, f.name))(raw)
        policy = {}
        for sev, p in cfg.severity_policy.items():
            policy[sev] = SeverityPolicy(
                p.rank, int(env.get(f"RULE_ACK_DEADLINE_{sev}", p.ack_deadline_seconds)),
                int(env.get(f"RULE_ESCALATION_{sev}", p.escalation_after_seconds)))
        return replace(cfg, severity_policy=policy, **updates).validate()


DEFAULT_RULES = RulesConfig()
