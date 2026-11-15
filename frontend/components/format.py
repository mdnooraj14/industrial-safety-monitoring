"""Display formatting only."""
from __future__ import annotations


def ts(value: str | None) -> str:
    return "-" if not value else value.replace("T", " ").replace("+00:00", "")[:19]


def num(value, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def pct(value) -> str:
    return "n/a" if value is None else f"{value:,.1f} %"


def hours(value) -> str:
    return "n/a" if value is None else f"{value:,.1f} h"


def seconds(value) -> str:
    if value is None:
        return "n/a"
    s = float(value)
    if s < 120:
        return f"{s:,.0f} s"
    if s < 7200:
        return f"{s / 60:,.1f} min"
    return f"{s / 3600:,.1f} h"
