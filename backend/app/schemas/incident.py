"""Schemas for incidents and corrective actions."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models import enums as E
from app.schemas.common import ORM, OptUtcDateTime, UtcDateTime
from app.schemas.events import AlarmEventOut, MachineStateEventDetail
from app.schemas.master import OperatorShiftOut


# ---------------------------------------------------------------- incident
class IncidentCreate(BaseModel):
    """Rule IN-1: an incident is created from an UNSAFE machine-state event that has HIGH/CRITICAL alarm context."""
    trigger_machine_state_event_id: str = Field(examples=["MSE-P01-M01-000123"])
    title: Optional[str] = Field(default=None, max_length=160)
    description: Optional[str] = Field(default=None, max_length=2000)


class IncidentOut(ORM):
    incident_id: str
    machine_id: str
    safety_zone_id: Optional[str]
    trigger_machine_state_event_id: Optional[str]
    trigger_rule_code: Optional[str]
    shift_id: Optional[str]
    severity: E.IncidentSeverity
    status: E.IncidentStatus
    title: str
    description: Optional[str]
    opened_at: UtcDateTime
    investigation_started_at: OptUtcDateTime
    resolved_at: OptUtcDateTime
    closed_at: OptUtcDateTime


class IncidentStatusEventOut(ORM):
    incident_status_event_id: str
    incident_id: str
    sequence_no: int
    from_status: Optional[E.IncidentStatus]
    to_status: E.IncidentStatus
    changed_at: UtcDateTime
    changed_by_role: E.ChangedByRole
    note: Optional[str]


class IncidentTransition(BaseModel):
    to_status: E.IncidentStatus
    changed_by_role: E.ChangedByRole = E.ChangedByRole.ENGINEER
    changed_at: OptUtcDateTime = Field(default=None, description="defaults to now (UTC)")
    note: Optional[str] = Field(default=None, max_length=500)


class IncidentAlarmOut(BaseModel):
    link_role: E.LinkRole
    linked_at: UtcDateTime
    alarm: AlarmEventOut


# ---------------------------------------------------------------- corrective action
class CorrectiveActionCreate(BaseModel):
    assigned_engineer_id: str = Field(pattern=r"^EMP-\d{5}$", examples=["EMP-20001"])
    assigned_engineer_name: str = Field(min_length=1, max_length=80)
    action_type: E.ActionType
    action_description: str = Field(min_length=3, max_length=2000)
    planned_at: OptUtcDateTime = Field(default=None, description="defaults to now (UTC)")
    due_at: UtcDateTime

    @model_validator(mode="after")
    def _due(self):
        if self.planned_at and self.due_at <= self.planned_at:
            raise ValueError("due_at must be after planned_at")
        return self


class CorrectiveActionOut(ORM):
    corrective_action_id: str
    incident_id: str
    assigned_engineer_id: str
    assigned_engineer_name: str
    action_type: E.ActionType
    action_description: str
    status: E.ActionStatus
    planned_at: UtcDateTime
    due_at: UtcDateTime
    started_at: OptUtcDateTime
    completed_at: OptUtcDateTime
    verified_at: OptUtcDateTime
    verified_by_id: Optional[str]
    verification_result: Optional[E.VerificationResult]
    closed_at: OptUtcDateTime


class CorrectiveActionStatusEventOut(ORM):
    action_status_event_id: str
    corrective_action_id: str
    sequence_no: int
    from_status: Optional[E.ActionStatus]
    to_status: E.ActionStatus
    changed_at: UtcDateTime
    note: Optional[str]


class ActionTransition(BaseModel):
    to_status: E.ActionStatus
    changed_at: OptUtcDateTime = Field(default=None, description="defaults to now (UTC)")
    verification_result: Optional[E.VerificationResult] = Field(
        default=None, description="required when moving to VERIFIED")
    verified_by_id: Optional[str] = Field(default=None, pattern=r"^EMP-\d{5}$",
                                          description="required when moving to VERIFIED; must differ from the assignee")
    note: Optional[str] = Field(default=None, max_length=500)


class CorrectiveActionDetail(CorrectiveActionOut):
    history: list[CorrectiveActionStatusEventOut] = []


class IncidentDetail(BaseModel):
    """Traceability: incident -> trigger machine state -> alarms -> operator -> lifecycle -> corrective actions."""
    incident: IncidentOut
    trigger_state: Optional[MachineStateEventDetail]
    alarms: list[IncidentAlarmOut]
    shift: Optional[OperatorShiftOut]
    status_history: list[IncidentStatusEventOut]
    corrective_actions: list[CorrectiveActionDetail]
    context_complete: bool = Field(description="false when the incident lacks a trigger alarm or trigger machine state")
