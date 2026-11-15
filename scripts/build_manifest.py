"""Build the source manifest (data provenance): docs/source_manifest.md and data/source_manifest.json.

    python scripts/build_manifest.py
"""
import hashlib
import json
import sys
from importlib import metadata as md
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    sys.path.insert(0, str(p))

from data_generator.config import FULL, GENERATOR_VERSION, STARTER  # noqa: E402

LIBS = ["Faker", "numpy", "pandas", "SQLAlchemy", "alembic", "psycopg", "fastapi", "pydantic", "streamlit", "plotly", "requests", "pytest"]

TABLE_PROVENANCE = [
    ("plant, machine, safety_zone, machine_safety_zone, interlock", "seeded draws from fixed template lists (cities, industries, machine types, interlock plans per machine type)", "none"),
    ("alarm_severity_policy", "copied from the documented rule defaults (`RulesConfig`)", "priority_rank, deadlines"),
    ("alarm_definition", "templates by trigger type x machine type x interlock type, numbered variants", "none"),
    ("operator_shift, shift_machine_assignment", "three 8-hour shifts per plant-local day, two operators per shift, machines split by slot", "shift window (UTC), assignments"),
    ("machine_state_event, interlock_state_event", "per-machine seeded state machine with an allowed-transition graph; unsafe excursions at documented probabilities", "interlock_status_summary, safety_assessment, unsafe_rule_code (rules R-U/R-S/R-I), shift_id"),
    ("alarm_event", "state-driven alarms, planted floods and nuisance clusters, background alarms filling the target count exactly", "severity (AL-1), ack_deadline_at (AL-2), lifecycle_status, machine_state_event_id, shift_id"),
    ("acknowledgement", "delay and missing-acknowledgement outcome from workload, shift and repeat rules", "ack_delay_seconds, ack_status (AL-4)"),
    ("incident, incident_alarm_link, incident_status_event", "created by rule IN-1 from unsafe episodes; timeline from per-severity ranges truncated at the cut-off", "severity (IN-2), status and milestone timestamps from the history"),
    ("corrective_action, corrective_action_status_event", "chains per incident with verification outcome by action type (CA-4)", "status and timestamps from the history"),
    ("injected anomalies (appended rows)", "five labelled scenarios appended to a copy of the baseline", "derived columns by the same rules"),
    ("ground truth (separate files / schema gt)", "labels written by the generator, never read by detectors or KPIs", "n/a"),
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def lib_info(name: str) -> dict:
    try:
        m = md.metadata(name)
        lic = m.get("License-Expression") or (m.get("License") or "").strip().splitlines()[0:1]
        if isinstance(lic, list):
            lic = lic[0] if lic else ""
        if not lic or len(lic) > 60:
            cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
            lic = ", ".join(cls) or "see package metadata"
        return {"name": name, "version": md.version(name), "license": lic}
    except md.PackageNotFoundError:
        return {"name": name, "version": "not installed", "license": ""}


def dataset_entry(label: str, folder: Path) -> dict | None:
    if not folder.exists():
        return None
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    sums = json.loads((folder / "checksums.json").read_text(encoding="utf-8")) if (folder / "checksums.json").exists() else {}
    digest = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(sums.items())).encode()).hexdigest() if sums else ""
    return {"label": label, "path": str(folder.relative_to(ROOT)).replace("\\", "/"), "files": len(files),
            "bytes": sum(p.stat().st_size for p in files), "manifest_digest": digest,
            "checksums": sums or {p.name: sha(p) for p in files}}


