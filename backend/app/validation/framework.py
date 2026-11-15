"""Validation result types and report rendering."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Failure:
    record_table: str
    record_id: str
    detail: str


@dataclass
class CheckResult:
    check_id: str
    category: str
    name: str
    description: str
    records_checked: int
    failures: list[Failure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def failed_ids(self) -> list[str]:
        return sorted({f.record_id for f in self.failures})


@dataclass
class ValidationReport:
    table_counts: dict[str, int]
    checks: list[CheckResult]
    anomalies: list = field(default_factory=list)          # Finding objects from the detectors

    @property
    def total_records(self) -> int:
        return sum(self.table_counts.values())

    @property
    def passed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.passed]

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> dict:
        return {
            "total_records_checked": self.total_records,
            "total_record_evaluations": sum(c.records_checked for c in self.checks),
            "validations_run": len(self.checks),
            "validations_passed": len(self.passed_checks),
            "validations_failed": len(self.failed_checks),
            "failed_validation_ids": [c.check_id for c in self.failed_checks],
            "anomalies_detected": len(self.anomalies),
            "anomalies_by_type": dict(sorted(Counter(a.issue_type for a in self.anomalies).items())),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "table_counts": self.table_counts,
            "checks": [{
                "check_id": c.check_id, "category": c.category, "name": c.name, "description": c.description,
                "status": "PASSED" if c.passed else "FAILED", "records_checked": c.records_checked,
                "failure_count": len(c.failures), "affected_record_ids": c.failed_ids,
                "errors": [{"table": f.record_table, "record_id": f.record_id, "detail": f.detail} for f in c.failures],
            } for c in self.checks],
            "anomalies": [{"issue_type": a.issue_type, "record_table": a.record_table, "record_id": a.record_id,
                           "reason": a.reason} for a in self.anomalies],
        }

    def to_markdown(self, title: str = "Validation report", max_ids: int = 12) -> str:
        s = self.summary()
        lines = [f"## {title}", "",
                 "| Metric | Value |", "|---|---|",
                 f"| Total records checked | {s['total_records_checked']:,} |",
                 f"| Record evaluations (a record can be checked by several validations) | {s['total_record_evaluations']:,} |",
                 f"| Validations run | {s['validations_run']} |",
                 f"| Validations passed | {s['validations_passed']} |",
                 f"| Validations failed | {s['validations_failed']} |",
                 f"| Anomalies detected | {s['anomalies_detected']} |", "",
                 "### Validations", "", "| ID | Category | Validation | Status | Records checked | Failures |",
                 "|---|---|---|---|---|---|"]
        for c in self.checks:
            lines.append(f"| {c.check_id} | {c.category} | {c.name} | {'PASSED' if c.passed else '**FAILED**'} "
                         f"| {c.records_checked:,} | {len(c.failures)} |")
        if self.failed_checks:
            lines += ["", "### Errors and affected records", ""]
            for c in self.failed_checks:
                lines.append(f"**{c.check_id} - {c.name}**: {len(c.failures)} failure(s), {len(c.failed_ids)} record(s). "
                             f"{c.description}")
                lines.append("")
                for f in c.failures[:max_ids]:
                    lines.append(f"- `{f.record_id}` ({f.record_table}): {f.detail}")
                if len(c.failures) > max_ids:
                    lines.append(f"- ... and {len(c.failures) - max_ids} more (see the JSON report)")
                lines.append("")
        lines += ["### Anomalies detected", ""]
        if not self.anomalies:
            lines.append("None.")
        else:
            lines += ["| Anomaly type | Records |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in s["anomalies_by_type"].items()]
        lines += ["", "### Records per table", "", "| Table | Rows |", "|---|---|"]
        lines += [f"| {t} | {n:,} |" for t, n in self.table_counts.items()]
        return "\n".join(lines) + "\n"
