"""Master / reference data: Plant, Machine, SafetyZone, Interlock, AlarmDefinition, OperatorShift + bridges."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, SmallInteger,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, effective_check, in_enum
from .enums import (
    AlarmDefStatus, Criticality, IndustryType, InterlockLifecycle, InterlockType, MachineStatus,
    MachineType, OperatorRole, PlantStatus, RiskLevel, Severity, ShiftCode, ShiftStatus,
    TriggerType, ZoneStatus, ZoneType,
)

TS = DateTime(timezone=True)


def _created():
    return mapped_column(TS, server_default=func.now(), nullable=False)


class Plant(Base):
    __tablename__ = "plant"
    __table_args__ = (
        CheckConstraint("plant_id ~ '^P[0-9]{2}$'", name="id_format"),
        in_enum("industry_type", IndustryType), in_enum("lifecycle_status", PlantStatus),
        CheckConstraint("country_code = upper(country_code)", name="country_upper"),
        CheckConstraint("length(trim(plant_name)) > 0", name="name_not_blank"),
        effective_check(),
    )
    plant_id: Mapped[str] = mapped_column(String(3), primary_key=True)
    plant_name: Mapped[str] = mapped_column(String(80), unique=True)
    city: Mapped[str] = mapped_column(String(60))
    country_code: Mapped[str] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(40))
    industry_type: Mapped[str] = mapped_column(String(24))
    lifecycle_status: Mapped[str] = mapped_column(String(16))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()

    machines: Mapped[list[Machine]] = relationship(back_populates="plant")
    safety_zones: Mapped[list[SafetyZone]] = relationship(back_populates="plant")
    shifts: Mapped[list[OperatorShift]] = relationship(back_populates="plant")


class Machine(Base):
    __tablename__ = "machine"
    __table_args__ = (
        CheckConstraint("machine_id ~ ('^' || plant_id || '-M[0-9]{2,3}$')", name="id_format"),
        UniqueConstraint("plant_id", "machine_name"),
        in_enum("machine_type", MachineType), in_enum("criticality_class", Criticality),
        in_enum("lifecycle_status", MachineStatus),
        CheckConstraint("commissioned_on <= effective_from", name="commissioned_before_effective"),
        effective_check(),
        Index("ix_machine_plant_id", "plant_id"),
    )
    machine_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("plant.plant_id"))
    machine_name: Mapped[str] = mapped_column(String(80))
    machine_type: Mapped[str] = mapped_column(String(24))
    criticality_class: Mapped[str] = mapped_column(String(1))
    lifecycle_status: Mapped[str] = mapped_column(String(16))
    commissioned_on: Mapped[date] = mapped_column(Date)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()

    plant: Mapped[Plant] = relationship(back_populates="machines")
    interlocks: Mapped[list[Interlock]] = relationship(back_populates="machine")
    zone_links: Mapped[list[MachineSafetyZone]] = relationship(back_populates="machine")
    state_events: Mapped[list["MachineStateEvent"]] = relationship(back_populates="machine")  # noqa: F821
    alarm_events: Mapped[list["AlarmEvent"]] = relationship(back_populates="machine")  # noqa: F821
    incidents: Mapped[list["Incident"]] = relationship(back_populates="machine")  # noqa: F821


class SafetyZone(Base):
    __tablename__ = "safety_zone"
    __table_args__ = (
        CheckConstraint("safety_zone_id ~ ('^SAFE-' || plant_id || '-[0-9]{3}$')", name="id_format"),
        UniqueConstraint("plant_id", "zone_name"),
        in_enum("zone_type", ZoneType), in_enum("risk_level", RiskLevel),
        in_enum("lifecycle_status", ZoneStatus), effective_check(),
        Index("ix_safety_zone_plant_id", "plant_id"),
    )
    safety_zone_id: Mapped[str] = mapped_column(String(13), primary_key=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("plant.plant_id"))
    zone_name: Mapped[str] = mapped_column(String(80))
    zone_type: Mapped[str] = mapped_column(String(24))
    risk_level: Mapped[str] = mapped_column(String(8))
    lifecycle_status: Mapped[str] = mapped_column(String(16))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()

    plant: Mapped[Plant] = relationship(back_populates="safety_zones")
    machine_links: Mapped[list[MachineSafetyZone]] = relationship(back_populates="safety_zone")
    interlocks: Mapped[list[Interlock]] = relationship(back_populates="safety_zone")


class MachineSafetyZone(Base):
    """Bridge: Machine N:M SafetyZone, effective-dated."""
    __tablename__ = "machine_safety_zone"
    __table_args__ = (effective_check(), Index("ix_machine_safety_zone_safety_zone_id", "safety_zone_id"))
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), primary_key=True)
    safety_zone_id: Mapped[str] = mapped_column(ForeignKey("safety_zone.safety_zone_id"), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()

    machine: Mapped[Machine] = relationship(back_populates="zone_links")
    safety_zone: Mapped[SafetyZone] = relationship(back_populates="machine_links")


class Interlock(Base):
    __tablename__ = "interlock"
    __table_args__ = (
        CheckConstraint("interlock_id ~ ('^ILK-' || machine_id || '-[0-9]{2}$')", name="id_format"),
        in_enum("interlock_type", InterlockType), in_enum("lifecycle_status", InterlockLifecycle),
        CheckConstraint("interlock_type <> 'E_STOP' OR bypass_allowed = false", name="estop_no_bypass"),
        effective_check(),
        Index("ix_interlock_machine_id", "machine_id"),
        Index("ix_interlock_safety_zone_id", "safety_zone_id"),
    )
    interlock_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"))
    safety_zone_id: Mapped[str | None] = mapped_column(ForeignKey("safety_zone.safety_zone_id"))
    interlock_type: Mapped[str] = mapped_column(String(16))
    is_required: Mapped[bool] = mapped_column(Boolean)
    bypass_allowed: Mapped[bool] = mapped_column(Boolean)
    lifecycle_status: Mapped[str] = mapped_column(String(16))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()

    machine: Mapped[Machine] = relationship(back_populates="interlocks")
    safety_zone: Mapped[SafetyZone | None] = relationship(back_populates="interlocks")
    state_events: Mapped[list["InterlockStateEvent"]] = relationship(back_populates="interlock")  # noqa: F821


class AlarmSeverityPolicy(Base):
    """Configurable severity → priority and acknowledgement-deadline rules (AL-2)."""
    __tablename__ = "alarm_severity_policy"
    __table_args__ = (
        in_enum("severity", Severity),
        CheckConstraint("priority_rank BETWEEN 1 AND 4", name="rank_range"),
        CheckConstraint("ack_deadline_seconds > 0", name="deadline_positive"),
        CheckConstraint("escalation_after_seconds >= ack_deadline_seconds", name="escalation_after_deadline"),
        effective_check(),
    )
    severity: Mapped[str] = mapped_column(String(8), primary_key=True)
    priority_rank: Mapped[int] = mapped_column(SmallInteger, unique=True)
    ack_deadline_seconds: Mapped[int] = mapped_column(Integer)
    escalation_after_seconds: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()


class AlarmDefinition(Base):
    __tablename__ = "alarm_definition"
    __table_args__ = (
        CheckConstraint("alarm_definition_id ~ '^ADEF-[0-9]{4}$'", name="id_format"),
        CheckConstraint("alarm_code ~ '^[A-Z0-9-]+$'", name="code_format"),
        CheckConstraint("machine_type IS NULL OR machine_type IN ("
                        + ", ".join(f"'{e.value}'" for e in MachineType) + ")", name="machine_type_enum"),
        CheckConstraint("interlock_type IS NULL OR interlock_type IN ("
                        + ", ".join(f"'{e.value}'" for e in InterlockType) + ")", name="interlock_type_enum"),
        in_enum("trigger_type", TriggerType), in_enum("lifecycle_status", AlarmDefStatus),
        CheckConstraint("trigger_type <> 'INTERLOCK_FAULT' OR interlock_type IS NOT NULL",
                        name="interlock_fault_needs_type"),
        CheckConstraint("trigger_type <> 'SAFETY_STATE' OR is_safety_related", name="safety_state_is_safety"),
        effective_check(),
    )
    alarm_definition_id: Mapped[str] = mapped_column(String(9), primary_key=True)
    alarm_code: Mapped[str] = mapped_column(String(40), unique=True)
    alarm_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    machine_type: Mapped[str | None] = mapped_column(String(24))
    trigger_type: Mapped[str] = mapped_column(String(20))
    interlock_type: Mapped[str | None] = mapped_column(String(16))
    default_severity: Mapped[str] = mapped_column(ForeignKey("alarm_severity_policy.severity"))
    is_safety_related: Mapped[bool] = mapped_column(Boolean)
    lifecycle_status: Mapped[str] = mapped_column(String(12))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()

    alarm_events: Mapped[list["AlarmEvent"]] = relationship(back_populates="definition")  # noqa: F821


class OperatorShift(Base):
    __tablename__ = "operator_shift"
    __table_args__ = (
        CheckConstraint("shift_id ~ ('^SHF-' || plant_id || '-[0-9]{8}-' || shift_code || '-[0-9]{2}$')",
                        name="id_format"),
        CheckConstraint("employee_id ~ '^EMP-[0-9]{5}$'", name="employee_format"),
        UniqueConstraint("plant_id", "shift_date", "shift_code", "employee_id"),
        in_enum("shift_code", ShiftCode), in_enum("operator_role", OperatorRole),
        in_enum("lifecycle_status", ShiftStatus),
        CheckConstraint("shift_end > shift_start", name="end_after_start"),
        Index("ix_operator_shift_plant_id_shift_start", "plant_id", "shift_start"),
    )
    shift_id: Mapped[str] = mapped_column(String(28), primary_key=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("plant.plant_id"))
    shift_date: Mapped[date] = mapped_column(Date)
    shift_code: Mapped[str] = mapped_column(String(1))
    shift_start: Mapped[datetime] = mapped_column(TS)
    shift_end: Mapped[datetime] = mapped_column(TS)
    employee_id: Mapped[str] = mapped_column(String(9))
    operator_name: Mapped[str] = mapped_column(String(80))
    operator_role: Mapped[str] = mapped_column(String(16))
    lifecycle_status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = _created()

    plant: Mapped[Plant] = relationship(back_populates="shifts")
    machine_assignments: Mapped[list[ShiftMachineAssignment]] = relationship(back_populates="shift")


class ShiftMachineAssignment(Base):
    """Bridge: OperatorShift N:M Machine."""
    __tablename__ = "shift_machine_assignment"
    __table_args__ = (Index("ix_shift_machine_assignment_machine_id", "machine_id"),)
    shift_id: Mapped[str] = mapped_column(ForeignKey("operator_shift.shift_id"), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machine.machine_id"), primary_key=True)
    created_at: Mapped[datetime] = _created()

    shift: Mapped[OperatorShift] = relationship(back_populates="machine_assignments")
    machine: Mapped[Machine] = relationship()
