"""Load generated CSV data into PostgreSQL.

    python scripts/load_data.py --scale starter --truncate
    python scripts/load_data.py --csv-dir data/exports/full/csv --database safety_db --truncate
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    sys.path.insert(0, str(p))

from app.core.config import get_settings  # noqa: E402
from app.core.database import make_engine  # noqa: E402
from data_generator.loader import load_dataset, load_ground_truth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=["starter", "full"], default="starter")
    ap.add_argument("--csv-dir", type=Path, default=None)
    ap.add_argument("--database", default=None, help="default: DB_NAME from .env")
    ap.add_argument("--truncate", action="store_true", help="empty all tables first")
    ap.add_argument("--no-ground-truth", action="store_true", help="skip loading ground truth into schema gt")
    args = ap.parse_args()
    csv_dir = args.csv_dir or ROOT / "data" / "exports" / args.scale / "csv"
    db = args.database or get_settings().db_name
    counts = load_dataset(make_engine(db), csv_dir, truncate=args.truncate)
    for table, n in counts.items():
        print(f"{table:34s} {n:>8d}")
    if not args.no_ground_truth:
        gt_csv = ROOT / "data" / "ground_truth" / args.scale / "ground_truth.csv"
        n = load_ground_truth(make_engine(db), gt_csv, truncate=True)
        print(f"{'gt.ground_truth_label':34s} {n:>8d}")
    print(f"loaded into database '{db}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
