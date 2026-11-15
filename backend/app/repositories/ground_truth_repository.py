"""The ONLY data-access module that reads the gt schema. Used by the evaluation service, never by detectors / KPIs."""
from sqlalchemy import text
from sqlalchemy.orm import Session

COLUMNS = ["label_id", "label_class", "issue_type", "record_table", "record_id", "group_id", "expected_decision",
           "description"]


def load_labels(db: Session) -> list[dict]:
    rows = db.execute(text(f"SELECT {', '.join(COLUMNS)} FROM gt.ground_truth_label ORDER BY label_id")).all()
    return [dict(zip(COLUMNS, r)) for r in rows]
