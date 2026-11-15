"""Evaluation service: run the detectors on the feature tables and score them against the separate ground truth."""
from __future__ import annotations

from typing import Mapping, Sequence

from sqlalchemy.orm import Session

from app.analytics.evaluation import EvaluationResult, evaluate
from app.business_rules.config import DEFAULT_RULES, RulesConfig
from app.repositories.bulk_repository import cached_tables
from app.repositories.ground_truth_repository import load_labels
from app.validation.anomaly_detectors import ISSUE_TYPES, detect_all
from app.validation.pattern_detectors import PATTERN_TYPES, detect_patterns

# tables each scope's records live in (for the NONE/NONE confusion cell)
ANOMALY_TABLES = ["alarm_event", "acknowledgement", "machine_state_event", "incident", "corrective_action"]
PATTERN_TABLES = ["alarm_event", "machine_state_event"]


def evaluate_findings(tables: Mapping[str, Sequence[Mapping]], labels: Sequence[Mapping], anomaly_findings,
                      pattern_findings) -> dict[str, EvaluationResult]:
    """Score already-computed findings against the labels (the only place findings and labels meet)."""
    anomalies = evaluate(anomaly_findings, labels, ISSUE_TYPES, "injected anomalies (5 detectors)",
                         {t: len(tables[t]) for t in ANOMALY_TABLES})
    patterns = evaluate(pattern_findings, labels, PATTERN_TYPES, "baseline scenarios (pattern detectors)",
                        {t: len(tables[t]) for t in PATTERN_TABLES})
    return {"anomalies": anomalies, "patterns": patterns}


def evaluate_tables(tables: Mapping[str, Sequence[Mapping]], labels: Sequence[Mapping],
                    rules: RulesConfig = DEFAULT_RULES) -> dict[str, EvaluationResult]:
    """Two scopes: the five injected anomalies, and the three baseline scenarios (floods, nuisance, repeated unsafe)."""
    return evaluate_findings(tables, labels, detect_all(tables), detect_patterns(tables, rules))


def evaluate_database(db: Session, rules: RulesConfig = DEFAULT_RULES) -> dict[str, EvaluationResult]:
    from app.services.validation_service import anomaly_findings, pattern_findings
    _, tables = cached_tables(db)
    return evaluate_findings(tables, load_labels(db), anomaly_findings(db), pattern_findings(db, rules))
