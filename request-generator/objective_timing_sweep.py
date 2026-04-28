#!/usr/bin/env python3
"""Batch timing sweep for flexibility vs makespan vs earliest completion time vs slack schedules.

Workflow per scenario:
1) Generate one scenario using the fixed target pairs from the flexibility sweep.
2) Build the flexibility schedule, the makespan schedule, and the earliest completion time schedule.
3) Time each scheduler call once per scenario/objective.
4) Save raw timings and per-objective summaries to CSV.
"""

import argparse
import copy
import math
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

import pandas as pd

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scenario_generator import generate_scenario
from tds_slack.executer import run_scheduler


OBJECTIVE_MINIMIZE = {
    "flexibility": False,
    "makespan": True,
    "earliest_completion_time": False,
    "slack": False,
}

OBJECTIVES = ["flexibility", "makespan", "earliest_completion_time", "slack"]


TARGET_PAIRS = [
    (12, 4), (16, 4), (20, 4), (24, 4), 
    (28, 4), (32, 4), (36, 4), (40, 4),
]


def parse_args():
    p = argparse.ArgumentParser(
        description="Sweep scenarios to compare flexibility, makespan, earliest completion time, and slack computation times",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Sweep parameters
    p.add_argument("--num-scenarios", type=int, default=10, help="Number of scenarios per target pair")
    p.add_argument("--seed-start", type=int, default=1000, help="Starting random seed")

    # Fixed scenario settings.
    p.add_argument("--min-caps", type=int, default=1)
    p.add_argument("--max-caps", type=int, default=5)
    p.add_argument("--min-duration", type=int, default=15)
    p.add_argument("--max-duration", type=int, default=200)
    p.add_argument("--min-slack", type=int, default=0)
    p.add_argument("--max-slack", type=int, default=800)
    p.add_argument("--horizon", type=int, default=1440)
    p.add_argument("--capability-overlap", type=float, default=0.8)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)

    # Output
    p.add_argument("--output-dir", type=str, default="./scenario_sweeps")
    p.add_argument("--results-csv", type=str, default="objective_timing_results.csv")
    p.add_argument("--summary-csv", type=str, default="objective_timing_summary.csv")
    p.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite output files if they already exist. Default behavior is to create timestamped filenames.",
    )

    return p.parse_args()


def resolve_output_path(base_dir: Path, filename: str, overwrite_existing: bool) -> Path:
    """Return a safe output path that avoids overwriting existing files by default."""
    candidate = base_dir / filename
    if overwrite_existing or not candidate.exists():
        return candidate

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / f"{candidate.stem}_{timestamp}{candidate.suffix}"


