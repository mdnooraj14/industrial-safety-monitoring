"""Ground-truth labels live in a SEPARATE schema (gt) with no foreign keys to feature tables.

Detection/analytics code must never import or query this module; only the evaluation service may.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, in_enum
from .enums import IssueType, LabelClass

GT_SCHEMA = "gt"
_INJECTED = "', '".join(
    ["ACK_BEFORE_ALARM", "IMPOSSIBLE_INTERLOCK_STATE", "INCIDENT_NO_CONTEXT",
     "REPLAY_DUPLICATE_FLOOD", "ACTION_CLOSED_UNVERIFIED"])


class GroundTruthLabel(Base):
    __tablename__ = "ground_truth_label"
    __table_args__ = (
        CheckConstraint("label_id ~ '^GT-[0-9]{6}$'", name="id_format"),
        in_enum("label_class", LabelClass), in_enum("issue_type", IssueType),
        CheckConstraint(
            f"(label_class = 'INJECTED_ANOMALY') = (issue_type IN ('{_INJECTED}'))", name="class_matches_issue"),
        Index("ix_ground_truth_label_issue_type", "issue_type"),
        Index("ix_ground_truth_label_record", "record_table", "record_id"),
        Index("ix_ground_truth_label_group_id", "group_id"),
        {"schema": GT_SCHEMA},
    )
    label_id: Mapped[str] = mapped_column(String(9), primary_key=True)
    label_class: Mapped[str] = mapped_column(String(20))
    issue_type: Mapped[str] = mapped_column(String(32))
    record_table: Mapped[str] = mapped_column(String(48))
    record_id: Mapped[str] = mapped_column(String(48))  # soft reference – intentionally NO foreign key
    group_id: Mapped[str | None] = mapped_column(String(32))
    expected_decision: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
