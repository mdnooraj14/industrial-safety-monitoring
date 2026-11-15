"""Declarative base and column helpers."""
from datetime import datetime
from enum import Enum
from typing import Type

from sqlalchemy import CheckConstraint, DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def in_enum(column: str, enum: Type[Enum], name: str | None = None) -> CheckConstraint:
    values = ", ".join(f"'{e.value}'" for e in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_enum")


def effective_check(table_hint: str = "") -> CheckConstraint:
    return CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from", name="effective_range"
    )


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def enum_col(length: int = 24, nullable: bool = False):
    return mapped_column(String(length), nullable=nullable)
