#!/usr/bin/env python3
"""Batch single-task downtime comparison with rescheduling.

Workflow per scenario:
1) Generate one scenario.
2) Build flexibility, earliest completion time, makespan, and slack schedules.
3) Pick one reference task from flexibility and create one targeted downtime.
4) Apply one equivalent downtime event to each objective using rescheduling.
5) Record dropped task counts and aggregate totals across many scenarios.
"""

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scenario_generator import generate_scenario
from tds_slack.executer import run_scheduler, send_events


OBJECTIVE_MINIMIZE = {
    "flexibility": False,
    "earliest_completion_time": False,
    "makespan": True,
    "slack": False,
}

OBJECTIVES = ["flexibility", "earliest_completion_time", "makespan", "slack"]


def parse_int_list(value: str) -> list[int]:
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def build_ratio_pairs(task_counts, resource_counts, min_ratio, max_ratio=None):
    pairs = []
    for n_tasks in sorted(set(task_counts)):
        for n_resources in sorted(set(resource_counts)):
            if n_resources <= 0:
                continue
            ratio = n_tasks / n_resources
            if ratio <= min_ratio:
                continue
            if max_ratio is not None and ratio > max_ratio:
                continue
            pairs.append((n_tasks, n_resources, ratio))
    pairs.sort(key=lambda x: (x[2], x[0], x[1]))
    return pairs


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Run many single-task downtime scenarios with rescheduling for flexibility "
            "vs earliest completion time vs makespan vs slack."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Batch controls
    p.add_argument("--num-scenarios", type=int, default=100)
    p.add_argument("--seed-start", type=int, default=4000)
    p.add_argument(
        "--mode",
        type=str,
        choices=["single", "ratio_sweep"],
        default="single",
        help="single: run one task/resource pair; ratio_sweep: run all eligible pairs",
    )

    # Scenario settings
    p.add_argument("--n-resources", type=int, default=4)
    p.add_argument("--n-tasks", type=int, default=24)
    p.add_argument("--n-locations", type=int, default=5)
    p.add_argument("--min-caps", type=int, default=1)
    p.add_argument("--max-caps", type=int, default=5)
    p.add_argument("--min-duration", type=int, default=15)
    p.add_argument("--max-duration", type=int, default=100)
    p.add_argument("--min-slack", type=int, default=0)
    p.add_argument("--max-slack", type=int, default=700)
    p.add_argument("--horizon", type=int, default=1440)
    p.add_argument("--capability-overlap", type=float, default=0.7)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)

    # Ratio sweep settings (used when --mode ratio_sweep)
    p.add_argument(
        "--task-counts",
        type=parse_int_list,
        default=[16, 18, 20, 22, 24, 26, 28, 30],
        help="Candidate task counts for ratio sweep",
    )
    p.add_argument(
        "--resource-counts",
        type=parse_int_list,
        default=[4, 5, 6],
        help="Candidate resource counts for ratio sweep",
    )
    p.add_argument(
        "--min-ratio",
        type=float,
        default=3.5,
        help="Include only pairs where n_tasks / n_resources is strictly greater than this value",
    )
    p.add_argument(
        "--max-ratio",
        type=float,
        default=None,
        help="Optional upper bound for n_tasks / n_resources",
    )

    # Downtime target settings
    p.add_argument(
        "--task-selection",
        type=str,
        choices=["first", "random"],
        default="first",
        help="How to pick the one task to disrupt in each scenario",
    )
    p.add_argument(
        "--full-horizon-downtime",
        action="store_true",
        help="Make the downtime span the interior of the schedule (start=1, end=horizon-1) instead of sampling a shorter event.",
    )

    # Output
    p.add_argument("--output-dir", type=str, default="./scenario_sweeps")
    p.add_argument("--results-csv", type=str, default="single_task_downtime_reschedule_results.csv")
    p.add_argument("--summary-csv", type=str, default="single_task_downtime_reschedule_summary.csv")
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
    stem = candidate.stem
    suffix = candidate.suffix
    return base_dir / f"{stem}_{timestamp}{suffix}"


