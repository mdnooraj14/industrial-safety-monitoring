"""Stage 1-5: Plant, Machine, SafetyZone (+bridge), Interlock, AlarmSeverityPolicy, AlarmDefinition."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from app.business_rules.config import DEFAULT_RULES

from ..common import interlock_id, machine_id, plant_id, rng_for, zone_id

EFFECTIVE_FROM = date(2025, 1, 1)

PLANT_TEMPLATES = [
    ("Pune Works", "Pune", "IN", "Asia/Kolkata", "AUTOMOTIVE"),
    ("Stuttgart Plant", "Stuttgart", "DE", "Europe/Berlin", "METAL_FABRICATION"),
    ("Monterrey Plant", "Monterrey", "MX", "America/Monterrey", "FOOD_PROCESSING"),
    ("Columbus Plant", "Columbus", "US", "America/New_York", "CHEMICAL"),
    ("Gdansk Plant", "Gdansk", "PL", "Europe/Warsaw", "AUTOMOTIVE"),
    ("Osaka Plant", "Osaka", "JP", "Asia/Tokyo", "METAL_FABRICATION"),
]
MACHINE_TYPES = ["CNC_MILL", "HYDRAULIC_PRESS", "CONVEYOR", "ROBOT_CELL", "PACKAGING_LINE",
                 "INJECTION_MOLDER", "WELDING_STATION"]
MACHINE_TYPE_WEIGHTS = [0.18, 0.16, 0.14, 0.14, 0.12, 0.13, 0.13]
ZONE_TYPES = ["GUARDED_CELL", "LIGHT_CURTAIN_AREA", "PERIMETER", "PRESSURE_MAT_AREA"]

PROCESS_ALARMS = {
    "CNC_MILL": ["SPINDLE-OVERLOAD", "COOLANT-LOW", "TOOL-WEAR"],
    "HYDRAULIC_PRESS": ["HYD-PRESSURE-HIGH", "OIL-TEMP-HIGH", "STROKE-OVERRUN"],
    "CONVEYOR": ["BELT-MISALIGN", "MOTOR-OVERCURRENT", "JAM-DETECTED"],
    "ROBOT_CELL": ["JOINT-TORQUE-HIGH", "GRIPPER-FAULT", "PATH-DEVIATION"],
    "PACKAGING_LINE": ["FILM-BREAK", "SEAL-TEMP-LOW", "LABEL-MISSING"],
    "INJECTION_MOLDER": ["BARREL-TEMP-HIGH", "MOLD-PRESSURE-HIGH", "CYCLE-OVERTIME"],
    "WELDING_STATION": ["WELD-CURRENT-DEVIATION", "GAS-FLOW-LOW", "ELECTRODE-WEAR"],
}
INTERLOCK_FAULT_VARIANTS = ["FAULT", "DISCREPANCY", "RESPONSE-TIME"]
COMM_ALARMS = ["PLC-COMM-LOSS", "HMI-TIMEOUT", "SENSOR-NOISE", "NETWORK-LATENCY"]
MAINT_ALARMS = ["LUBRICATION-DUE", "FILTER-CHANGE-DUE", "CALIBRATION-DUE"]
PROCESS_SEVERITIES = ["LOW", "MEDIUM", "HIGH"]  # by alarm position j within a machine type: common ones are LOW


@dataclass
class Master:
    plants: list[dict] = field(default_factory=list)
    machines: list[dict] = field(default_factory=list)
    zones: list[dict] = field(default_factory=list)
    machine_zone: list[dict] = field(default_factory=list)
    interlocks: list[dict] = field(default_factory=list)
    severity_policy: list[dict] = field(default_factory=list)
    alarm_defs: list[dict] = field(default_factory=list)
    problem_machines: set[str] = field(default_factory=set)


def build_master(cfg) -> Master:
    m = Master()
    # --- plants ---
    for i in range(cfg.n_plants):
        name, city, cc, tz, industry = PLANT_TEMPLATES[i % len(PLANT_TEMPLATES)]
        m.plants.append(dict(plant_id=plant_id(i + 1), plant_name=name, city=city, country_code=cc, timezone=tz,
                             industry_type=industry, lifecycle_status="ACTIVE", effective_from=EFFECTIVE_FROM,
                             effective_to=None))
    # --- machines (spread evenly across plants) ---
    rng = rng_for(cfg.seed, "machines")
    per_plant = [cfg.n_machines // cfg.n_plants + (1 if i < cfg.n_machines % cfg.n_plants else 0)
                 for i in range(cfg.n_plants)]
    for p, count in zip(m.plants, per_plant):
        for n in range(1, count + 1):
            mtype = str(rng.choice(MACHINE_TYPES, p=MACHINE_TYPE_WEIGHTS))
            crit = str(rng.choice(["A", "B", "C"], p=[0.25, 0.45, 0.30]))
            commissioned = date(int(rng.integers(2015, 2025)), int(rng.integers(1, 13)), int(rng.integers(1, 29)))
            m.machines.append(dict(
                machine_id=machine_id(p["plant_id"], n), plant_id=p["plant_id"],
                machine_name=f"{mtype.replace('_', ' ').title()} {n:02d}", machine_type=mtype,
                criticality_class=crit, lifecycle_status="ACTIVE", commissioned_on=commissioned,
                effective_from=EFFECTIVE_FROM, effective_to=None))
    # --- safety zones + bridge ---
    rng = rng_for(cfg.seed, "zones")
    zones_by_plant: dict[str, list[dict]] = {}
    for p in m.plants:
        machines = [x for x in m.machines if x["plant_id"] == p["plant_id"]]
        nz = max(2, math.ceil(len(machines) / 3))
        zs = []
        for z in range(1, nz + 1):
            zs.append(dict(
                safety_zone_id=zone_id(p["plant_id"], z), plant_id=p["plant_id"],
                zone_name=f"Zone {chr(64 + z) if z <= 26 else z}", zone_type=ZONE_TYPES[(z - 1) % len(ZONE_TYPES)],
                risk_level=str(rng.choice(["HIGH", "MEDIUM", "LOW"], p=[0.2, 0.4, 0.4])),
                lifecycle_status="ACTIVE", effective_from=EFFECTIVE_FROM, effective_to=None))
        zones_by_plant[p["plant_id"]] = zs
        m.zones.extend(zs)
        for i, mach in enumerate(machines):
            primary = zs[i % nz]
            m.machine_zone.append(dict(machine_id=mach["machine_id"], safety_zone_id=primary["safety_zone_id"],
                                       effective_from=EFFECTIVE_FROM, effective_to=None, is_primary=True))
            if nz > 1 and rng.random() < 0.25:
                other = zs[(i + 1) % nz]
                m.machine_zone.append(dict(machine_id=mach["machine_id"], safety_zone_id=other["safety_zone_id"],
                                           effective_from=EFFECTIVE_FROM, effective_to=None, is_primary=False))
    # --- interlocks ---
    rng = rng_for(cfg.seed, "interlocks")
    primary_zone = {r["machine_id"]: r["safety_zone_id"] for r in m.machine_zone if r["is_primary"]}
    for mach in m.machines:
        mid, mtype = mach["machine_id"], mach["machine_type"]
        zone = primary_zone[mid]
        plan = [("E_STOP", True, False, zone if rng.random() < 0.5 else None)]
        if mtype != "CONVEYOR":
            plan.append(("DOOR_SWITCH", True, bool(rng.random() < 0.3), zone))
        if mtype in ("HYDRAULIC_PRESS", "ROBOT_CELL", "INJECTION_MOLDER", "WELDING_STATION"):
            plan.append(("GUARD_LOCK", True, False, zone))
        if mtype in ("ROBOT_CELL", "PACKAGING_LINE", "CONVEYOR"):
            plan.append(("LIGHT_CURTAIN", True, bool(rng.random() < 0.2), zone))
        if mtype in ("HYDRAULIC_PRESS", "ROBOT_CELL"):
            plan.append(("PRESSURE_MAT", False, bool(rng.random() < 0.2), zone))
        for n, (itype, required, bypass, z) in enumerate(plan, start=1):
            m.interlocks.append(dict(
                interlock_id=interlock_id(mid, n), machine_id=mid, safety_zone_id=z, interlock_type=itype,
                is_required=required, bypass_allowed=bypass, lifecycle_status="ACTIVE",
                effective_from=EFFECTIVE_FROM, effective_to=None))
    # --- problem machines: highest-criticality machines first, then by id (deterministic) ---
    ranked = sorted(m.machines, key=lambda x: (x["criticality_class"], x["machine_id"]))
    m.problem_machines = {x["machine_id"] for x in ranked[:cfg.n_problem_machines]}
    # --- severity policy (from the rules config) ---
    for sev, pol in DEFAULT_RULES.severity_policy.items():
        m.severity_policy.append(dict(severity=sev, priority_rank=pol.rank,
                                      ack_deadline_seconds=pol.ack_deadline_seconds,
                                      escalation_after_seconds=pol.escalation_after_seconds,
                                      effective_from=EFFECTIVE_FROM, effective_to=None))
    m.alarm_defs = build_alarm_defs(cfg)
    return m


def _round_items():
    """Ordered (round, spec) templates; round 0 and 1 give full coverage first (important for small starters)."""
    safety = [("DOOR-OPEN-RUN", "Safety door open while running", "SAFETY_STATE", "DOOR_SWITCH", "HIGH", True, None),
              ("INTERLOCK-INACTIVE-RUN", "Required interlock inactive while running", "SAFETY_STATE", None, "HIGH", True, None),
              ("INTERLOCK-BYPASS-RUN", "Required interlock bypassed while running", "SAFETY_STATE", None, "HIGH", True, None)]
    comm = [(c, c.replace("-", " ").title(), "COMMUNICATION", None, "LOW" if i % 2 else "MEDIUM", False, None)
            for i, c in enumerate(COMM_ALARMS)]
    maint = [(c, c.replace("-", " ").title(), "MAINTENANCE_DUE", None, "LOW", False, None) for c in MAINT_ALARMS]
    types = ["DOOR_SWITCH", "GUARD_LOCK", "E_STOP", "LIGHT_CURTAIN", "PRESSURE_MAT"]
    ilk = {v: [(f"{t.replace('_', '-')}-{v}", f"{t.replace('_', ' ').title()} {v.replace('-', ' ').lower()}", "INTERLOCK_FAULT", t,
                "HIGH" if t == "E_STOP" and v == "FAULT" else ("MEDIUM" if v != "RESPONSE-TIME" else "LOW"), True, None) for t in types]
           for v in INTERLOCK_FAULT_VARIANTS}
    proc = {k: [(f"{mt.split('_')[0]}-{name}", f"{mt.replace('_', ' ').title()} {name.replace('-', ' ').lower()}",
                 "PROCESS_LIMIT", None, PROCESS_SEVERITIES[j], False, mt)
                for j, name in enumerate(PROCESS_ALARMS[mt])]
            for i, mt in enumerate(MACHINE_TYPES) for k in [mt]}
    round0 = safety + comm + maint + [proc[mt][0] for mt in MACHINE_TYPES] + ilk["FAULT"]
    round1 = [x for mt in MACHINE_TYPES for x in proc[mt][1:]] + ilk["DISCREPANCY"] + ilk["RESPONSE-TIME"]
    return round0, round1


def build_alarm_defs(cfg) -> list[dict]:
    rng = rng_for(cfg.seed, "defs")
    round0, round1 = _round_items()
    base = round0 + round1
    specs = list(base)
    k = 2
    sev_order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    while len(specs) < cfg.n_alarm_defs:
        for code, name, trig, itype, sev, safety, mtype in base:
            if len(specs) >= cfg.n_alarm_defs:
                break
            if trig == "SAFETY_STATE":  # state-driven rules are unique; do not duplicate
                continue
            shift = int(rng.integers(-1, 1))
            nsev = sev_order[max(0, min(3, sev_order.index(sev) + shift))]
            specs.append((f"{code}-V{k:02d}", f"{name} (variant {k})", trig, itype, nsev, safety, mtype))
        k += 1
    defs = []
    for n, (code, name, trig, itype, sev, safety, mtype) in enumerate(specs[:cfg.n_alarm_defs], start=1):
        deprecated = n > 60 and n % 40 == 0  # a few retired definitions that must never fire
        defs.append(dict(
            alarm_definition_id=f"ADEF-{n:04d}", alarm_code=code, alarm_name=name,
            description=f"{name}. Trigger: {trig}.", machine_type=mtype, trigger_type=trig, interlock_type=itype,
            default_severity=sev, is_safety_related=safety,
            lifecycle_status="DEPRECATED" if deprecated else "ACTIVE", effective_from=EFFECTIVE_FROM,
            effective_to=date(2025, 12, 31) if deprecated else None))
    return defs
