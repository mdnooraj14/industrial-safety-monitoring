"""Fixed-seed reproducibility check: run the whole pipeline twice in SEPARATE processes (different PYTHONHASHSEED) and compare.

    python scripts/reproducibility_check.py --scale full           # writes docs/reproducibility_report.md
    python scripts/reproducibility_check.py --scale starter --no-report

Compared: row counts, deterministic IDs (digest of every primary key), SHA-256 of every exported file (CSV / JSON / SQL,
baseline and baseline+anomalies, ground truth), validation outcomes incl. affected ids, detector evaluation. The last
run is also compared with the files already delivered in data/exports/<scale>.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEEDS = ("1", "2")


def run_once(scale: str, out: Path, hashseed: str) -> dict:
    if out.exists():
        shutil.rmtree(out)
    env = dict(os.environ, PYTHONHASHSEED=hashseed)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "repro_worker.py"), "--scale", scale, "--out", str(out)],
                   check=True, env=env, cwd=ROOT)
    return json.loads((out / "summary.json").read_text(encoding="utf-8")) | {
        "run_info": json.loads((out / "run_info.json").read_text(encoding="utf-8"))}


def compare(a: dict, b: dict) -> list[str]:
    diffs = []
    for key in ("seed", "scale", "row_counts", "ground_truth_rows", "id_digests", "validation", "evaluation", "checksums"):
        if a[key] != b[key]:
            diffs.append(key)
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=["starter", "full"], default="starter")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the two run directories")
    args = ap.parse_args()
    work = ROOT / "data" / "processed" / f"repro_{args.scale}"
    runs = []
    for i, seed in enumerate(SEEDS, start=1):
        print(f"run {i} (PYTHONHASHSEED={seed}) ...", flush=True)
        runs.append(run_once(args.scale, work / f"run{i}", seed))
    diffs = compare(runs[0], runs[1])
    sums = runs[0]["checksums"]
    n_files = sum(len(v) for v in sums.values())
    mismatched = [f for grp in sums for f, h in sums[grp].items() if runs[1]["checksums"][grp].get(f) != h]
    # delivered files vs reproduced files
    delivered_final = json.loads((ROOT / "data" / "exports" / args.scale / "checksums.json").read_text(encoding="utf-8")) \
        if (ROOT / "data" / "exports" / args.scale / "checksums.json").exists() else None
    delivered_base = json.loads((ROOT / "data" / "exports" / f"{args.scale}_baseline" / "checksums.json").read_text(encoding="utf-8")) \
        if (ROOT / "data" / "exports" / f"{args.scale}_baseline" / "checksums.json").exists() else None
    delivered_ok = (delivered_final == runs[0]["checksums"]["final"] and delivered_base == runs[0]["checksums"]["baseline"]) \
        if delivered_final and delivered_base else None
    passed = not diffs and not mismatched
    print("reproducible:", passed, "| differing sections:", diffs, "| mismatched files:", mismatched,
          "| delivered exports identical:", delivered_ok)
    if not args.no_report:
        write_report(args.scale, runs, diffs, n_files, delivered_ok, passed)
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if passed else 1


def write_report(scale, runs, diffs, n_files, delivered_ok, passed) -> None:
    r0 = runs[0]
    rc_b, rc_f = r0["row_counts"]["baseline"], r0["row_counts"]["final"]
    val = r0["validation"]
    lines = [
        "# Fixed-seed reproducibility report", "",
        f"Scale: **{scale}**. Seed: **{r0['seed']}**. Result: **{'PASSED - identical' if passed else 'FAILED'}**.", "",
        "The complete pipeline (generate baseline, inject anomalies, export CSV / JSON / SQL, write ground truth, validate, evaluate "
        "detectors) was run twice in **separate Python processes** with different `PYTHONHASHSEED` values, so any dependence on "
        "dictionary / set ordering or on process state would show up as a difference. Command: "
        f"`python scripts/reproducibility_check.py --scale {scale}`.", "",
        "| Run | PYTHONHASHSEED | Python | Wall time |", "|---|---|---|---|"]
    for i, r in enumerate(runs, start=1):
        lines.append(f"| {i} | {r['run_info']['PYTHONHASHSEED']} | {r['run_info']['python']} | {r['run_info']['seconds']} s |")
    lines += ["", "## What was compared", "",
              "| Comparison | Items | Result |", "|---|---|---|",
              f"| Row counts (18 tables x baseline and baseline+anomalies, ground-truth rows) | {len(rc_b) + len(rc_f) + 1} | {'identical' if 'row_counts' not in diffs and 'ground_truth_rows' not in diffs else 'DIFFER'} |",
              f"| Deterministic IDs (digest of every primary key of every table) | {len(r0['id_digests'])} tables, {sum(rc_f.values()):,} IDs | {'identical' if 'id_digests' not in diffs else 'DIFFER'} |",
              f"| SHA-256 of every exported file (CSV, JSON, SQL, checksums, counts, ground truth) | {n_files} files | {'identical' if 'checksums' not in diffs else 'DIFFER'} |",
              f"| Validation outcomes (counts, failed validations, affected record ids) | baseline + injected | {'identical' if 'validation' not in diffs else 'DIFFER'} |",
              f"| Detector evaluation (TP / FP / FN, precision, recall, F1) | 2 scopes | {'identical' if 'evaluation' not in diffs else 'DIFFER'} |",
              f"| Reproduced files vs the files delivered in `data/exports/{scale}` and `{scale}_baseline` | {n_files - len(r0['checksums']['ground_truth'])} files | "
              f"{'identical' if delivered_ok else ('DIFFER' if delivered_ok is False else 'not present')} |", "",
              "## Reference numbers (identical in both runs)", "",
              f"* Alarm events: baseline **{rc_b['alarm_event']:,}**, with injected anomalies **{rc_f['alarm_event']:,}**.",
              f"* Machine-state events {rc_f['machine_state_event']:,}; acknowledgements {rc_f['acknowledgement']:,}; incidents {rc_f['incident']:,}; "
              f"corrective actions {rc_f['corrective_action']:,}.",
              f"* Validations on the clean baseline: {val['baseline']['validations_passed']}/{val['baseline']['validations_run']} passed.",
              f"* Validations with injected anomalies: {val['final']['validations_passed']}/{val['final']['validations_run']} passed; failed: "
              f"{', '.join(val['final']['failed_validation_ids'])}; anomalies detected {val['final']['anomalies_detected']}.",
              f"* Ground-truth rows: {r0['ground_truth_rows']:,}.", "",
              "## Sample checksums (final dataset)", "", "| File | SHA-256 |", "|---|---|"]
    for f in ("csv/alarm_event.csv", "csv/machine_state_event.csv", "csv/incident.csv", "sql/seed.sql"):
        lines.append(f"| `{f}` | `{r0['checksums']['final'][f]}` |")
    lines += ["", "Reproducibility holds for a fixed software environment (Python 3.11, the library versions in `requirements-lock.txt`): "
              "Faker's name generator is deterministic for a given Faker version, so upgrading Faker may change the fictional names "
              "(never the structure, IDs or counts)."]
    (ROOT / "docs" / "reproducibility_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote docs/reproducibility_report.md")


if __name__ == "__main__":
    raise SystemExit(main())
