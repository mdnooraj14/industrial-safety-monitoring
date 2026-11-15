"""Schemas for master data: Plant, Machine, SafetyZone, Interlock, AlarmDefinition, OperatorShift."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models import enums as E
from app.schemas.common import ORM, OptUtcDateTime, UtcDateTime


class _Effective(BaseModel):
    @model_validator(mode="after")
    def _range(self):
        if getattr(self, "effective_to", None) and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        return self


# ---------------------------------------------------------------- plant
class PlantCreate(_Effective):
    plant_id: str = Field(pattern=r"^P\d{2}$", examples=["P05"])
    plant_name: str = Field(min_length=1, max_length=80)
    city: str = Field(max_length=60)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    timezone: str = Field(max_length=40, examples=["Asia/Kolkata"])
    industry_type: E.IndustryType
    lifecycle_status: E.PlantStatus = E.PlantStatus.ACTIVE
    effective_from: date
    effective_to: Optional[date] = None


class PlantOut(ORM):
    plant_id: str
    plant_name: str
    city: str
    country_code: str
    timezone: str
    industry_type: E.IndustryType
    lifecycle_status: E.PlantStatus
    effective_from: date
    effective_to: Optional[date]


# ---------------------------------------------------------------- machine
class MachineCreate(_Effective):
    machine_id: str = Field(pattern=r"^P\d{2}-M\d{2,3}$", examples=["P01-M16"])
    plant_id: str = Field(pattern=r"^P\d{2}$")
    machine_name: str = Field(min_length=1, max_length=80)
    machine_type: E.MachineType
    criticality_class: E.Criticality
    lifecycle_status: E.MachineStatus = E.MachineStatus.ACTIVE
    commissioned_on: date
    effective_from: date
    effective_to: Optional[date] = None

    @model_validator(mode="after")
    def _ids(self):
        if not self.machine_id.startswith(self.plant_id + "-"):
            raise ValueError("machine_id must start with its plant_id")
        if self.commissioned_on > self.effective_from:
            raise ValueError("commissioned_on must not be after effective_from")
        return self


class MachineOut(ORM):
    machine_id: str
    plant_id: str
    machine_name: str
    machine_type: E.MachineType
    criticality_class: E.Criticality
    lifecycle_status: E.MachineStatus
    commissioned_on: date
    effective_from: date
    effective_to: Optional[date]


# ---------------------------------------------------------------- safety zone
class SafetyZoneCreate(_Effective):
    safety_zone_id: str = Field(pattern=r"^SAFE-P\d{2}-\d{3}$", examples=["SAFE-P01-005"])
    plant_id: str = Field(pattern=r"^P\d{2}$")
    zone_name: str = Field(min_length=1, max_length=80)
    zone_type: E.ZoneType
    risk_level: E.RiskLevel
    lifecycle_status: E.ZoneStatus = E.ZoneStatus.ACTIVE
    effective_from: date
    effective_to: Optional[date] = None
    machine_ids: list[str] = Field(default_factory=list, description="machines to link (same plant)")

    @model_validator(mode="after")
    def _ids(self):
        if self.safety_zone_id.split("-")[1] != self.plant_id:
            raise ValueError("safety_zone_id must embed its plant_id")
        return self


class SafetyZoneOut(ORM):
    safety_zone_id: str
    plant_id: str
    zone_name: str
    zone_type: E.ZoneType
    risk_level: E.RiskLevel
    lifecycle_status: E.ZoneStatus
    effective_from: date
    effective_to: Optional[date]


class MachineZoneLinkOut(ORM):
    machine_id: str
    safety_zone_id: str
    effective_from: date
    effective_to: Optional[date]
    is_primary: bool


# ---------------------------------------------------------------- interlock
class InterlockCreate(_Effective):
    interlock_id: str = Field(pattern=r"^ILK-P\d{2}-M\d{2,3}-\d{2}$", examples=["ILK-P01-M01-05"])
    machine_id: str
    safety_zone_id: Optional[str] = None
    interlock_type: E.InterlockType
    is_required: bool
    bypass_allowed: bool = False
    lifecycle_status: E.InterlockLifecycle = E.InterlockLifecycle.ACTIVE
    effective_from: date
    effective_to: Optional[date] = None

    @model_validator(mode="after")
    def _rules(self):
        if not self.interlock_id.startswith(f"ILK-{self.machine_id}-"):
            raise ValueError("interlock_id must embed its machine_id")
        if self.interlock_type == E.InterlockType.E_STOP and self.bypass_allowed:
            raise ValueError("an E_STOP interlock can never allow bypass")
        return self


class InterlockOut(ORM):
    interlock_id: str
    machine_id: str
    safety_zone_id: Optional[str]
    interlock_type: E.InterlockType
    is_required: bool
    bypass_allowed: bool
    lifecycle_status: E.InterlockLifecycle
    effective_from: date
    effective_to: Optional[date]


class InterlockStateEventOut(ORM):
    interlock_state_event_id: str
    interlock_id: str
    sequence_no: int
    event_ts: UtcDateTime
    interlock_status: E.InterlockStatus
    door_state_observed: Optional[E.DoorObserved]
    machine_state_event_id: Optional[str]


# ---------------------------------------------------------------- alarm definition / policy
class AlarmDefinitionCreate(_Effective):
    alarm_definition_id: str = Field(pattern=r"^ADEF-\d{4}$", examples=["ADEF-0201"])
    alarm_code: str = Field(pattern=r"^[A-Z0-9-]+$", max_length=40)
    alarm_name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None
    machine_type: Optional[E.MachineType] = None
    trigger_type: E.TriggerType
    interlock_type: Optional[E.InterlockType] = None
    default_severity: E.Severity
    is_safety_related: bool
    lifecycle_status: E.AlarmDefStatus = E.AlarmDefStatus.ACTIVE
    effective_from: date
    effective_to: Optional[date] = None

    @model_validator(mode="after")
    def _rules(self):
        if self.trigger_type == E.TriggerType.INTERLOCK_FAULT and self.interlock_type is None:
            raise ValueError("INTERLOCK_FAULT definitions need an interlock_type")
        if self.trigger_type == E.TriggerType.SAFETY_STATE and not self.is_safety_related:
            raise ValueError("SAFETY_STATE definitions must be safety related")
        return self


class AlarmDefinitionOut(ORM):
    alarm_definition_id: str
    alarm_code: str
    alarm_name: str
    description: Optional[str]
    machine_type: Optional[E.MachineType]
    trigger_type: E.TriggerType
    interlock_type: Optional[E.InterlockType]
    default_severity: E.Severity
    is_safety_related: bool
    lifecycle_status: E.AlarmDefStatus
    effective_from: date
    effective_to: Optional[date]


class SeverityPolicyOut(ORM):
    severity: E.Severity
    priority_rank: int
    ack_deadline_seconds: int
    escalation_after_seconds: int
    effective_from: date
    effective_to: Optional[date]


# ---------------------------------------------------------------- operator shift
class OperatorShiftCreate(BaseModel):
    shift_id: str = Field(pattern=r"^SHF-P\d{2}-\d{8}-[ABC]-\d{2}$", examples=["SHF-P01-20261101-A-01"])
    plant_id: str
    shift_date: date
    shift_code: E.ShiftCode
    shift_start: UtcDateTime
    shift_end: UtcDateTime
    employee_id: str = Field(pattern=r"^EMP-\d{5}$")
    operator_name: str = Field(min_length=1, max_length=80)
    operator_role: E.OperatorRole = E.OperatorRole.OPERATOR
    lifecycle_status: E.ShiftStatus = E.ShiftStatus.SCHEDULED
    machine_ids: list[str] = Field(default_factory=list, description="machines this operator covers (same plant)")

    @model_validator(mode="after")
    def _rules(self):
        if self.shift_end <= self.shift_start:
            raise ValueError("shift_end must be after shift_start")
        if self.shift_id.split("-")[1] != self.plant_id or self.shift_id.split("-")[3] != self.shift_code.value:
            raise ValueError("shift_id must embed plant_id and shift_code")
        return self


class OperatorShiftOut(ORM):
    shift_id: str
    plant_id: str
    shift_date: date
    shift_code: E.ShiftCode
    shift_start: UtcDateTime
    shift_end: UtcDateTime
    employee_id: str
    operator_name: str
    operator_role: E.OperatorRole
    lifecycle_status: E.ShiftStatus


class OperatorShiftDetail(OperatorShiftOut):
    machine_ids: list[str] = []