def main() -> None:
    datasets = [d for d in (
        dataset_entry("full: baseline + injected anomalies", ROOT / "data" / "exports" / "full"),
        dataset_entry("full: clean baseline", ROOT / "data" / "exports" / "full_baseline"),
        dataset_entry("full: ground truth", ROOT / "data" / "ground_truth" / "full"),
        dataset_entry("starter: baseline + injected anomalies", ROOT / "data" / "exports" / "starter"),
        dataset_entry("starter: clean baseline", ROOT / "data" / "exports" / "starter_baseline"),
        dataset_entry("starter: ground truth", ROOT / "data" / "ground_truth" / "starter")) if d]
    import platform
    manifest = {
        "title": "Industrial Safety Monitoring - synthetic data source manifest",
        "generator": {"version": GENERATOR_VERSION, "seed": FULL.seed, "entry_point": "data_generator/generate.py",
                      "python": platform.python_version()},
        "provenance": {"external_data_used": False, "web_retrieval": False, "personal_data": False, "confidential_data": False},
        "window": {"start": FULL.start.isoformat(), "days": FULL.days, "end": FULL.end.isoformat()},
        "scales": {"full": {k: getattr(FULL, k) for k in ("n_plants", "n_machines", "n_alarm_defs", "n_alarm_events", "days")},
                   "starter": {k: getattr(STARTER, k) for k in ("n_plants", "n_machines", "n_alarm_defs", "n_alarm_events", "days")}},
        "libraries": [lib_info(n) for n in LIBS], "datasets": datasets,
    }
    (ROOT / "data" / "source_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    L = ["# Source manifest and data provenance", "",
         "Generated by `scripts/build_manifest.py` from the files actually present. Machine-readable copy: `data/source_manifest.json` "
         "(includes every file's SHA-256).", "",
         "## 1. Provenance in one paragraph", "",
         "**All data in this project is synthetic and generated by this repository's code.** No external or public dataset was "
         "downloaded, copied, scraped or transformed; nothing was retrieved from the web; no access controls were involved; no personal, "
         "confidential or customer data was used. Because no external data is used, there is no source URL, dataset version, retrieval "
         "date, licence, attribution, transformation list or discarded-field list to record (the fields the problem statement asks for "
         "would all read \"not applicable\").", "",
         "| Item | Statement |", "|---|---|",
         "| External datasets | none |", "| Fictional content | operator and engineer names (Faker, seeded; entirely fictional), employee ids `EMP-nnnnn` (synthetic), "
         "plant names (invented labels) |",
         "| Real-world labels | plant cities, countries and time zones are real place names used only as labels; machine types and interlock "
         "types are generic industrial terms |",
         "| Public reference material | concepts only, for schema and rule inspiration: alarm-management practice (alarm floods, chattering / "
         "nuisance alarms, acknowledgement deadlines - e.g. ISA-18.2 / IEC 62682 terminology) and machine-safeguarding vocabulary "
         "(interlocks, guard locks, e-stops, light curtains). No text, tables or data were copied. All thresholds are this project's "
         "own configurable defaults (`docs/business_rules.md`), not normative values of any standard |",
         "| Personal data | none; names are random and fictional, any resemblance to real people is coincidental |", "",
         "## 2. How the data is produced", "",
         f"* Entry point `python data_generator/generate.py --scale full` (generator version **{GENERATOR_VERSION}**, fixed seed **{FULL.seed}**; "
         "`--seed` overrides).",
         f"* Window: {FULL.start:%Y-%m-%d} to {FULL.end:%Y-%m-%d} UTC ({FULL.days} days). Full scale: {FULL.n_plants} plants, {FULL.n_machines} machines, "
         f"{FULL.n_alarm_defs} alarm definitions, {FULL.n_alarm_events:,} baseline alarm events. Starter scale: {STARTER.n_plants} plants, "
         f"{STARTER.n_machines} machines, {STARTER.n_alarm_defs} definitions, {STARTER.n_alarm_events:,} events, {STARTER.days} days. Same code, different presets.",
         "* Each generation stage has its own random stream derived from the seed, so stages do not disturb each other. "
         "Outcomes are derived by the rules in `backend/app/business_rules`, not assigned independently.",
         "* Valid baseline first, then five labelled anomalies appended to a copy (`data_generator/anomaly_injector.py`); labels are written "
         "to separate ground-truth files and the database schema `gt`.",
         "* Reproducibility evidence: `docs/reproducibility_report.md`.", "",
         "| Tables | How produced | Derived columns |", "|---|---|---|"]
    L += [f"| {a} | {b} | {c} |" for a, b, c in TABLE_PROVENANCE]
    L += ["", "## 3. Files", "", "| Dataset | Folder | Files | Size | Digest of all file checksums |", "|---|---|---|---|---|"]
    for d in datasets:
        L.append(f"| {d['label']} | `{d['path']}` | {d['files']} | {d['bytes'] / 1e6:,.1f} MB | `{d['manifest_digest'][:16] or 'n/a'}` |")
    L += ["", "Each export folder contains `csv/`, `json/`, `sql/seed.sql`, `row_counts.json` and `checksums.json` (SHA-256 of every file).", "",
          "## 4. Software used (versions installed when this manifest was built)", "", "| Library | Version | Licence (from package metadata) |", "|---|---|---|"]
    L += [f"| {l['name']} | {l['version']} | {l['license']} |" for l in manifest["libraries"]]
    L += ["", f"Python {manifest['generator']['python']}. Exact pins: `requirements-lock.txt`. All of the above are free and open-source "
          "components; PostgreSQL is used as the database server.", ""]
    (ROOT / "docs" / "source_manifest.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote docs/source_manifest.md and data/source_manifest.json ({len(datasets)} datasets)")


if __name__ == "__main__":
    main()
