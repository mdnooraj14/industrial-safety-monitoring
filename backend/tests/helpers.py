"""Shared test helpers."""
from sqlalchemy import text

from app.models import Base
from data_generator.export import TABLE_ORDER


def assert_foreign_keys(tables: dict) -> int:
    """Every non-null foreign key in `tables` resolves to a parent row. Returns the number of FK values checked."""
    pk = {t: {tuple(r[c.name] for c in Base.metadata.tables[t].primary_key.columns) for r in rows}
          for t, rows in tables.items()}
    checked = 0
    for table in TABLE_ORDER:
        for fk in Base.metadata.tables[table].foreign_key_constraints:
            parent, cols = fk.referred_table.name, [e.parent.name for e in fk.elements]
            for r in tables[table]:
                vals = tuple(r[c] for c in cols)
                if any(v is None for v in vals):
                    continue
                assert vals in pk[parent], f"{table}.{cols} -> {parent}: {vals}"
                checked += 1
    return checked


def read_tables(engine) -> dict:
    """Read every feature table from the database as list-of-dicts (public schema only; never the gt schema)."""
    out = {}
    with engine.connect() as conn:
        for t in TABLE_ORDER:
            out[t] = [dict(r._mapping) for r in conn.execute(text(f"SELECT * FROM {t}"))]
    return out
