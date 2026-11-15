"""Page 6 - Data quality and anomaly analysis: validation report, anomalies, ground-truth comparison, P/R/F1."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from frontend.components import charts
from frontend.components.data import api
from frontend.components.format import num
from frontend.components.layout import chart, setup, severity_style, table, tiles

setup("Data Quality and Anomaly Analysis", "Validation results, detected anomalies and how well the detectors match the ground truth.",
      ":material/fact_check:")
st.info("Data quality covers the whole database, so the plant / machine / date filters do not apply on this page.")

tab_val, tab_anom, tab_eval = st.tabs(["Validation report", "Detected anomalies", "Ground-truth comparison"])

with tab_val:
    rep = api("/api/v1/validation/report", max_errors=10)
    s = rep["summary"]
    tiles([("Records checked", num(s["total_records_checked"]), f"{num(s['total_record_evaluations'])} record evaluations across all validations"),
           ("Validations run", num(s["validations_run"])), ("Passed", num(s["validations_passed"])),
           ("Failed", num(s["validations_failed"]), ", ".join(s["failed_validation_ids"]) or "none")])
    checks = [dict(id=c["check_id"], category=c["category"], validation=c["name"], status=c["status"],
                   records_checked=c["records_checked"], failures=c["failure_count"]) for c in rep["checks"]]
    table(checks, height=330, style=lambda v: ("color: #b42318; font-weight: 600" if v == "FAILED" else "color: #2f9e44" if v == "PASSED" else ""))
    for c in rep["checks"]:
        if c["status"] == "FAILED":
            with st.expander(f"{c['check_id']} - {c['name']}: {c['failure_count']} failure(s)"):
                st.caption(c["description"])
                table(c["errors"], ["table", "record_id", "detail"])
                if c.get("errors_truncated"):
                    st.caption(f"... and {c['errors_truncated']} more (use /api/v1/validation/report?full=true).")

with tab_anom:
    summary = api("/api/v1/anomalies/summary")
    left, right = st.columns(2)
    with left:
        st.markdown("**Anomalies (rule detectors)**")
        df = pd.DataFrame(dict(category=list(summary["anomaly"]["by_issue_type"]), records=list(summary["anomaly"]["by_issue_type"].values())))
        if len(df):
            chart(charts.simple_bar(df, "category", "records", f"{summary['anomaly']['total']} anomalous records", horizontal=True, height=300), "anom_cat")
    with right:
        st.markdown("**Patterns (floods, nuisance, repeated unsafe)**")
        df = pd.DataFrame(dict(category=list(summary["pattern"]["by_issue_type"]), records=list(summary["pattern"]["by_issue_type"].values())))
        if len(df):
            chart(charts.simple_bar(df, "category", "records", f"{summary['pattern']['total']} flagged records", horizontal=True, height=300), "pat_cat")
    kind = st.radio("Detector family", ["anomaly", "pattern"], horizontal=True, key="anom_kind")
    types = ["All"] + list(summary[kind]["by_issue_type"])
    issue = st.selectbox("Category", types, key="anom_issue")
    page_no = st.number_input("Page", min_value=1, value=1, step=1, key="anom_page")
    page = api("/api/v1/anomalies", kind=kind, issue_type=None if issue == "All" else issue, page=int(page_no), page_size=50)
    st.caption(f"{num(page['total'])} records; page {page['page']} of {page['pages']}.")
    table(page["items"], ["issue_type", "record_table", "record_id", "reason"], height=360)

with tab_eval:
    st.warning("DETECTION EVALUATION - this compares the detectors with the separate ground-truth labels. It measures the detectors; "
               "it is not a business KPI. The detectors never see the labels.")
    ev = api("/api/v1/anomalies/evaluation")
    for scope, title in (("anomalies", "Injected anomalies (five rule detectors)"), ("patterns", "Baseline scenarios (flood, nuisance, repeated unsafe)")):
        r = ev[scope]
        st.subheader(title)
        micro, macro = r["micro"], r["macro"]
        fmt = lambda v: "n/a" if v is None else f"{v:.3f}"
        tiles([("Precision (micro)", fmt(micro["precision"])), ("Recall (micro)", fmt(micro["recall"])), ("F1 (micro)", fmt(micro["f1"])),
               ("True / false positives, false negatives", f"{micro['tp']} / {micro['fp']} / {micro['fn']}")])
        st.caption(f"Macro average over {macro['classes_averaged']} classes: precision {fmt(macro['precision'])}, recall {fmt(macro['recall'])}, F1 {fmt(macro['f1'])}.")
        table([{k: (None if v is None else (round(v, 3) if isinstance(v, float) else v)) for k, v in c.items()
                if k in ("issue_type", "tp", "fp", "fn", "precision", "recall", "f1", "support")} for c in r["per_class"]])
        cm = r["confusion_matrix"]
        chart(charts.heatmap(cm["matrix"], cm["labels"], "Confusion matrix (per record)"), f"cm_{scope}")
        for c in r["per_class"]:
            if c["fp"] or c["fn"]:
                with st.expander(f"{c['issue_type']}: {c['fp']} false positives, {c['fn']} false negatives"):
                    st.write("False positives:", ", ".join(c["false_positive_ids"][:30]) or "none")
                    st.write("False negatives:", ", ".join(c["false_negative_ids"][:30]) or "none")
