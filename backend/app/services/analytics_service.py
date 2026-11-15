"""Analytics service: business KPIs from the database (no ground truth involved)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.analytics.filters import Filters
from app.analytics.kpis import compute_kpis
from app.business_rules.config import DEFAULT_RULES, RulesConfig
from app.repositories.bulk_repository import cached_result, cached_tables

KPI_SECTIONS = ["overview", "alarms", "acknowledgements", "floods", "nuisance", "repeated_alarms", "unsafe_conditions",
                "incidents", "corrective_actions", "alarms_per_day"]


def business_kpis(db: Session, filters: Filters | None = None, cutoff: datetime | None = None,
                  rules: RulesConfig = DEFAULT_RULES) -> dict:
    """KPIs for the current data. Results are memoised until the data changes."""
    key, tables = cached_tables(db)
    name = ("kpis", tuple(sorted((filters.as_dict() if filters else {}).items())), cutoff, id(rules))
    return cached_result(key, name, lambda: compute_kpis(tables, filters, cutoff, rules))
