"""Generator parameters. Starter and full datasets share the same generation logic; only these numbers differ."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20261022
WINDOW_START = datetime(2026, 10, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = DEFAULT_SEED
    n_plants: int = 4
    n_machines: int = 60
    n_alarm_defs: int = 200
    n_alarm_events: int = 50_000      # baseline alarm events (before any injected anomalies)
    days: int = 30
    start: datetime = WINDOW_START
    n_floods: int = 25
    n_nuisance_clusters: int = 30
    n_problem_machines: int = 6       # machines with a high rate of repeated unsafe excursions
    base_unsafe_prob: float = 0.004   # per RUNNING segment
    problem_unsafe_prob: float = 0.03
    operators_per_plant: int = 14
    engineers_per_plant: int = 5
    # injected anomalies (Milestone 5): counts per scenario
    n_ack_before_alarm: int = 25
    n_impossible_interlock: int = 20
    n_incident_no_context: int = 12
    n_replay_floods: int = 4
    n_action_closed_unverified: int = 12

    @property
    def end(self) -> datetime:
        return self.start + timedelta(days=self.days)


FULL = GeneratorConfig()
STARTER = GeneratorConfig(n_plants=2, n_machines=8, n_alarm_defs=40, n_alarm_events=2_000, days=7,
                          n_floods=2, n_nuisance_clusters=3, n_problem_machines=1,
                          base_unsafe_prob=0.02, problem_unsafe_prob=0.12,
                          operators_per_plant=8, engineers_per_plant=3,
                          n_ack_before_alarm=4, n_impossible_interlock=4, n_incident_no_context=3,
                          n_replay_floods=1, n_action_closed_unverified=3)

PRESETS = {"starter": STARTER, "full": FULL}


def from_scale(scale: str, seed: int | None = None) -> GeneratorConfig:
    cfg = PRESETS[scale]
    return replace(cfg, seed=seed) if seed is not None else cfg
