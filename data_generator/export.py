"""Deterministic CSV / JSON / SQL export plus checksums. No timestamps or machine-specific data are written."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.models import Base

from .common import fmt_date, fmt_ts
from .ground_truth import GT_COLUMNS

# FK-dependency order (parents first) for the 18 feature tables
TABLE_ORDER = [t.name for t in Base.metadata.sorted_tables if t.schema is None]


def table_columns(table: str) -> list[str]:
    return [c.name for c in Base.metadata.tables[table].columns if c.name != "created_at"]


def _pk(table: str) -> list[str]:
    return [c.name for c in Base.metadata.tables[table].primary_key.columns]


def sort_rows(table: str, rows: list[dict]) -> list[dict]:
    pk = _pk(table)
    return sorted(rows, key=lambda r: tuple(str(r[k]) for k in pk))


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return fmt_ts(v)
    if isinstance(v, date):
        return fmt_date(v)
    if isinstance(v, (float, Decimal)):
        return f"{v:.1f}"
    return str(v)


def _json_value(v):
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, (float, Decimal)):
        return float(f"{v:.1f}")
    return _text(v)


def _sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, Decimal)):
        return f"{v:.1f}" if isinstance(v, (float, Decimal)) else str(v)
    return "'" + _text(v).replace("'", "''") + "'"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_exports(tables: dict[str, list[dict]], out_dir: Path, batch: int = 500) -> dict:
    """Write csv/, json/, sql/seed.sql, row_counts.json, checksums.json. Returns {relative_path: sha256}."""
    out_dir = Path(out_dir)
    (out_dir / "csv").mkdir(parents=True, exist_ok=True)
    (out_dir / "json").mkdir(exist_ok=True)
    (out_dir / "sql").mkdir(exist_ok=True)
    counts = {}
    sql_parts = ["-- Synthetic seed data (deterministic). Load order respects foreign keys.", "BEGIN;"]
    for table in TABLE_ORDER:
        cols = table_columns(table)
        rows = sort_rows(table, tables[table])
        counts[table] = len(rows)
        with open(out_dir / "csv" / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            for r in rows:
                w.writerow([_text(r[c]) for c in cols])
        with open(out_dir / "json" / f"{table}.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump([{c: _json_value(r[c]) for c in cols} for r in rows], f, ensure_ascii=False)
            f.write("\n")
        for i in range(0, len(rows), batch):
            values = ",\n".join("(" + ", ".join(_sql_literal(r[c]) for c in cols) + ")" for r in rows[i:i + batch])
            sql_parts.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n{values};")
    sql_parts.append("COMMIT;")
    with open(out_dir / "sql" / "seed.sql", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sql_parts) + "\n")
    with open(out_dir / "row_counts.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(counts, f, indent=2)
        f.write("\n")
    sums = {str(p.relative_to(out_dir)).replace("\\", "/"): sha256(p)
            for p in sorted(out_dir.rglob("*")) if p.is_file() and p.name != "checksums.json"}
    with open(out_dir / "checksums.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(sums, f, indent=2)
        f.write("\n")
    return sums


def write_ground_truth(rows: list[dict], gt_dir: Path) -> dict:
    gt_dir = Path(gt_dir)
    gt_dir.mkdir(parents=True, exist_ok=True)
    with open(gt_dir / "ground_truth.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(GT_COLUMNS)
        for r in rows:
            w.writerow([_text(r[c]) for c in GT_COLUMNS])
    with open(gt_dir / "ground_truth.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump([{c: r[c] for c in GT_COLUMNS} for r in rows], f, ensure_ascii=False)
        f.write("\n")
    return {p.name: sha256(p) for p in sorted(gt_dir.glob("ground_truth.*"))}