def validate_target_pairs(target_pairs):
    """Ensure target pairs have unique reduced ratios."""
    ratio_owner = {}
    for n_tasks, n_resources in target_pairs:
        d = math.gcd(n_tasks, n_resources)
        reduced = (n_tasks // d, n_resources // d)
        if reduced in ratio_owner:
            raise ValueError(
                "Duplicate task/resource ratio detected in TARGET_PAIRS: "
                f"{n_tasks}/{n_resources} duplicates {ratio_owner[reduced]}"
            )
        ratio_owner[reduced] = f"{n_tasks}/{n_resources}"


def run_objective_timing(request, travel_matrix, objective):
    """Time a single scheduler run for one objective."""
    request_copy = copy.deepcopy(request)
    travel_matrix_copy = copy.deepcopy(travel_matrix)

    start = perf_counter()
    tds = run_scheduler(
        request_copy,
        travel_matrix_copy,
        objective_metric=objective,
        minimize=OBJECTIVE_MINIMIZE[objective],
    )
    elapsed = perf_counter() - start

    return elapsed


def main():
    args = parse_args()
    validate_target_pairs(TARGET_PAIRS)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("OBJECTIVE TIMING SWEEP")
    print("=" * 100)
    print("Objectives: flexibility, makespan, earliest completion time, slack")
    print(f"Target pairs ({len(TARGET_PAIRS)}): {', '.join([f'{t}/{r}' for t, r in TARGET_PAIRS])}")
    print(f"Scenarios per pair: {args.num_scenarios}")
    print(f"Seed start: {args.seed_start}")
    print()

    all_results = []
    seed = args.seed_start
    scenario_num = 1
    total_runs = len(TARGET_PAIRS) * args.num_scenarios * len(OBJECTIVES)

    for n_tasks, n_resources in TARGET_PAIRS:
        ratio = n_tasks / n_resources if n_resources else float("inf")

        for scenario_idx in range(args.num_scenarios):
            print(
                f"[{scenario_num:4d}] Pair={n_tasks:2d}/{n_resources:<2d} ratio={ratio:.3f} "
                f"Scenario={scenario_idx + 1}/{args.num_scenarios} Seed={seed}"
            )

            request, travel_matrix, _ = generate_scenario(
                n_resources=n_resources,
                n_tasks=n_tasks,
                n_locations=3,
                caps_range=(args.min_caps, args.max_caps),
                task_duration_range=(args.min_duration, args.max_duration),
                due_date_slack_range=(args.min_slack, args.max_slack),
                horizon=args.horizon,
                downtime_prob=0.0,
                capability_overlap=args.capability_overlap,
                travel_time_range=(args.min_travel, args.max_travel),
                future_downtime_count=0,
                seed=seed,
            )

            for objective in OBJECTIVES:
                elapsed_seconds = run_objective_timing(request, travel_matrix, objective)
                all_results.append(
                    {
                        "scenario_num": scenario_num,
                        "scenario_idx": scenario_idx + 1,
                        "objective": objective,
                        "seed": seed,
                        "n_tasks": n_tasks,
                        "n_resources": n_resources,
                        "ratio": ratio,
                        "elapsed_seconds": elapsed_seconds,
                    }
                )

            seed += 1
            scenario_num += 1

    df_results = pd.DataFrame(all_results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    results_path = resolve_output_path(output_dir, args.results_csv, args.overwrite_existing)
    if not args.overwrite_existing and results_path == output_dir / args.results_csv:
        results_path = output_dir / f"{results_path.stem}_{timestamp}{results_path.suffix}"
    df_results.to_csv(results_path, index=False)
    print(f"\nDetailed results saved to: {results_path}")

    summary_rows = []
    for (n_tasks, n_resources), group in df_results.groupby(["n_tasks", "n_resources"], sort=True):
        ratio = n_tasks / n_resources if n_resources else float("inf")
        for objective, objective_group in group.groupby("objective", sort=True):
            summary_rows.append(
                {
                    "n_tasks": n_tasks,
                    "n_resources": n_resources,
                    "ratio": ratio,
                    "objective": objective,
                    "scenarios": len(objective_group),
                    "mean_elapsed_seconds": objective_group["elapsed_seconds"].mean(),
                    "median_elapsed_seconds": objective_group["elapsed_seconds"].median(),
                    "std_elapsed_seconds": objective_group["elapsed_seconds"].std(),
                    "min_elapsed_seconds": objective_group["elapsed_seconds"].min(),
                    "max_elapsed_seconds": objective_group["elapsed_seconds"].max(),
                }
            )

    df_summary = pd.DataFrame(summary_rows)
    summary_path = resolve_output_path(output_dir, args.summary_csv, args.overwrite_existing)
    if not args.overwrite_existing and summary_path == output_dir / args.summary_csv:
        summary_path = output_dir / f"{summary_path.stem}_{timestamp}{summary_path.suffix}"
    df_summary.to_csv(summary_path, index=False)
    print(f"Summary results saved to: {summary_path}")

    overall = df_results.groupby("objective")["elapsed_seconds"].agg(["count", "mean", "median", "std", "min", "max"])
    print("\nOverall runtime summary:")
    print(overall.to_string())
    print()
    print(f"Completed {len(df_results)} timing rows across {total_runs} scheduled runs.")


if __name__ == "__main__":
    main()