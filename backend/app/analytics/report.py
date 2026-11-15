"""Markdown rendering of the metrics report: Part A business KPIs, Part B detection evaluation (kept visibly apart)."""
from __future__ import annotations

from app.analytics.evaluation import EvaluationResult


def f(x, suffix="") -> str:
    return "n/a" if x is None else (f"{x:,}{suffix}" if isinstance(x, int) else f"{x:,.2f}{suffix}")


def table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def kpi_markdown(k: dict, title: str = "Business KPIs") -> str:
    o, a, ak = k["overview"], k["alarms"], k["acknowledgements"]
    fl, nu, un, inc, ca = k["floods"], k["nuisance"], k["unsafe_conditions"], k["incidents"], k["corrective_actions"]
    L = [f"### {title}", "", f"Cut-off (snapshot) time: `{k['meta']['cutoff']}`. {k['meta']['note']}", "",
         "**Overview**", ""]
    L += table(["KPI", "Value"], [
        ["Plants / machines", f"{o['plants']} / {o['machines']}"],
        ["Alarm events stored", f(o["alarm_events_stored"])], ["Alarm events analysed (replay duplicates excluded)", f(o["alarm_events_analysed"])],
        ["Replay duplicates excluded", f(o["replay_duplicates_excluded"])],
        ["Critical / high alarms", f"{o['critical_alarms']} / {o['high_alarms']}"],
        ["Incidents (critical)", f"{o['incidents']} ({o['critical_incidents']})"],
        ["Open incidents (not closed) / unresolved", f"{o['open_incidents']} / {o['unresolved_incidents']}"],
        ["Delayed acknowledgements (late + missed)", f"{o['delayed_acknowledgements']} ({f(o['delayed_acknowledgement_pct'], ' %')})"],
        ["Alarm floods", o["alarm_floods"]], ["Nuisance alarm clusters", o["nuisance_clusters"]],
        ["Repeated unsafe conditions", o["repeated_unsafe_conditions"]]])
    L += ["**Alarms by plant**", ""] + table(
        ["Plant", "Alarms", "Critical", "High", "Medium", "Low"],
        [[r["plant_id"] + " " + r["plant_name"], f(r["alarms"]), r["critical"], r["high"], r["medium"], r["low"]]
         for r in a["by_plant"]])
    L += ["**Top 10 machines by alarms**", ""] + table(
        ["Machine", "Alarms", "Critical", "High", "Share"],
        [[r["machine_id"], f(r["alarms"]), r["critical"], r["high"], f(r["share_pct"], " %")] for r in a["by_machine"][:10]])
    L += ["**Acknowledgement**", ""] + table(["KPI", "Value"], [
        ["Acknowledged / on time / late / missed / pending", f"{ak['acknowledged']} / {ak['on_time']} / {ak['late']} / {ak['missed']} / {ak['pending']}"],
        ["Average / median / p90 acknowledgement time (s)", f"{f(ak['ack_seconds']['mean'])} / {f(ak['ack_seconds']['median'])} / {f(ak['ack_seconds']['p90'])}"],
        ["Delayed acknowledgement %", f(ak["delayed_pct"], " %")],
        ["Invalid (pre-alarm) acknowledgements excluded", ak["invalid_acknowledgements_excluded"]]])
    L += [f"*{ak['definition']}*", ""] + table(
        ["Severity", "Alarms", "Acknowledged", "Avg ack (s)", "Late", "Missed", "Delayed %"],
        [[s, v["alarms"], v["acknowledged"], f(v["avg_ack_seconds"]), v["late"], v["missed"], f(v["delayed_pct"], " %")]
         for s, v in ak["by_severity"].items()])
    L += ["**Alarm floods, nuisance and repeated unsafe conditions**", ""] + table(["KPI", "Value"], [
        ["Flood episodes (alarms in floods, per day)", f"{fl['episodes']} ({fl['alarms_in_floods']}, {fl['floods_per_day']}/day)"],
        ["Nuisance clusters (alarms in clusters)", f"{nu['clusters']} ({nu['alarms_in_clusters']}, {f(nu['alarms_in_clusters_pct'], ' %')})"],
        ["Unsafe episodes by rule", ", ".join(f"{k_}={v}" for k_, v in un["by_rule"].items()) or "none"],
        ["Repeated unsafe conditions (episodes involved)", f"{un['repeated_conditions']} ({un['episodes_in_repeated_conditions']})"]])
    L += ["**Incidents**", ""] + table(["KPI", "Value"], [
        ["Total / critical / open / unresolved", f"{inc['total']} / {inc['critical']} / {inc['open']} / {inc['unresolved']}"],
        ["By status", ", ".join(f"{k_}={v}" for k_, v in sorted(inc["by_status"].items())) or "none"],
        ["MTTR (hours, mean over resolved) / resolved count", f"{f(inc['mttr_hours'])} / {inc['resolved_count']}"],
        ["Mean time to start investigation (h)", f(inc["mean_time_to_investigate_hours"])]])
    L += ["Incidents by machine (top 10):", ""] + table(
        ["Machine", "Incidents", "Critical", "Open"],
        [[r["machine_id"], r["incidents"], r["critical"], r["open"]] for r in inc["by_machine"][:10]])
    L += ["**Corrective actions**", ""] + table(["KPI", "Value"], [
        ["Total (live)", f"{ca['total']} ({ca['live']})"], ["By status", ", ".join(f"{k_}={v}" for k_, v in sorted(ca["by_status"].items()))],
        ["Completion rate", f(ca["completion_rate_pct"], " %")], ["Verification rate", f(ca["verification_rate_pct"], " %")],
        ["Failed verifications / closed without verification", f"{ca['failed_verifications']} / {ca['closed_without_verification']}"],
        ["Overdue", ca["overdue"]]])
    return "\n".join(L)


def evaluation_markdown(res: EvaluationResult, title: str) -> str:
    L = [f"### {title}", "", f"Scope: {res.scope}.", ""]
    L += table(["Issue type", "TP", "FP", "FN", "Precision", "Recall", "F1", "Support"],
               [[c.issue_type, c.tp, c.fp, c.fn, f(c.precision), f(c.recall), f(c.f1), c.support] for c in res.classes]
               + [["**micro (all)**", res.micro["tp"], res.micro["fp"], res.micro["fn"], f(res.micro["precision"]),
                   f(res.micro["recall"]), f(res.micro["f1"]), ""],
                  ["**macro**", "", "", "", f(res.macro["precision"]), f(res.macro["recall"]), f(res.macro["f1"]), ""]])
    L += ["Confusion matrix (rows = actual from ground truth, columns = predicted by the detectors):", ""]
    labels = res.labels
    L += table(["actual \\ predicted"] + labels, [[a] + [f"{res.confusion[a][p]:,}" for p in labels] for a in labels])
    for c in res.classes:
        if c.fp or c.fn:
            L.append(f"- **{c.issue_type}** false positives: {', '.join(c.false_positive_ids[:8]) or 'none'}"
                     f"{' ...' if len(c.false_positive_ids) > 8 else ''}; false negatives: "
                     f"{', '.join(c.false_negative_ids[:8]) or 'none'}{' ...' if len(c.false_negative_ids) > 8 else ''}")
    return "\n".join(L) + "\n"