def collect_scheduled_tasks(tds):
    """Collect scheduled tasks excluding header/footer/downtime/travel tasks."""
    candidates = []
    for resource in tds.resources.values():
        for task in resource.timeline.tasks:
            if (
                "header" not in task.name
                and "footer" not in task.name
                and "downtime" not in task.name
                and not task.name.startswith("travel")
            ):
                candidates.append((task, resource.name))

    candidates.sort(key=lambda x: (x[1], int(np.abs(x[0].start.lb)), x[0].name))
    return candidates


def get_task_duration(task):
    """Resolve a positive integer task duration from metadata or bounds."""
    task_duration = task.get_duration()
    if task_duration is None or int(task_duration) <= 0:
        task_start = int(np.abs(task.start.lb))
        task_end_lb = int(np.abs(task.end.lb))
        return max(1, task_end_lb - task_start)
    return int(task_duration + 50)


def sample_downtime_duration(task_duration, rng):
    """Sample downtime as task duration + random integer in [0, task duration]."""
    task_duration = max(1, int(task_duration))
    random_extension = int(rng.integers(0, task_duration + 1))
    return task_duration + random_extension


def get_resource_header_location(tds, resource_name, fallback_location):
    """Return the location of the resource header task when available."""
    resource = tds.resources.get(resource_name)
    if resource is None:
        return str(fallback_location)

    for timeline_task in resource.timeline.tasks:
        if "header" in timeline_task.name and timeline_task.locations:
            return str(timeline_task.locations[0])

    return str(fallback_location)


def create_downtime_for_task(
    task,
    resource_name,
    duration_override=None,
    full_horizon=False,
    horizon=None,
    location_override=None,
):
    task_start = 0 if full_horizon else int(np.abs(task.start.lb))
    if full_horizon:
        if horizon is None:
            raise ValueError("horizon must be provided when full_horizon is True")
        if int(horizon) < 2:
            raise ValueError("horizon must be at least 2 when full_horizon is True")
        task_start = 1
        duration = int(horizon) - 2
    else:
        duration = int(duration_override) if duration_override is not None else get_task_duration(task)
    location = location_override if location_override is not None else (task.locations[0] if task.locations else "0")

    return {
        "resource": resource_name,
        "start_time": task_start,
        "end_time": task_start + duration,
        "duration": duration,
        "location": str(location),
    }


def pick_target_task(candidates, strategy, seed):
    if not candidates:
        return None
    if strategy == "first":
        return candidates[0]

    rng = np.random.default_rng(seed)
    idx = int(rng.integers(0, len(candidates)))
    return candidates[idx]


def find_task_by_name(candidates, task_name):
    for task, resource_name in candidates:
        if task.name == task_name:
            return task, resource_name
    return None


