"""Analytics (business KPIs), validation and anomaly-detection endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.analytics.filters import Filters
from app.api.deps import DB, ERRORS, Pagination, utc
from app.core.errors import NotFoundError
from app.schemas.common import ErrorResponse, Page, make_page
from app.models import enums as E
from app.services import analytics_service, evaluation_service, validation_service
from app.validation.checks import CHECKS

router = APIRouter(responses={k: {**v, "model": ErrorResponse} for k, v in ERRORS.items()})


# ============================================================ analytics (business KPIs)
def _filters(plant_id, machine_id, severity, date_from, date_to, incident_status) -> Filters:
    return Filters(plant_id=plant_id, machine_id=machine_id, severity=severity.value if severity else None,
                   date_from=date_from, date_to=date_to, incident_status=incident_status.value if incident_status else None)


@router.get("/analytics/kpis", tags=["analytics"], summary="Business KPIs",
            description="Plant performance: alarms, acknowledgement delays, floods, nuisance clusters, unsafe conditions, incidents, "
                        "MTTR and corrective actions. BUSINESS KPIs only - detector precision/recall are under /anomalies/evaluation. "
                        "Replay duplicates and invalid acknowledgements are excluded and reported separately.")
def kpis(db: DB, plant_id: Optional[str] = None, machine_id: Optional[str] = None, severity: Optional[E.Severity] = None,
         date_from: Optional[date] = None, date_to: Optional[date] = None, incident_status: Optional[E.IncidentStatus] = None,
         cutoff: Optional[datetime] = Query(None, description="snapshot time; default: latest event rounded up to the hour")) -> dict[str, Any]:
    return analytics_service.business_kpis(db, _filters(plant_id, machine_id, severity, date_from, date_to, incident_status), utc(cutoff))


@router.get("/analytics/kpis/{section}", tags=["analytics"], summary="One section of the business KPIs",
            description="Sections: " + ", ".join(analytics_service.KPI_SECTIONS))
def kpi_section(section: str, db: DB, plant_id: Optional[str] = None, machine_id: Optional[str] = None,
                severity: Optional[E.Severity] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                incident_status: Optional[E.IncidentStatus] = None, cutoff: Optional[datetime] = None) -> Any:
    if section not in analytics_service.KPI_SECTIONS:
        raise NotFoundError(f"unknown KPI section {section!r}; use one of {analytics_service.KPI_SECTIONS}")
    data = analytics_service.business_kpis(db, _filters(plant_id, machine_id, severity, date_from, date_to, incident_status), utc(cutoff))
    return data[section]


# ============================================================ validation
@router.get("/validation/checks", tags=["validation"], summary="List the validation checks")
def validation_checks() -> list[dict]:
    return [{"check_id": c.__name__[:3].upper(), "name": c.__name__[4:].replace("_", " "), "description": (c.__doc__ or "")}
            for c in CHECKS]


@router.get("/validation/report", tags=["validation"], summary="Run all validations on the database",
            description="Totals, passed/failed validations, anomalies detected, error descriptions and affected record ids. "
                        "Each failed validation lists up to `max_errors` errors unless `full=true`.")
def validation_report(db: DB, full: bool = False, max_errors: int = Query(20, ge=1, le=1000)) -> dict[str, Any]:
    rep = validation_service.run_validation(db).to_dict()
    if not full:
        for c in rep["checks"]:
            c["errors_truncated"] = max(0, len(c["errors"]) - max_errors)
            c["errors"] = c["errors"][:max_errors]
            c["affected_record_ids"] = c["affected_record_ids"][:max_errors]
        rep["anomalies"] = rep["anomalies"][:max_errors]
    return rep


# ============================================================ anomaly detection
class FindingOut(BaseModel):
    issue_type: str
    record_table: str
    record_id: str
    reason: str


@router.get("/anomalies", response_model=Page[FindingOut], tags=["anomaly detection"],
            summary="Anomalies and patterns found by the detectors",
            description="`kind=anomaly`: the five rule detectors (ack before alarm, impossible interlock state, incident without "
                        "context, replay duplicate, action closed unverified). `kind=pattern`: flood / nuisance cluster / repeated "
                        "unsafe condition. Detectors never read ground truth.")
def anomalies(db: DB, pg: Pagination, kind: str = Query("anomaly", pattern="^(anomaly|pattern)$"),
              issue_type: Optional[str] = None, record_table: Optional[str] = None):
    found = (validation_service.anomaly_findings(db) if kind == "anomaly" else validation_service.pattern_findings(db))
    if issue_type:
        found = [f for f in found if f.issue_type == issue_type]
    if record_table:
        found = [f for f in found if f.record_table == record_table]
    total = len(found)
    lo = (pg.page - 1) * pg.page_size
    items = [FindingOut(issue_type=f.issue_type, record_table=f.record_table, record_id=f.record_id, reason=f.reason)
             for f in found[lo:lo + pg.page_size]]
    return make_page(items, total, pg.page, pg.page_size)


@router.get("/anomalies/summary", tags=["anomaly detection"], summary="Counts of findings by type",
            description="Anomaly categories with counts; no ground truth involved.")
def anomaly_summary(db: DB) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind, found in (("anomaly", validation_service.anomaly_findings(db)), ("pattern", validation_service.pattern_findings(db))):
        counts: dict[str, int] = {}
        for f in found:
            counts[f.issue_type] = counts.get(f.issue_type, 0) + 1
        out[kind] = {"total": len(found), "by_issue_type": dict(sorted(counts.items()))}
    return out


@router.get("/anomalies/evaluation", tags=["anomaly detection"],
            summary="DETECTION EVALUATION: detectors vs the separate ground truth",
            description="TP / FP / FN, precision, recall, F1 and confusion matrices. This is the only endpoint that reads the ground-truth "
                        "schema; it is a measure of the detectors, not a business KPI.")
def evaluation(db: DB) -> dict[str, Any]:
    return {k: v.to_dict() for k, v in evaluation_service.evaluate_database(db).items()}
