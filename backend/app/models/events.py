"""Event / transaction tables: machine & interlock state history, alarms, acknowledgements."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, in_enum
from .enums import (
    AckMethod, AckStatus, AlarmLifecycle, DoorObserved, DoorState, InterlockStatus,
    InterlockSummary, MachineState, SafetyAssessment, Severity,
)
from .master import Interlock, Machine, AlarmDefinition

TS = DateTime(timezone=True)


def _created():
    return mapped_column(TS, server_default=func.now(), nullable=False)


_STATES = ", ".join(f"'{e.value}'" for e in MachineState)


class MachineStateEvent(Base):
    """Append-only machine state history (never updated or deleted)."""
    __tablename__ = "machine_state_event"
    __table_args__ = (
        CheckConstraint("machine_state_event_id ~ ('^MSE-' || machine_id || '-[0-9]{6}$')", name="id_format"),
        UniqueConstraint("machine_id", "sequence_no"),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint(f"previous_state IS NULL OR previous_state IN ({_STATES})", name="previous_state_enum"),
        in_enum("machine_state", MachineState), in_enum("door_state", DoorState),
        in_enum("interlock_status_summary", InterlockSummary),
        in_enum("safety_assessment", SafetyAssessment),
        CheckConstraint("(safety_assessment = 'SAFE') = (unsafe_rule_code IS NULL)", name="rule_code_iff_not_safe"),
        CheckConstraint("(sequence_no = 1) = (previous_state IS NULL)", name="previous_null_iff_first"),
        Index("ix_machine_state_event_machine_id_event_ts", "machine_id", "event_ts"),
        Index("ix_machine_state_event_assessment_ts", "safety_assessment", "event_ts"),
        Index("ix_machine_state_event_shift_id", "shift_id"),
    )
    machine_state_event_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_ts: Mapped[datetime] = mapped_column(TS)
    previous_state: Mapped[str | None] = mapped_column(String(12))
    machine_state: Mapped[str] = mapped_column(String(12))
    door_state: Mapped[str] = mapped_column(String(14))
    interlock_status_summary: Mapped[str] = mapped_column(String(14))
    safety_assessment: Mapped[str] = mapped_column(String(8))
    unsafe_rule_code: Mapped[str | None] = mapped_column(String(6))
    shift_id: Mapped[str | None] = mapped_column(ForeignKey("operator_shift.shift_id"))
    transition_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    machine: Mapped[Machine] = relationship(back_populates="state_events")


class InterlockStateEvent(Base):
    """Append-only per-interlock status history."""
    __tablename__ = "interlock_state_event"
    __table_args__ = (
        CheckConstraint("interlock_state_event_id ~ ('^ISE-' || interlock_id || '-[0-9]{6}$')", name="id_format"),
        UniqueConstraint("interlock_id", "sequence_no"),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        in_enum("interlock_status", InterlockStatus),
        CheckConstraint("door_state_observed IS NULL OR door_state_observed IN ('OPEN','CLOSED')",
                        name="door_observed_enum"),
        Index("ix_interlock_state_event_interlock_id_event_ts", "interlock_id", "event_ts"),
        Index("ix_interlock_state_event_machine_state_event_id", "machine_state_event_id"),
    )
    interlock_state_event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    interlock_id: Mapped[str] = mapped_column(ForeignKey("interlock.interlock_id"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_ts: Mapped[datetime] = mapped_column(TS)
    interlock_status: Mapped[str] = mapped_column(String(10))
    door_state_observed: Mapped[str | None] = mapped_column(String(6))
    machine_state_event_id: Mapped[str | None] = mapped_column(ForeignKey("machine_state_event.machine_state_event_id"))
    created_at: Mapped[datetime] = _created()

    interlock: Mapped[Interlock] = relationship(back_populates="state_events")


class AlarmEvent(Base):
    """Write-once alarm record. Acknowledgement is a separate record (0..1)."""
    __tablename__ = "alarm_event"
    __table_args__ = (
        CheckConstraint("alarm_event_id ~ ('^ALM-' || machine_id || '-[0-9]{6}$')", name="id_format"),
        in_enum("severity", Severity), in_enum("lifecycle_status", AlarmLifecycle),
        CheckConstraint("cleared_at IS NULL OR cleared_at >= raised_at", name="cleared_after_raised"),
        CheckConstraint("ack_deadline_at > raised_at", name="deadline_after_raised"),
        CheckConstraint("(lifecycle_status = 'CLEARED') = (cleared_at IS NOT NULL)", name="status_matches_cleared"),
        Index("ix_alarm_event_machine_id_raised_at", "machine_id", "raised_at"),
        Index("ix_alarm_event_alarm_definition_id", "alarm_definition_id"),
        Index("ix_alarm_event_severity_raised_at", "severity", "raised_at"),
        Index("ix_alarm_event_raised_at", "raised_at"),
        Index("ix_alarm_event_machine_state_event_id", "machine_state_event_id"),
        Index("ix_alarm_event_shift_id", "shift_id"),
        Index("ix_alarm_event_interlock_id", "interlock_id"),
        Index("ix_alarm_event_safety_zone_id", "safety_zone_id"),
        Index("ix_alarm_event_ingest_batch_id", "ingest_batch_id"),
    )
    alarm_event_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"))
    alarm_definition_id: Mapped[str] = mapped_column(ForeignKey("alarm_definition.alarm_definition_id"))
    interlock_id: Mapped[str | None] = mapped_column(ForeignKey("interlock.interlock_id"))
    safety_zone_id: Mapped[str | None] = mapped_column(ForeignKey("safety_zone.safety_zone_id"))
    machine_state_event_id: Mapped[str | None] = mapped_column(ForeignKey("machine_state_event.machine_state_event_id"))
    shift_id: Mapped[str | None] = mapped_column(ForeignKey("operator_shift.shift_id"))
    severity: Mapped[str] = mapped_column(ForeignKey("alarm_severity_policy.severity"))
    raised_at: Mapped[datetime] = mapped_column(TS)
    cleared_at: Mapped[datetime | None] = mapped_column(TS)
    ack_deadline_at: Mapped[datetime] = mapped_column(TS)
    lifecycle_status: Mapped[str] = mapped_column(String(8))
    ingest_batch_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()

    machine: Mapped[Machine] = relationship(back_populates="alarm_events")
    definition: Mapped[AlarmDefinition] = relationship(back_populates="alarm_events")
    acknowledgement: Mapped["Acknowledgement | None"] = relationship(back_populates="alarm_event", uselist=False)


class Acknowledgement(Base):
    """One-to-one (optional) with AlarmEvent; absent row = unacknowledged."""
    __tablename__ = "acknowledgement"
    __table_args__ = (
        CheckConstraint("acknowledgement_id ~ ('^ACK-' || alarm_event_id || '-[0-9]{2}$')", name="id_format"),
        in_enum("ack_method", AckMethod), in_enum("ack_status", AckStatus),
        Index("ix_acknowledgement_acknowledged_by_shift_id", "acknowledged_by_shift_id"),
        Index("ix_acknowledgement_acknowledged_at", "acknowledged_at"),
    )
    acknowledgement_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alarm_event_id: Mapped[str] = mapped_column(ForeignKey("alarm_event.alarm_event_id"), unique=True)
    acknowledged_by_shift_id: Mapped[str] = mapped_column(ForeignKey("operator_shift.shift_id"))
    acknowledged_at: Mapped[datetime] = mapped_column(TS)
    ack_method: Mapped[str] = mapped_column(String(6))
    # Derived (rule AL-4); NOT range-checked in the DB so that anomaly 1 (negative delay) can be stored and detected.
    ack_delay_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 1))
    ack_status: Mapped[str] = mapped_column(String(8))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    alarm_event: Mapped[AlarmEvent] = relationship(back_populates="acknowledgement")
