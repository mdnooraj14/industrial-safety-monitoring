"""Generic repository helpers: pagination and translation of database integrity errors into domain errors."""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, DomainError, RuleViolationError


def paginate(db: Session, stmt: Select, page: int, page_size: int) -> tuple[list, int]:
    """Run `stmt` (an ORM select with filters and ordering) for one page; returns (rows, total matching)."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.limit(page_size).offset((page - 1) * page_size)))
    return rows, total


def translate(exc: IntegrityError) -> DomainError:
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
    if state == "23505":
        return ConflictError("a record with the same key already exists", "DUPLICATE")
    if state == "23503":
        return RuleViolationError("a referenced record does not exist or is still referenced", "FOREIGN_KEY")
    if state == "23514":
        return RuleViolationError(f"value violates constraint {constraint or 'check'}", "CHECK_CONSTRAINT")
    if state == "23502":
        return RuleViolationError("a required value is missing", "NOT_NULL")
    return DomainError("database integrity error", "INTEGRITY")


def flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise translate(exc) from exc


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise translate(exc) from exc
