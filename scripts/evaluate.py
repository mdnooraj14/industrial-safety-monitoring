"""Generate the evaluation metrics report (Part A business KPIs, Part B detection evaluation).

    python scripts/evaluate.py --source generate --scale starter     # no database needed
    python scripts/evaluate.py --source db                           # evaluate what is loaded in PostgreSQL

Writes docs/evaluation_metrics.md and data/processed/metrics_<label>.json.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    sys.path.insert(0, str(p))

from app.analytics.kpis import compute_kpis  # noqa: E402
from app.analytics.report import evaluation_markdown, kpi_markdown  # noqa: E402
from app.services.evaluation_service import evaluate_tables  # noqa: E402

HEADER = """# Evaluation metrics report

This report keeps two different kinds of numbers apart.

* **Part A - business KPIs** describe the plant: alarms, acknowledgement delays, incidents, MTTR, corrective actions.
  They never use ground truth. Replay duplicates and acknowledgements dated before their alarm are excluded from the
  maths and counted separately, so data-quality defects do not distort plant performance.
* **Part B - detection evaluation** describes the detectors: precision, recall, F1, TP / FP / FN and confusion matrices
  against the *separate* ground truth. Detectors never see the labels; only the evaluation step combines them.

Precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2PR/(P+R); undefined values are shown as n/a. The five anomaly
detectors are exact rule checks on deliberately violated rules, so perfect scores there are expected by construction;
the pattern detectors (flood, nuisance, repeated unsafe) are the informative ones.

"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["generate", "db"], default="generate")
    ap.add_argument("--scale", choices=["starter", "full"], default="starter")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "evaluation_metrics.md")
    args = ap.parse_args()
    if args.source == "generate":
        from data_generator.config import from_scale
        from data_generator.generate import generate_with_anomalies
        cfg = from_scale(args.scale)
        _, final = generate_with_anomalies(cfg)
        tables, labels, cutoff, label = final.tables, final.ground_truth, cfg.end, f"{args.scale}"
        source = f"generated {args.scale} dataset (seed {cfg.seed}), baseline + injected anomalies"
    else:
        from sqlalchemy.orm import Session
        from app.core.config import get_settings
        from app.core.database import make_engine
        from app.repositories.bulk_repository import load_feature_tables
        from app.repositories.ground_truth_repository import load_labels
        db = get_settings().db_name
        with Session(make_engine(db)) as s:
            tables, labels = load_feature_tables(s), load_labels(s)
        cutoff, label, source = None, f"db_{db}", f"database '{db}'"
    kpis = compute_kpis(tables, cutoff=cutoff)
    results = evaluate_tables(tables, labels)
    md = [HEADER, f"Source: {source}.", "", "## Part A - Business KPIs", "", kpi_markdown(kpis, "Plant performance"), "",
          "## Part B - Detection evaluation", "",
          evaluation_markdown(results["anomalies"], "Injected anomalies (five rule detectors)"),
          evaluation_markdown(results["patterns"], "Baseline scenarios (flood, nuisance, repeated unsafe condition)")]
    args.out.write_text("\n".join(md), encoding="utf-8")
    proc = ROOT / "data" / "processed"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / f"metrics_{label}.json").write_text(json.dumps(
        {"business_kpis": kpis, "detection_evaluation": {k: r.to_dict() for k, r in results.items()}}, indent=2,
        default=str), encoding="utf-8")
    for k, r in results.items():
        print(f"{k}: micro {r.micro}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
