"""Synthetic data generator CLI.

    python data_generator/generate.py --scale starter
    python data_generator/generate.py --scale full --seed 20261022 --out data/exports/full

Same code for every scale; only data_generator/config.py presets differ. Output is deterministic for a given seed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data_generator.config import GENERATOR_VERSION, GeneratorConfig, from_scale  # noqa: E402
from data_generator.export import write_exports, write_ground_truth  # noqa: E402
from data_generator.generators.acks import build_acks  # noqa: E402
from data_generator.generators.alarms import build_alarms  # noqa: E402
from data_generator.generators.incidents import build_incidents  # noqa: E402
from data_generator.generators.master import build_master  # noqa: E402
from data_generator.generators.shifts import build_shifts  # noqa: E402
from data_generator.generators.states import simulate_states  # noqa: E402
from data_generator.ground_truth import baseline_labels  # noqa: E402


@dataclass
class Dataset:
    cfg: GeneratorConfig
    tables: dict[str, list[dict]] = field(default_factory=dict)
    ground_truth: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    ctx: dict = field(default_factory=dict)      # generator internals (master, shifts, states) for the injector


def generate(cfg: GeneratorConfig) -> Dataset:
    """Generate valid baseline records in dependency order (plants -> ... -> corrective actions)."""
    master = build_master(cfg)
    shifts = build_shifts(cfg, master)
    states = simulate_states(cfg, master, shifts)
    alarm_res = build_alarms(cfg, master, shifts, states)
    acks = build_acks(cfg, master, shifts, alarm_res.alarms)
    inc = build_incidents(cfg, master, shifts, states, alarm_res.alarms)
    ds = Dataset(cfg)
    ds.tables = {
        "plant": master.plants, "machine": master.machines, "safety_zone": master.zones,
        "machine_safety_zone": master.machine_zone, "interlock": master.interlocks,
        "alarm_severity_policy": master.severity_policy, "alarm_definition": master.alarm_defs,
        "operator_shift": shifts.shifts, "shift_machine_assignment": shifts.assignments,
        "machine_state_event": states.machine_states, "interlock_state_event": states.interlock_states,
        "alarm_event": alarm_res.alarms, "acknowledgement": acks,
        "incident": inc.incidents, "incident_alarm_link": inc.links, "incident_status_event": inc.incident_events,
        "corrective_action": inc.actions, "corrective_action_status_event": inc.action_events,
    }
    ds.ground_truth = baseline_labels(cfg, states, alarm_res)
    ds.ctx = dict(master=master, shifts=shifts, states=states, alarm_res=alarm_res)
    ds.meta = dict(alarm_sources=alarm_res.counts, problem_machines=sorted(master.problem_machines))
    return ds


def generate_with_anomalies(cfg: GeneratorConfig) -> tuple[Dataset, Dataset]:
    """Return (baseline, final). The baseline is never modified; the final dataset = baseline + appended anomalies."""
    from data_generator.anomaly_injector import inject_anomalies
    base = generate(cfg)
    inj = inject_anomalies(base)
    final = Dataset(cfg, inj.tables, inj.labels, dict(base.meta, injected=inj.counts), base.ctx)
    return base, final


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", choices=["starter", "full"], default="starter")
    ap.add_argument("--seed", type=int, default=None, help="override the fixed seed (default 20261022)")
    ap.add_argument("--out", type=Path, default=None,
                    help="export directory for baseline + injected anomalies (default data/exports/<scale>)")
    ap.add_argument("--baseline-out", type=Path, default=None,
                    help="export directory for the clean baseline (default data/exports/<scale>_baseline)")
    ap.add_argument("--gt-out", type=Path, default=None, help="ground-truth directory (default data/ground_truth/<scale>)")
    args = ap.parse_args(argv)
    cfg = from_scale(args.scale, args.seed)
    out = args.out or ROOT / "data" / "exports" / args.scale
    base_out = args.baseline_out or ROOT / "data" / "exports" / f"{args.scale}_baseline"
    gt_out = args.gt_out or ROOT / "data" / "ground_truth" / args.scale
    t0 = time.time()
    base, final = generate_with_anomalies(cfg)
    base_sums = write_exports(base.tables, base_out)
    sums = write_exports(final.tables, out)
    gt_sums = write_ground_truth(final.ground_truth, gt_out)
    summary = {"generator_version": GENERATOR_VERSION, "scale": args.scale, "seed": cfg.seed,
               "baseline_row_counts": {t: len(r) for t, r in base.tables.items()},
               "final_row_counts": {t: len(r) for t, r in final.tables.items()},
               "ground_truth_rows": len(final.ground_truth), "injected_anomalies": final.meta["injected"],
               "alarm_sources": base.meta["alarm_sources"],
               "files_checksummed": len(base_sums) + len(sums) + len(gt_sums)}
    print(json.dumps(summary, indent=2))
    print(f"done in {time.time() - t0:.1f}s -> {out} (baseline: {base_out}; ground truth: {gt_out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
