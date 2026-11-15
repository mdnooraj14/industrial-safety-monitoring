"""Schemas for events: machine state, alarms, acknowledgements."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from app.models import enums as E
from app.schemas.common import ORM, OptUtcDateTime, UtcDateTime
from app.schemas.master import (
    AlarmDefinitionOut, InterlockStateEventOut, MachineOut, OperatorShiftOut,
)


# ---------------------------------------------------------------- machine state
class MachineStateCreate(BaseModel):
    machine_id: str
    event_ts: UtcDateTime
    machine_state: E.MachineState
    door_state: E.DoorState
    interlock_statuses: dict[str, E.InterlockStatus] = Field(
        default_factory=dict,
        description="only the interlocks whose status changes; others keep their current status",
        examples=[{"ILK-P01-M01-02": "DISENGAGED"}])
    reason: Optional[str] = Field(default=None, max_length=500)


class MachineStateEventOut(ORM):
    machine_state_event_id: str
    machine_id: str
    sequence_no: int
    event_ts: UtcDateTime
    previous_state: Optional[E.MachineState]
    machine_state: E.MachineState
    door_state: E.DoorState
    interlock_status_summary: E.InterlockSummary
    safety_assessment: E.SafetyAssessment
    unsafe_rule_code: Optional[str]
    shift_id: Optional[str]
    transition_reason: Optional[str]


class MachineStateEventDetail(MachineStateEventOut):
    interlock_events: list[InterlockStateEventOut] = []


class MachineStatusOut(BaseModel):
    machine_id: str
    machine_name: str
    plant_id: str
    machine_type: E.MachineType
    lifecycle_status: E.MachineStatus
    machine_state: Optional[E.MachineState]
    door_state: Optional[E.DoorState]
    interlock_status_summary: Optional[E.InterlockSummary]
    safety_assessment: Optional[E.SafetyAssessment]
    unsafe_rule_code: Optional[str]
    last_event_ts: OptUtcDateTime
    state_events: int


# ---------------------------------------------------------------- alarms
class AlarmCreate(BaseModel):
    machine_id: str
    alarm_definition_id: str = Field(pattern=r"^ADEF-\d{4}$")
    raised_at: UtcDateTime
    interlock_id: Optional[str] = None
    cleared_at: OptUtcDateTime = None


class AlarmEventOut(ORM):
    alarm_event_id: str
    machine_id: str
    alarm_definition_id: str
    interlock_id: Optional[str]
    safety_zone_id: Optional[str]
    machine_state_event_id: Optional[str]
    shift_id: Optional[str]
    severity: E.Severity
    raised_at: UtcDateTime
    cleared_at: OptUtcDateTime
    ack_deadline_at: UtcDateTime
    lifecycle_status: E.AlarmLifecycle
    ingest_batch_id: str


# ---------------------------------------------------------------- acknowledgement
class AcknowledgementCreate(BaseModel):
    alarm_event_id: str
    acknowledged_at: UtcDateTime
    acknowledged_by_shift_id: Optional[str] = Field(
        default=None, description="defaults to the operator on duty for the machine at acknowledged_at")
    ack_method: E.AckMethod = E.AckMethod.HMI
    comment: Optional[str] = Field(default=None, max_length=500)


class AcknowledgementOut(ORM):
    acknowledgement_id: str
    alarm_event_id: str
    acknowledged_by_shift_id: str
    acknowledged_at: UtcDateTime
    ack_method: E.AckMethod
    ack_delay_seconds: float
    ack_status: E.AckStatus
    comment: Optional[str]


class AlarmEventDetail(AlarmEventOut):
    acknowledgement: Optional[AcknowledgementOut] = None


class IncidentRef(ORM):
    incident_id: str
    severity: str
    status: str
    link_role: str


class AlarmTrace(BaseModel):
    """Alarm -> machine state -> operator -> acknowledgement -> incident -> corrective actions."""
    alarm: AlarmEventOut
    definition: AlarmDefinitionOut
    machine: MachineOut
    machine_state: Optional[MachineStateEventDetail]
    shift: Optional[OperatorShiftOut]
    acknowledgement: Optional[AcknowledgementOut]
    ack_outcome: str = Field(description="ON_TIME, LATE, MISSED (deadline passed, no ack) or PENDING")
    incidents: list[IncidentRef]
    corrective_actions: list[dict]
