#!/usr/bin/env python3
"""
Generate scenario batches across schedule fullness levels.
This script is separate from scenario_generator.py so single-scenario generation stays simple.
"""

import argparse
import os
from datetime import datetime

from scenario_generator import generate_scenario, write_scenario_files


FULLNESS_PROFILES = {
    # Original pressure-heavy profile.
    "default": {
        "level1_sparse": {
            "task_multiplier": 8,
            "task_duration_range": (15, 90),
            "due_date_slack_range": (0, 200),
            "future_downtime_count": 5,
        },
        "level2_semi_crowded": {
            "task_multiplier": 12,
            "task_duration_range": (15, 90),
            "due_date_slack_range": (0, 200),
            "future_downtime_count": 5,
        },
        "level3_very_crowded": {
            "task_multiplier": 20,
            "task_duration_range": (15, 90),
            "due_date_slack_range": (0, 200),
            "future_downtime_count": 5,
        },
    },
    # Tight-constraint profile to stress reassignment under disruption.
    "reassignment_friendly": {
        "level1_sparse": {
            "task_multiplier": 6,
            "task_duration_range": (15, 90),
            "due_date_slack_range": (0, 50),
            "future_downtime_count": 10,
        },
        "level2_semi_crowded": {
            "task_multiplier": 9,
            "task_duration_range": (15, 90),
            "due_date_slack_range": (0, 50),
            "future_downtime_count": 15,
        },
        "level3_very_crowded": {
            "task_multiplier": 12,
            "task_duration_range": (15, 90),
            "due_date_slack_range": (0, 50),
            "future_downtime_count": 25,
        },
    },
}


def parse_args():
    p = argparse.ArgumentParser(
        description="Generate 3-level fullness experiment scenarios.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--output-dir", type=str, default="./slack_scenarios",
                   help="Base directory where experiment folder is created")
    p.add_argument("--cases-per-level", type=int, default=5,
                   help="Number of cases per fullness level")
    p.add_argument("--seed", type=int, default=None,
                   help="Base seed for reproducibility")
    p.add_argument("--n-resources", type=int, default=5,
                   help="Number of resources")
    p.add_argument("--n-locations", type=int, default=5,
                   help="Number of locations")
    p.add_argument("--min-caps", type=int, default=1,
                   help="Minimum non-presence capabilities per resource")
    p.add_argument("--max-caps", type=int, default=5,
                   help="Maximum non-presence capabilities per resource")
    p.add_argument("--horizon", type=int, default=1440,
                   help="Planning horizon in minutes")
    p.add_argument("--downtime-prob", type=float, default=0.0,
                   help="Probability [0,1] that a resource has an initial downtime")
    p.add_argument("--capability-overlap", type=float, default=0.7,
                   help="Capability overlap [0,1] between resources")
    p.add_argument("--min-travel", type=int, default=5,
                   help="Minimum travel time between locations")
    p.add_argument("--max-travel", type=int, default=60,
                   help="Maximum travel time between locations")
    p.add_argument("--level-profile", type=str, default="default",
                   choices=sorted(FULLNESS_PROFILES.keys()),
                   help="Scenario profile for fullness level generation")
    return p.parse_args()


def generate_fullness_experiments(base_output_dir: str,
                                  cases_per_level: int = 5,
                                  base_seed: int | None = None,
                                  n_resources: int = 5,
                                  n_locations: int = 5,
                                  caps_range: tuple[int, int] = (1, 5),
                                  horizon: int = 1440,
                                  downtime_prob: float = 0.0,
                                  capability_overlap: float = 0.8,
                                  travel_time_range: tuple[int, int] = (5, 60),
                                  level_profile: str = "default") -> str:
    if level_profile not in FULLNESS_PROFILES:
        raise ValueError(
            f"Unknown level_profile '{level_profile}'. "
            f"Expected one of: {', '.join(sorted(FULLNESS_PROFILES.keys()))}"
        )

    levels = FULLNESS_PROFILES[level_profile]

    timestamp = datetime.now().strftime("fullness_experiments_%Y%m%d_%H%M%S")
    experiment_root = os.path.join(base_output_dir, timestamp)
    os.makedirs(experiment_root, exist_ok=False)

    level_names = list(levels.keys())
    for level_idx, level_name in enumerate(level_names):
        cfg = levels[level_name]
        level_dir = os.path.join(experiment_root, level_name)
        os.makedirs(level_dir, exist_ok=True)

        n_tasks = max(1, n_resources * cfg["task_multiplier"])
        for case_idx in range(1, cases_per_level + 1):
            case_seed = None
            if base_seed is not None:
                case_seed = base_seed + (1000 * level_idx) + case_idx

            request, travel_matrix, future_downtimes = generate_scenario(
                n_resources=n_resources,
                n_tasks=n_tasks,
                n_locations=n_locations,
                caps_range=caps_range,
                task_duration_range=cfg["task_duration_range"],
                due_date_slack_range=cfg["due_date_slack_range"],
                horizon=horizon,
                downtime_prob=downtime_prob,
                capability_overlap=capability_overlap,
                travel_time_range=travel_time_range,
                future_downtime_count=cfg["future_downtime_count"],
                seed=case_seed,
            )

            case_dir = os.path.join(level_dir, f"case_{case_idx:02d}")
            write_scenario_files(case_dir, request, travel_matrix, future_downtimes)

    return experiment_root


def main():
    args = parse_args()

    experiment_root = generate_fullness_experiments(
        base_output_dir=args.output_dir,
        cases_per_level=args.cases_per_level,
        base_seed=args.seed,
        n_resources=args.n_resources,
        n_locations=args.n_locations,
        caps_range=(args.min_caps, args.max_caps),
        horizon=args.horizon,
        downtime_prob=args.downtime_prob,
        capability_overlap=args.capability_overlap,
        travel_time_range=(args.min_travel, args.max_travel),
        level_profile=args.level_profile,
    )

    selected_levels = FULLNESS_PROFILES[args.level_profile]
    print(f"✓ experiment folder   -> {experiment_root}")
    print(f"✓ profile selected    -> {args.level_profile}")
    print(f"✓ levels generated    -> {', '.join(selected_levels.keys())}")
    print(f"✓ cases per level     -> {args.cases_per_level}")
    print(f"✓ total scenarios     -> {args.cases_per_level * len(selected_levels)}")


if __name__ == "__main__":
    main()
