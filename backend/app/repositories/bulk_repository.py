"""Bulk reads of the feature tables for validation and analytics (public schema only; never the gt schema).

`load_feature_tables` always reads from the database. `cached_tables` re-uses the last read while the data has not
changed (a cheap fingerprint of row counts and newest `created_at` per table), so dashboards that issue many analytics
requests do not reload ~200k rows each time.
"""
from __future__ import annotations

import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Base

FEATURE_TABLES = [t.name for t in Base.metadata.sorted_tables if t.schema is None]


def load_feature_tables(db: Session) -> dict[str, list[dict]]:
    """Return every feature table as a list of dicts (typed values, `created_at` omitted)."""
    out: dict[str, list[dict]] = {}
    for name in FEATURE_TABLES:
        cols = [c.name for c in Base.metadata.tables[name].columns if c.name != "created_at"]
        rows = db.execute(text(f"SELECT {', '.join(cols)} FROM {name}")).all()
        out[name] = [dict(zip(cols, r)) for r in rows]
    return out


def fingerprint(db: Session) -> tuple:
    """Changes whenever rows are added (or the table is reloaded): (count, newest created_at) per table."""
    parts = []
    for name in FEATURE_TABLES:
        parts.append(tuple(db.execute(text(f"SELECT count(*), max(created_at) FROM {name}")).one()))
    return tuple(parts)


_lock = threading.Lock()
_cache: dict = {"key": None, "tables": None, "results": {}}


def cached_tables(db: Session) -> tuple[tuple, dict[str, list[dict]]]:
    """(fingerprint, tables). The fingerprint also keys derived-result caches (see `cached_result`)."""
    key = fingerprint(db)
    with _lock:
        if _cache["key"] != key:
            _cache.update(key=key, tables=load_feature_tables(db), results={})
        return key, _cache["tables"]


def cached_result(key: tuple, name: tuple, compute):
    """Memoise a derived result for the current data version (cleared automatically when the data changes)."""
    with _lock:
        if _cache["key"] == key and name in _cache["results"]:
            return _cache["results"][name]
    value = compute()
    with _lock:
        if _cache["key"] == key:
            if len(_cache["results"]) > 64:
                _cache["results"].clear()
            _cache["results"][name] = value
    return value


def clear_cache() -> None:
    with _lock:
        _cache.update(key=None, tables=None, results={})
