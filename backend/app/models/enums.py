"""Allowed-value enums shared by models, rules, generator and validators.

Stored as VARCHAR with CHECK constraints (simple to migrate) – values mirror data_dictionary.md.
"""
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class IndustryType(StrEnum):
    AUTOMOTIVE = "AUTOMOTIVE"
    FOOD_PROCESSING = "FOOD_PROCESSING"
    METAL_FABRICATION = "METAL_FABRICATION"
    CHEMICAL = "CHEMICAL"


class PlantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMMISSIONING = "COMMISSIONING"
    DECOMMISSIONED = "DECOMMISSIONED"


class MachineType(StrEnum):
    CNC_MILL = "CNC_MILL"
    HYDRAULIC_PRESS = "HYDRAULIC_PRESS"
    CONVEYOR = "CONVEYOR"
    ROBOT_CELL = "ROBOT_CELL"
    PACKAGING_LINE = "PACKAGING_LINE"
    INJECTION_MOLDER = "INJECTION_MOLDER"
    WELDING_STATION = "WELDING_STATION"


class Criticality(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class MachineStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    RETIRED = "RETIRED"


class ZoneType(StrEnum):
    GUARDED_CELL = "GUARDED_CELL"
    LIGHT_CURTAIN_AREA = "LIGHT_CURTAIN_AREA"
    PERIMETER = "PERIMETER"
    PRESSURE_MAT_AREA = "PRESSURE_MAT_AREA"


class RiskLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ZoneStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class InterlockType(StrEnum):
    DOOR_SWITCH = "DOOR_SWITCH"
    GUARD_LOCK = "GUARD_LOCK"
    E_STOP = "E_STOP"
    LIGHT_CURTAIN = "LIGHT_CURTAIN"
    PRESSURE_MAT = "PRESSURE_MAT"


class InterlockLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class TriggerType(StrEnum):
    SAFETY_STATE = "SAFETY_STATE"
    INTERLOCK_FAULT = "INTERLOCK_FAULT"
    PROCESS_LIMIT = "PROCESS_LIMIT"
    COMMUNICATION = "COMMUNICATION"
    MAINTENANCE_DUE = "MAINTENANCE_DUE"


class AlarmDefStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class ShiftCode(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class OperatorRole(StrEnum):
    OPERATOR = "OPERATOR"
    SHIFT_SUPERVISOR = "SHIFT_SUPERVISOR"


class ShiftStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MachineState(StrEnum):
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    STOPPED = "STOPPED"
    E_STOPPED = "E_STOPPED"
    FAULT = "FAULT"
    MAINTENANCE = "MAINTENANCE"


class DoorState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DoorObserved(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class InterlockSummary(StrEnum):
    ALL_ENGAGED = "ALL_ENGAGED"
    PARTIAL = "PARTIAL"
    NONE_ENGAGED = "NONE_ENGAGED"


class SafetyAssessment(StrEnum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    INVALID = "INVALID"


class InterlockStatus(StrEnum):
    ENGAGED = "ENGAGED"
    DISENGAGED = "DISENGAGED"
    BYPASSED = "BYPASSED"
    FAULT = "FAULT"


class AlarmLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    CLEARED = "CLEARED"


class AckMethod(StrEnum):
    HMI = "HMI"
    SCADA = "SCADA"
    MOBILE = "MOBILE"


class AckStatus(StrEnum):
    ON_TIME = "ON_TIME"
    LATE = "LATE"


class IncidentSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    ACTION_IN_PROGRESS = "ACTION_IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class LinkRole(StrEnum):
    TRIGGER = "TRIGGER"
    CONTRIBUTING = "CONTRIBUTING"


class ChangedByRole(StrEnum):
    OPERATOR = "OPERATOR"
    ENGINEER = "ENGINEER"
    SUPERVISOR = "SUPERVISOR"
    SYSTEM = "SYSTEM"


class ActionType(StrEnum):
    REPLACE_INTERLOCK = "REPLACE_INTERLOCK"
    REPAIR_DOOR_SWITCH = "REPAIR_DOOR_SWITCH"
    ADJUST_ALARM_LIMIT = "ADJUST_ALARM_LIMIT"
    OPERATOR_TRAINING = "OPERATOR_TRAINING"
    REVISE_PROCEDURE = "REVISE_PROCEDURE"
    INSTALL_GUARD = "INSTALL_GUARD"


class ActionStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class VerificationResult(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class LabelClass(StrEnum):
    INJECTED_ANOMALY = "INJECTED_ANOMALY"
    BASELINE_SCENARIO = "BASELINE_SCENARIO"


class IssueType(StrEnum):
    ACK_BEFORE_ALARM = "ACK_BEFORE_ALARM"
    IMPOSSIBLE_INTERLOCK_STATE = "IMPOSSIBLE_INTERLOCK_STATE"
    INCIDENT_NO_CONTEXT = "INCIDENT_NO_CONTEXT"
    REPLAY_DUPLICATE_FLOOD = "REPLAY_DUPLICATE_FLOOD"
    ACTION_CLOSED_UNVERIFIED = "ACTION_CLOSED_UNVERIFIED"
    ALARM_FLOOD = "ALARM_FLOOD"
    NUISANCE_CLUSTER = "NUISANCE_CLUSTER"
    REPEATED_UNSAFE_CONDITION = "REPEATED_UNSAFE_CONDITION"
