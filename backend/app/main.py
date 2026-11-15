"""FastAPI application: Industrial Safety Monitoring & Incident Analysis (Tier 2)."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import DB
from app.api.routes import events, incidents, insight, master
from app.core.errors import DomainError

log = logging.getLogger("safety.api")

DESCRIPTION = """
REST API over the safety-event database (PostgreSQL). Business rules live in `app/business_rules`; routes only validate
input and call services.

* **Errors** always use `{"error": {"code", "message", "details"}}`. `404` unknown record, `409` duplicate or illegal state
  change, `422` validation or business-rule violation, `500` unexpected.
* **Lists** are paginated (`page`, `page_size`<=500) and filterable; responses are `{items, total, page, page_size, pages}`.
* **History is append-only**: state, alarm, acknowledgement and status-history rows can be created but never changed or deleted.
* **Timestamps** are ISO-8601 UTC. Naive query timestamps are read as UTC.
* **Business KPIs** (`/analytics`) and **detector evaluation** (`/anomalies/evaluation`) are separate: only the latter reads ground truth.
"""

TAGS = [
    {"name": "plants"}, {"name": "machines"}, {"name": "safety zones"}, {"name": "interlocks"},
    {"name": "alarm definitions"}, {"name": "operator shifts"}, {"name": "machine state events"},
    {"name": "alarm events"}, {"name": "acknowledgements"}, {"name": "incidents"}, {"name": "corrective actions"},
    {"name": "analytics"}, {"name": "validation"}, {"name": "anomaly detection"}, {"name": "system"},
]


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})


def create_app() -> FastAPI:
    app = FastAPI(title="Industrial Safety Monitoring API", version="1.0.0", description=DESCRIPTION, openapi_tags=TAGS)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"], "type": e["type"]}
                   for e in exc.errors()]
        return _error(422, "VALIDATION_ERROR", "request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, "HTTP_ERROR" if exc.status_code != 404 else "NOT_FOUND", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "unexpected server error")

    @app.get("/health", tags=["system"], summary="Liveness and database connectivity")
    def health(db: DB):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "up"}

    for module in (master, events, incidents, insight):
        app.include_router(module.router, prefix="/api/v1")
    return app


app = create_app()