def run_objective_with_single_downtime(tds, objective, event):
    """Apply one downtime with rescheduling and return dropped-task metrics."""
    initial_makespan = tds.makespan()
    initial_earliest_completion_time = tds.sum_completion_time_diff()
    unscheduled_tasks = send_events(
        tds,
        pd.DataFrame([event]),
        objective_metric=objective,
        minimize=OBJECTIVE_MINIMIZE[objective],
    )
    final_makespan = tds.makespan()
    dropped_names = sorted({task.name for task in unscheduled_tasks})
    return {
        "initial_makespan": initial_makespan,
        "initial_earliest_completion_time": initial_earliest_completion_time,
        "final_makespan": final_makespan,
        "makespan_change": final_makespan - initial_makespan,
        "dropped_task_count": len(dropped_names),
        "dropped_tasks": ";".join(dropped_names),
    }


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "single":
        ratio_pairs = [
            (
                args.n_tasks,
                args.n_resources,
                args.n_tasks / args.n_resources if args.n_resources else float("inf"),
            )
        ]
    else:
        ratio_pairs = build_ratio_pairs(
            args.task_counts,
            args.resource_counts,
            args.min_ratio,
            args.max_ratio,
        )
        if not ratio_pairs:
            raise ValueError(
                "No task/resource pairs satisfy the selected ratio constraints. "
                "Adjust --task-counts/--resource-counts/--min-ratio/--max-ratio."
            )

    print("=" * 90)
    print("BATCH SINGLE TASK DOWNTIME COMPARISON (RESCHEDULING)")
    print("=" * 90)
    print(f"Mode: {args.mode}")
    print(f"Scenarios per pair: {args.num_scenarios}")
    print(f"Seed start: {args.seed_start}")
    print(f"Full horizon downtime: {args.full_horizon_downtime}")
    if args.mode == "single":
        n_tasks, n_resources, ratio = ratio_pairs[0]
        print(f"Pair: {n_tasks}/{n_resources} (ratio={ratio:.3f})")
    else:
        print(f"Ratio filter: > {args.min_ratio}" + (f", <= {args.max_ratio}" if args.max_ratio is not None else ""))
        print(f"Eligible pairs ({len(ratio_pairs)}):")
        for n_tasks, n_resources, ratio in ratio_pairs:
            print(f"  - {n_tasks}/{n_resources} (ratio={ratio:.3f})")

    rows = []
    seed = args.seed_start
    for n_tasks, n_resources, ratio in ratio_pairs:
        pair_label = f"{n_tasks}/{n_resources}"
        print(f"\nProcessing pair: {pair_label} (ratio={ratio:.3f})")

        for scenario_idx in range(args.num_scenarios):
            current_seed = seed
            seed += 1
            if (scenario_idx + 1) % 25 == 0 or scenario_idx == 0:
                print(
                    f"  Scenario {scenario_idx + 1}/{args.num_scenarios} "
                    f"(seed={current_seed})"
                )

            request, travel_matrix, _ = generate_scenario(
                n_resources=n_resources,
                n_tasks=n_tasks,
            n_locations=args.n_locations,
            caps_range=(args.min_caps, args.max_caps),
            task_duration_range=(args.min_duration, args.max_duration),
            due_date_slack_range=(args.min_slack, args.max_slack),
            horizon=args.horizon,
            downtime_prob=0.0,
            capability_overlap=args.capability_overlap,
            travel_time_range=(args.min_travel, args.max_travel),
            future_downtime_count=0,
                seed=current_seed,
            )

            tds_by_objective = {}
            for objective in OBJECTIVES:
                tds_by_objective[objective] = run_scheduler(
                    copy.deepcopy(request),
                    copy.deepcopy(travel_matrix),
                    objective_metric=objective,
                    minimize=OBJECTIVE_MINIMIZE[objective],
                )

            flex_tds = tds_by_objective["flexibility"]
            flex_candidates = collect_scheduled_tasks(flex_tds)
            flex_target = pick_target_task(flex_candidates, args.task_selection, current_seed)

            if flex_target is None:
                # No target tasks in this scenario for any objective.
                rows.append(
                    {
                        "pair": pair_label,
                        "scenario_idx": scenario_idx,
                        "seed": current_seed,
                        "objective": "flexibility",
                        "n_tasks": n_tasks,
                        "n_resources": n_resources,
                        "ratio": ratio,
                        "reference_task": None,
                        "target_task": None,
                        "target_resource": None,
                        "downtime_start": None,
                        "downtime_end": None,
                        "downtime_duration": None,
                        "target_missing": True,
                        "initial_makespan": tds_by_objective["flexibility"].makespan(),
                        "initial_earliest_completion_time": tds_by_objective["flexibility"].sum_completion_time_diff(),
                        "final_makespan": tds_by_objective["flexibility"].makespan(),
                        "makespan_change": 0,
                        "dropped_task_count": 0,
                        "dropped_tasks": "",
                    }
                )
                for objective in [obj for obj in OBJECTIVES if obj != "flexibility"]:
                    rows.append(
                        {
                            "pair": pair_label,
                            "scenario_idx": scenario_idx,
                            "seed": current_seed,
                            "objective": objective,
                            "n_tasks": n_tasks,
                            "n_resources": n_resources,
                            "ratio": ratio,
                            "reference_task": None,
                            "target_task": None,
                            "target_resource": None,
                            "downtime_start": None,
                            "downtime_end": None,
                            "downtime_duration": None,
                            "target_missing": True,
                            "initial_makespan": tds_by_objective[objective].makespan(),
                            "initial_earliest_completion_time": tds_by_objective[objective].sum_completion_time_diff(),
                            "final_makespan": tds_by_objective[objective].makespan(),
                            "makespan_change": 0,
                            "dropped_task_count": 0,
                            "dropped_tasks": "",
                        }
                    )
                continue

            flex_task, flex_resource = flex_target
            reference_task_name = flex_task.name
            reference_task_duration = get_task_duration(flex_task)
            scenario_rng = np.random.default_rng(current_seed)
            reference_duration = sample_downtime_duration(reference_task_duration, scenario_rng)

            # Apply the same sampled downtime duration to all objectives.
            flex_event = create_downtime_for_task(
                flex_task,
                flex_resource,
                duration_override=reference_duration,
                full_horizon=args.full_horizon_downtime,
                horizon=args.horizon,
                location_override=get_resource_header_location(
                    flex_tds,
                    flex_resource,
                    flex_task.locations[0] if flex_task.locations else "0",
                ),
            )
            flex_metrics = run_objective_with_single_downtime(flex_tds, "flexibility", flex_event)
            rows.append(
                {
                    "pair": pair_label,
                    "scenario_idx": scenario_idx,
                    "seed": current_seed,
                    "objective": "flexibility",
                    "n_tasks": n_tasks,
                    "n_resources": n_resources,
                    "ratio": ratio,
                    "reference_task": reference_task_name,
                    "target_task": reference_task_name,
                    "target_resource": flex_resource,
                    "downtime_start": flex_event["start_time"],
                    "downtime_end": flex_event["end_time"],
                    "downtime_duration": flex_event["duration"],
                    "target_missing": False,
                    **flex_metrics,
                }
            )

            # Apply same task name + same duration to other objectives.
            for objective in [obj for obj in OBJECTIVES if obj != "flexibility"]:
                objective_tds = tds_by_objective[objective]
                objective_candidates = collect_scheduled_tasks(objective_tds)
                objective_target = find_task_by_name(objective_candidates, reference_task_name)

                if objective_target is None:
                    initial_makespan = objective_tds.makespan()
                    rows.append(
                        {
                            "pair": pair_label,
                            "scenario_idx": scenario_idx,
                            "seed": current_seed,
                            "objective": objective,
                            "n_tasks": n_tasks,
                            "n_resources": n_resources,
                            "ratio": ratio,
                            "reference_task": reference_task_name,
                            "target_task": None,
                            "target_resource": None,
                            "downtime_start": None,
                            "downtime_end": None,
                            "downtime_duration": reference_duration,
                            "target_missing": True,
                            "initial_makespan": initial_makespan,
                            "final_makespan": initial_makespan,
                            "makespan_change": 0,
                            "dropped_task_count": 0,
                            "dropped_tasks": "",
                        }
                    )
                    continue

                objective_task, objective_resource = objective_target
                objective_event = create_downtime_for_task(
                    objective_task,
                    objective_resource,
                    duration_override=reference_duration,
                    full_horizon=args.full_horizon_downtime,
                    horizon=args.horizon,
                    location_override=get_resource_header_location(
                        objective_tds,
                        objective_resource,
                        objective_task.locations[0] if objective_task.locations else "0",
                    ),
                )
                objective_metrics = run_objective_with_single_downtime(
                    objective_tds,
                    objective,
                    objective_event,
                )
                rows.append(
                    {
                        "pair": pair_label,
                        "scenario_idx": scenario_idx,
                        "seed": current_seed,
                        "objective": objective,
                        "n_tasks": n_tasks,
                        "n_resources": n_resources,
                        "ratio": ratio,
                        "reference_task": reference_task_name,
                        "target_task": reference_task_name,
                        "target_resource": objective_resource,
                        "downtime_start": objective_event["start_time"],
                        "downtime_end": objective_event["end_time"],
                        "downtime_duration": objective_event["duration"],
                        "target_missing": False,
                        **objective_metrics,
                    }
                )

    results_df = pd.DataFrame(rows)
    results_path = resolve_output_path(output_dir, args.results_csv, args.overwrite_existing)
    results_df.to_csv(results_path, index=False)

    objective_summary_df = (
        results_df.groupby("objective", as_index=False)
        .agg(
            scenarios=("scenario_idx", "count"),
            missing_targets=("target_missing", "sum"),
            drop_any_count=("dropped_task_count", lambda s: int((s > 0).sum())),
            total_dropped_tasks=("dropped_task_count", "sum"),
            mean_dropped_tasks=("dropped_task_count", "mean"),
            max_dropped_tasks=("dropped_task_count", "max"),
            mean_initial_makespan=("initial_makespan", "mean"),
            mean_initial_earliest_completion_time=("initial_earliest_completion_time", "mean"),
            mean_final_makespan=("final_makespan", "mean"),
            mean_makespan_change=("makespan_change", "mean"),
        )
    )
    objective_summary_df["drop_any_rate"] = objective_summary_df["drop_any_count"] / objective_summary_df["scenarios"]
    objective_summary_df["summary_scope"] = "all"

    pair_summary_df = (
        results_df.groupby(["pair", "ratio", "objective"], as_index=False)
        .agg(
            scenarios=("scenario_idx", "count"),
            missing_targets=("target_missing", "sum"),
            drop_any_count=("dropped_task_count", lambda s: int((s > 0).sum())),
            total_dropped_tasks=("dropped_task_count", "sum"),
            mean_dropped_tasks=("dropped_task_count", "mean"),
            max_dropped_tasks=("dropped_task_count", "max"),
            mean_initial_makespan=("initial_makespan", "mean"),
            mean_initial_earliest_completion_time=("initial_earliest_completion_time", "mean"),
            mean_final_makespan=("final_makespan", "mean"),
            mean_makespan_change=("makespan_change", "mean"),
        )
    )
    pair_summary_df["drop_any_rate"] = pair_summary_df["drop_any_count"] / pair_summary_df["scenarios"]
    pair_summary_df["summary_scope"] = "pair"

    objective_summary_df["pair"] = "all"
    objective_summary_df["ratio"] = np.nan

    summary_df = pd.concat([objective_summary_df, pair_summary_df], ignore_index=True, sort=False)
    summary_df = summary_df[
        [
            "summary_scope",
            "pair",
            "ratio",
            "objective",
            "scenarios",
            "missing_targets",
            "drop_any_count",
            "drop_any_rate",
            "total_dropped_tasks",
            "mean_dropped_tasks",
            "max_dropped_tasks",
            "mean_initial_makespan",
            "mean_initial_earliest_completion_time",
            "mean_final_makespan",
            "mean_makespan_change",
        ]
    ]
    summary_df = summary_df.sort_values(["summary_scope", "ratio", "pair", "objective"], na_position="first")

    summary_path = resolve_output_path(output_dir, args.summary_csv, args.overwrite_existing)
    summary_df.to_csv(summary_path, index=False)

    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(summary_df[summary_df["summary_scope"] == "all"].to_string(index=False))
    if args.mode == "ratio_sweep":
        print("\nPAIR-LEVEL SUMMARY")
        print(summary_df[summary_df["summary_scope"] == "pair"].to_string(index=False))
    print(f"\nDetailed results saved: {results_path}")
    print(f"Summary saved: {summary_path}")


if __name__ == "__main__":
    main()
