"""Incident, its alarm bridge, lifecycle history, corrective actions and their history.

NOTE: rule CA-3 (CLOSED requires verification) and acknowledgement/incident context rules are deliberately NOT
database CHECKs: the injected anomalies (docs/business_rules.md §5) must be storable so the validator and
detectors can find them. They are enforced by business_rules + validation instead.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, in_enum
from .enums import (
    ActionStatus, ActionType, ChangedByRole, IncidentSeverity, IncidentStatus, LinkRole,
    VerificationResult,
)
from .master import Machine

TS = DateTime(timezone=True)
_INC = ", ".join(f"'{e.value}'" for e in IncidentStatus)
_ACT = ", ".join(f"'{e.value}'" for e in ActionStatus)


def _created():
    return mapped_column(TS, server_default=func.now(), nullable=False)


class Incident(Base):
    __tablename__ = "incident"
    __table_args__ = (
        CheckConstraint("incident_id ~ '^INC-[0-9]{4}-[0-9]{6}$'", name="id_format"),
        CheckConstraint("split_part(incident_id, '-', 2)::int = EXTRACT(YEAR FROM opened_at AT TIME ZONE 'UTC')",
                        name="year_matches_opened"),
        in_enum("severity", IncidentSeverity), in_enum("status", IncidentStatus),
        CheckConstraint("trigger_rule_code IS NULL OR trigger_rule_code IN ('R-U1','R-U2','R-U3')",
                        name="trigger_rule_enum"),
        CheckConstraint("investigation_started_at IS NULL OR investigation_started_at >= opened_at", name="inv_after_open"),
        CheckConstraint("resolved_at IS NULL OR resolved_at >= COALESCE(investigation_started_at, opened_at)",
                        name="resolved_after_inv"),
        CheckConstraint("closed_at IS NULL OR closed_at >= COALESCE(resolved_at, opened_at)", name="closed_after_resolved"),
        Index("ix_incident_machine_id", "machine_id"),
        Index("ix_incident_status", "status"),
        Index("ix_incident_opened_at", "opened_at"),
        Index("ix_incident_severity_opened_at", "severity", "opened_at"),
        Index("ix_incident_trigger_machine_state_event_id", "trigger_machine_state_event_id"),
        Index("ix_incident_shift_id", "shift_id"),
        Index("ix_incident_safety_zone_id", "safety_zone_id"),
    )
    incident_id: Mapped[str] = mapped_column(String(15), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"))
    safety_zone_id: Mapped[str | None] = mapped_column(ForeignKey("safety_zone.safety_zone_id"))
    trigger_machine_state_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("machine_state_event.machine_state_event_id"))
    trigger_rule_code: Mapped[str | None] = mapped_column(String(6))
    shift_id: Mapped[str | None] = mapped_column(ForeignKey("operator_shift.shift_id"))
    severity: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(TS)
    investigation_started_at: Mapped[datetime | None] = mapped_column(TS)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()

    machine: Mapped[Machine] = relationship(back_populates="incidents")
    alarm_links: Mapped[list[IncidentAlarmLink]] = relationship(back_populates="incident")
    status_events: Mapped[list[IncidentStatusEvent]] = relationship(
        back_populates="incident", order_by="IncidentStatusEvent.sequence_no")
    corrective_actions: Mapped[list[CorrectiveAction]] = relationship(back_populates="incident")


class IncidentAlarmLink(Base):
    """Bridge: Incident N:M AlarmEvent."""
    __tablename__ = "incident_alarm_link"
    __table_args__ = (
        in_enum("link_role", LinkRole),
        Index("ix_incident_alarm_link_alarm_event_id", "alarm_event_id"),
    )
    incident_id: Mapped[str] = mapped_column(ForeignKey("incident.incident_id"), primary_key=True)
    alarm_event_id: Mapped[str] = mapped_column(ForeignKey("alarm_event.alarm_event_id"), primary_key=True)
    link_role: Mapped[str] = mapped_column(String(12))
    linked_at: Mapped[datetime] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()

    incident: Mapped[Incident] = relationship(back_populates="alarm_links")
    alarm_event: Mapped["AlarmEvent"] = relationship()  # noqa: F821


class IncidentStatusEvent(Base):
    """Append-only incident lifecycle history."""
    __tablename__ = "incident_status_event"
    __table_args__ = (
        CheckConstraint("incident_status_event_id ~ ('^ISTAT-' || incident_id || '-[0-9]{2}$')", name="id_format"),
        UniqueConstraint("incident_id", "sequence_no"),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint(f"from_status IS NULL OR from_status IN ({_INC})", name="from_status_enum"),
        in_enum("to_status", IncidentStatus), in_enum("changed_by_role", ChangedByRole),
        CheckConstraint("(sequence_no = 1) = (from_status IS NULL)", name="from_null_iff_first"),
        Index("ix_incident_status_event_incident_id_changed_at", "incident_id", "changed_at"),
    )
    incident_status_event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incident.incident_id"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    changed_at: Mapped[datetime] = mapped_column(TS)
    changed_by_role: Mapped[str] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    incident: Mapped[Incident] = relationship(back_populates="status_events")


class CorrectiveAction(Base):
    __tablename__ = "corrective_action"
    __table_args__ = (
        CheckConstraint("corrective_action_id ~ ('^ACT-' || incident_id || '-[0-9]{2}$')", name="id_format"),
        CheckConstraint("assigned_engineer_id ~ '^EMP-[0-9]{5}$'", name="engineer_format"),
        CheckConstraint("verified_by_id IS NULL OR verified_by_id ~ '^EMP-[0-9]{5}$'", name="verifier_format"),
        in_enum("action_type", ActionType), in_enum("status", ActionStatus),
        CheckConstraint("verification_result IS NULL OR verification_result IN ('PASSED','FAILED')",
                        name="verification_enum"),
        CheckConstraint("due_at > planned_at", name="due_after_planned"),
        CheckConstraint("started_at IS NULL OR started_at >= planned_at", name="started_after_planned"),
        CheckConstraint("completed_at IS NULL OR completed_at >= COALESCE(started_at, planned_at)",
                        name="completed_after_started"),
        CheckConstraint("verified_at IS NULL OR verified_at >= COALESCE(completed_at, planned_at)",
                        name="verified_after_completed"),
        CheckConstraint("closed_at IS NULL OR closed_at >= COALESCE(verified_at, completed_at, planned_at)",
                        name="closed_after_verified"),
        CheckConstraint("(verified_at IS NULL) = (verification_result IS NULL)", name="verification_pair"),
        CheckConstraint("verified_by_id IS NULL OR verified_by_id <> assigned_engineer_id", name="independent_verifier"),
        Index("ix_corrective_action_incident_id", "incident_id"),
        Index("ix_corrective_action_status", "status"),
        Index("ix_corrective_action_assigned_engineer_id", "assigned_engineer_id"),
    )
    corrective_action_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incident.incident_id"))
    assigned_engineer_id: Mapped[str] = mapped_column(String(9))
    assigned_engineer_name: Mapped[str] = mapped_column(String(80))
    action_type: Mapped[str] = mapped_column(String(24))
    action_description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    planned_at: Mapped[datetime] = mapped_column(TS)
    due_at: Mapped[datetime] = mapped_column(TS)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    verified_at: Mapped[datetime | None] = mapped_column(TS)
    verified_by_id: Mapped[str | None] = mapped_column(String(9))
    verification_result: Mapped[str | None] = mapped_column(String(6))
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()

    incident: Mapped[Incident] = relationship(back_populates="corrective_actions")
    status_events: Mapped[list[CorrectiveActionStatusEvent]] = relationship(
        back_populates="corrective_action", order_by="CorrectiveActionStatusEvent.sequence_no")


class CorrectiveActionStatusEvent(Base):
    """Append-only corrective action history."""
    __tablename__ = "corrective_action_status_event"
    __table_args__ = (
        CheckConstraint("action_status_event_id ~ ('^ASTAT-' || corrective_action_id || '-[0-9]{2}$')", name="id_format"),
        UniqueConstraint("corrective_action_id", "sequence_no"),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint(f"from_status IS NULL OR from_status IN ({_ACT})", name="from_status_enum"),
        in_enum("to_status", ActionStatus),
        Index("ix_corrective_action_status_event_action_id_changed_at", "corrective_action_id", "changed_at"),
    )
    action_status_event_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    corrective_action_id: Mapped[str] = mapped_column(ForeignKey("corrective_action.corrective_action_id"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    changed_at: Mapped[datetime] = mapped_column(TS)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    corrective_action: Mapped[CorrectiveAction] = relationship(back_populates="status_events")
