"""Alembic: downgrade to base and upgrade again must be clean and idempotent."""
from alembic import command
from sqlalchemy import inspect

from tests.conftest import alembic_config


def test_downgrade_then_upgrade_roundtrip(engine, test_db_name):
    cfg = alembic_config(test_db_name)
    command.downgrade(cfg, "base")
    insp = inspect(engine)
    assert "plant" not in insp.get_table_names()
    command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert "plant" in insp.get_table_names()
    assert "ground_truth_label" in insp.get_table_names(schema="gt")
