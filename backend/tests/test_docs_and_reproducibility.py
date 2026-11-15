"""Milestone 10 tests: documentation matches the schema; the pipeline is reproducible across separate processes."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.models import Base

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


# ---------------------------------------------------------------- data dictionary vs the real schema
def parse_dictionary() -> dict[str, dict[str, dict]]:
    """{table: {field: {'req': 'Y'/'N', 'type': str}}} from docs/data_dictionary.md."""
    text = (DOCS / "data_dictionary.md").read_text(encoding="utf-8")
    tables: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^## \d+\. (?:gt\.)?([a-z_]+)", line)
        if m:
            current = m.group(1)
            tables[current] = {}
            continue
        if current is None or not line.startswith("|") or line.startswith("|---") or line.startswith("| Field"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        names = [n.strip("` ") for n in cells[0].split(" / ")]
        types = [t.strip() for t in cells[1].split(" / ")] if len(cells) > 1 else [""]
        reqs = [r.strip() for r in cells[2].split(" / ")] if len(cells) > 2 else [""]
        for i, name in enumerate(names):
            tables[current][name] = {"type": types[i] if i < len(types) else types[-1],
                                     "req": reqs[i] if i < len(reqs) else reqs[-1]}
    return tables


def test_data_dictionary_lists_every_column_and_only_real_columns():
    doc = parse_dictionary()
    problems = []
    for table in Base.metadata.sorted_tables:
        cols = {c.name for c in table.columns if c.name != "created_at"}
        if table.name not in doc:
            problems.append(f"table {table.name} missing from the data dictionary")
            continue
        documented = set(doc[table.name])
        if documented != cols:
            problems.append(f"{table.name}: missing in doc {sorted(cols - documented)}; not in schema {sorted(documented - cols)}")
    assert not problems, "\n".join(problems)
    assert len(doc) == len(Base.metadata.tables) == 19


def test_data_dictionary_required_flags_match_nullability():
    doc, problems = parse_dictionary(), []
    for table in Base.metadata.sorted_tables:
        for c in table.columns:
            if c.name == "created_at" or table.name not in doc or c.name not in doc[table.name]:
                continue
            req = doc[table.name][c.name]["req"]
            expected = "N" if c.nullable else "Y"
            if req not in (expected,):
                problems.append(f"{table.name}.{c.name}: doc says {req!r}, schema is {'optional' if c.nullable else 'required'}")
    assert not problems, "\n".join(problems)


def test_data_dictionary_enum_values_match_code():
    """Every enum value mentioned for an enum column must exist in the code's enum (and the code's values must be documented)."""
    from app.models import enums as E
    text = (DOCS / "data_dictionary.md").read_text(encoding="utf-8")
    for enum in (E.IndustryType, E.MachineType, E.ZoneType, E.InterlockType, E.TriggerType, E.MachineState, E.IncidentStatus,
                 E.ActionStatus, E.IssueType, E.ActionType, E.AckMethod, E.LinkRole, E.SafetyAssessment, E.InterlockStatus,
                 E.InterlockSummary, E.ChangedByRole, E.PlantStatus, E.MachineStatus):
        for value in enum:
            assert value.value in text, f"{enum.__name__}.{value.value} is not documented in data_dictionary.md"


def test_er_diagram_mentions_every_table():
    er = (DOCS / "er_diagram.md").read_text(encoding="utf-8").upper()
    for t in Base.metadata.tables:
        name = t.split(".")[-1].upper()
        assert name in er, f"{name} missing from er_diagram.md"


def test_documented_endpoints_equal_the_real_api():
    from app.main import app
    doc = (DOCS / "api.md").read_text(encoding="utf-8")
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            assert f"| {method.upper()} | `{path}` |" in doc, f"{method.upper()} {path} missing from api.md (run scripts/export_api_docs.py)"


# ---------------------------------------------------------------- reproducibility across processes
def test_pipeline_is_reproducible_across_processes_with_different_hash_seeds():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "reproducibility_check.py"), "--scale", "starter", "--no-report"],
                       capture_output=True, text=True, cwd=ROOT, timeout=900)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert "reproducible: True" in r.stdout and "differing sections: []" in r.stdout and "mismatched files: []" in r.stdout
