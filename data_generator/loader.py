"""Load exported CSV files into PostgreSQL with COPY (FK-ordered). Foreign keys are enforced by the database."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from .export import TABLE_ORDER, table_columns


def load_dataset(engine, csv_dir: Path, truncate: bool = False) -> dict[str, int]:
    """Copy every table's CSV into the database. Returns {table: rows_loaded}."""
    csv_dir = Path(csv_dir)
    loaded: dict[str, int] = {}
    if truncate:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE " + ", ".join(TABLE_ORDER) + " RESTART IDENTITY CASCADE"))
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            for table in TABLE_ORDER:
                cols = ", ".join(table_columns(table))
                path = csv_dir / f"{table}.csv"
                with open(path, "rb") as f, cur.copy(
                        f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                    while chunk := f.read(1 << 20):
                        copy.write(chunk)
                cur.execute(f"SELECT count(*) FROM {table}")
                loaded[table] = cur.fetchone()[0]
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
    return loaded


def load_ground_truth(engine, csv_path: Path, truncate: bool = False) -> int:
    """Load ground_truth.csv into the separate gt schema (never joined to feature tables)."""
    from .ground_truth import GT_COLUMNS
    cols = ", ".join(GT_COLUMNS)
    if truncate:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE gt.ground_truth_label"))
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            with open(csv_path, "rb") as f, cur.copy(
                    f"COPY gt.ground_truth_label ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                while chunk := f.read(1 << 20):
                    copy.write(chunk)
            cur.execute("SELECT count(*) FROM gt.ground_truth_label")
            n = cur.fetchone()[0]
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
    return n
