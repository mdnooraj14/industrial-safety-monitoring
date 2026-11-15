"""Validation and anomaly-detection services: run the checks / detectors on the database (cached per data version)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.business_rules.config import DEFAULT_RULES, RulesConfig
from app.repositories.bulk_repository import cached_result, cached_tables
from app.validation.anomaly_detectors import Finding, detect_all
from app.validation.framework import ValidationReport
from app.validation.pattern_detectors import detect_patterns
from app.validation.validator import validate


def run_validation(db: Session) -> ValidationReport:
    key, tables = cached_tables(db)
    return cached_result(key, ("validation",), lambda: validate(tables))


def anomaly_findings(db: Session) -> list[Finding]:
    """The five anomaly detectors (never reads ground truth)."""
    key, tables = cached_tables(db)
    return cached_result(key, ("anomalies",), lambda: detect_all(tables))


def pattern_findings(db: Session, rules: RulesConfig = DEFAULT_RULES) -> list[Finding]:
    """Flood / nuisance cluster / repeated unsafe condition detectors."""
    key, tables = cached_tables(db)
    return cached_result(key, ("patterns", id(rules)), lambda: detect_patterns(tables, rules))
