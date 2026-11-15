"""DETECTION EVALUATION - how well detectors reproduce the separate ground truth. Not a business KPI.

Matching key: (record_table, record_id, issue_type). For each issue type:
    TP = flagged and in ground truth      FP = flagged but not in ground truth      FN = in ground truth, not flagged
    precision = TP/(TP+FP)  recall = TP/(TP+FN)  F1 = 2PR/(P+R)
A metric whose denominator is 0 is reported as None (undefined) instead of a misleading 0 or 1; macro averages use
only the defined values. The confusion matrix is per RECORD over the classes {issue types..., NONE}: rows are the actual
class (from ground truth), columns the predicted class (from the detectors); NONE/NONE counts records that are neither
labelled nor flagged when the table sizes are supplied.

This module is the ONLY place that combines findings with labels; detectors never see labels.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

NONE = "NONE"


def _safe(num: float, den: float):
    return None if den == 0 else num / den


@dataclass
class ClassMetrics:
    issue_type: str
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    support: int                      # ground-truth positives
    false_positive_ids: list[str] = field(default_factory=list)
    false_negative_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("precision", "recall", "f1"):
            d[k] = None if d[k] is None else round(d[k], 4)
        return d


@dataclass
class EvaluationResult:
    scope: str
    classes: list[ClassMetrics]
    micro: dict
    macro: dict
    labels: list[str]
    confusion: dict[str, dict[str, int]]

    def to_dict(self) -> dict:
        return {"metric_type": "detection evaluation (not a business KPI)", "scope": self.scope,
                "per_class": [c.to_dict() for c in self.classes], "micro": self.micro, "macro": self.macro,
                "confusion_matrix": {"labels": self.labels, "rows_are": "actual (ground truth)",
                                     "columns_are": "predicted (detector)", "matrix": self.confusion}}

    def by_type(self) -> dict[str, ClassMetrics]:
        return {c.issue_type: c for c in self.classes}


def _prf(tp: int, fp: int, fn: int):
    p, r = _safe(tp, tp + fp), _safe(tp, tp + fn)
    f1 = None if p is None or r is None or (p + r) == 0 else 2 * p * r / (p + r)
    return p, r, f1


def evaluate(findings: Iterable, labels: Iterable[Mapping], issue_types: Sequence[str], scope: str = "",
             universe: Mapping[str, int] | None = None) -> EvaluationResult:
    """`findings`: objects with issue_type / record_table / record_id. `labels`: ground-truth rows.

    `universe`: {table: row_count} for the tables the issue types refer to; enables the NONE/NONE confusion cell.
    """
    types = list(issue_types)
    truth = {(l["record_table"], l["record_id"], l["issue_type"]) for l in labels if l["issue_type"] in types}
    pred = {(f.record_table, f.record_id, f.issue_type) for f in findings if f.issue_type in types}
    classes = []
    for t in types:
        tr = {k for k in truth if k[2] == t}
        pr = {k for k in pred if k[2] == t}
        tp, fp, fn = len(tr & pr), len(pr - tr), len(tr - pr)
        p, r, f1 = _prf(tp, fp, fn)
        classes.append(ClassMetrics(t, tp, fp, fn, p, r, f1, len(tr), sorted(k[1] for k in pr - tr),
                                    sorted(k[1] for k in tr - pr)))
    tp, fp, fn = (sum(getattr(c, a) for c in classes) for a in ("tp", "fp", "fn"))
    mp, mr, mf = _prf(tp, fp, fn)
    defined = [c for c in classes if c.f1 is not None]

    def macro(attr):
        vals = [getattr(c, attr) for c in classes if getattr(c, attr) is not None]
        return None if not vals else round(sum(vals) / len(vals), 4)

    micro = {"tp": tp, "fp": fp, "fn": fn, "precision": None if mp is None else round(mp, 4),
             "recall": None if mr is None else round(mr, 4), "f1": None if mf is None else round(mf, 4)}
    macro_d = {"precision": macro("precision"), "recall": macro("recall"), "f1": macro("f1"),
               "classes_averaged": len(defined)}
    # per-record confusion matrix
    actual, predicted = defaultdict(set), defaultdict(set)
    for tbl, rid, t in truth:
        actual[(tbl, rid)].add(t)
    for tbl, rid, t in pred:
        predicted[(tbl, rid)].add(t)
    labels_list = types + [NONE]
    matrix = {a: {b: 0 for b in labels_list} for a in labels_list}
    for rec in set(actual) | set(predicted):
        a = sorted(actual[rec])[0] if actual[rec] else NONE
        p = sorted(predicted[rec])[0] if predicted[rec] else NONE
        matrix[a][p] += 1
    if universe is not None:
        matrix[NONE][NONE] = max(0, sum(universe.values()) - len(set(actual) | set(predicted)))
    return EvaluationResult(scope, classes, micro, macro_d, labels_list, matrix)
