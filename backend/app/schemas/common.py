"""Shared API schemas: UTC datetimes, pagination, error envelope."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

T = TypeVar("T")


def _utc(d: datetime | None) -> str | None:
    return None if d is None else d.astimezone(timezone.utc).isoformat()


# every timestamp leaves the API in UTC, whatever time zone the database session used
UtcDateTime = Annotated[datetime, PlainSerializer(_utc, return_type=str, when_used="json")]
OptUtcDateTime = Annotated[datetime | None, PlainSerializer(_utc, return_type=str | None, when_used="json")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(description="rows matching the filters")
    page: int
    page_size: int
    pages: int


def make_page(items: list, total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, math.ceil(total / page_size))}


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
