"""One reproducibility run: generate -> export -> validate -> evaluate, writing a summary next to the exports.

Used by scripts/reproducibility_check.py (run in separate processes with different PYTHONHASHSEED values).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    sys.path.insert(0, str(p))

from data_generator.config import from_scale  # noqa: E402
from data_generator.export import TABLE_ORDER, _pk, sha256, write_exports, write_ground_truth  # noqa: E402
from data_generator.generate import generate_with_anomalies  # noqa: E402
from app.services.evaluation_service import evaluate_tables  # noqa: E402
from app.validation.validator import validate  # noqa: E402


def id_digest(rows, table) -> str:
    """Order-independent digest of a table's primary keys (proves the deterministic IDs are identical)."""
    pk = _pk(table)
    h = hashlib.sha256()
    for key in sorted("|".join(str(r[c]) for c in pk) for r in rows):
        h.update(key.encode())
        h.update(b"\n")
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="starter")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    t0 = time.time()
    cfg = from_scale(a.scale)
    base, final = generate_with_anomalies(cfg)
    out = a.out
    base_sums = write_exports(base.tables, out / "baseline")
    final_sums = write_exports(final.tables, out / "final")
    gt_sums = write_ground_truth(final.ground_truth, out / "ground_truth")
    vb, vf = validate(base.tables), validate(final.tables)
    ev = evaluate_tables(final.tables, final.ground_truth)
    summary = {
        "seed": cfg.seed, "scale": a.scale,
        "row_counts": {"baseline": {t: len(base.tables[t]) for t in TABLE_ORDER}, "final": {t: len(final.tables[t]) for t in TABLE_ORDER}},
        "ground_truth_rows": len(final.ground_truth),
        "id_digests": {t: id_digest(final.tables[t], t) for t in TABLE_ORDER},
        "validation": {"baseline": vb.summary(), "final": vf.summary(),
                       "final_failed_affected_ids": {c.check_id: c.failed_ids for c in vf.failed_checks}},
        "evaluation": {k: {"micro": v.micro, "macro": v.macro, "per_class": [c.to_dict() for c in v.classes]} for k, v in ev.items()},
        "checksums": {"baseline": base_sums, "final": final_sums, "ground_truth": gt_sums},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")
    (out / "run_info.json").write_text(json.dumps({"PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED"), "seconds": round(time.time() - t0, 1),
                                                   "python": sys.version.split()[0]}), encoding="utf-8")


if __name__ == "__main__":
    main()
