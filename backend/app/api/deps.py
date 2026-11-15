"""FastAPI dependencies and small helpers shared by the routers."""
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Iterator, Type

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.repositories.generic import paginate
from app.schemas.common import make_page


def get_db() -> Iterator[Session]:
    yield from get_session()


DB = Annotated[Session, Depends(get_db)]


@dataclass
class PageParams:
    page: int = Query(1, ge=1, description="1-based page number")
    page_size: int = Query(50, ge=1, le=500, description="rows per page (max 500)")


Pagination = Annotated[PageParams, Depends()]

ERRORS = {404: {"description": "record not found"}, 409: {"description": "conflict (duplicate or invalid state change)"},
          422: {"description": "validation or business-rule violation"}}


def page_of(db: Session, stmt: Select, pg: PageParams, schema: Type[BaseModel]) -> dict:
    rows, total = paginate(db, stmt, pg.page, pg.page_size)
    return make_page([schema.model_validate(r) for r in rows], total, pg.page, pg.page_size)


def utc(ts: datetime | None) -> datetime | None:
    """Treat naive query datetimes as UTC."""
    from datetime import timezone
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=timezone.utc)
