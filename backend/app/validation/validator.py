"""Run every validation check plus the anomaly detectors and assemble the report."""
from __future__ import annotations

from typing import Mapping, Sequence

from app.validation.anomaly_detectors import detect_all
from app.validation.checks import CHECKS
from app.validation.framework import ValidationReport


def validate(tables: Mapping[str, Sequence[Mapping]]) -> ValidationReport:
    counts = {name: len(rows) for name, rows in tables.items()}
    checks = [check(tables) for check in CHECKS]
    return ValidationReport(table_counts=counts, checks=checks, anomalies=detect_all(tables))
