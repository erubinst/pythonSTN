#!/usr/bin/env python3
"""
Generate scenarios and create task-based downtimes with rescheduling.
Uses the flexibility schedule's initial tasks as the reference target set,
then applies those same downtimes to both flexibility and makespan objectives.
This ensures both objectives are tested with identical downtime sequences.
Tasks are selected dynamically after each downtime to account for schedule changes.
"""

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scenario_generator import generate_scenario
from tds_slack.executer import run_scheduler, send_events


OBJECTIVE_MINIMIZE = {
    "travel": True,
    "makespan": True,
    "flexibility": False,
    "slots": False,
    "total_slack": False,
    "slack": False,
    "max_slot": False,
    "earliest_completion_time": False,
}


def parse_int_list(value: str) -> list[int]:
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def normalize_objective(name: str) -> str:
    """Normalize objective aliases to scheduler metric names."""
    name = name.strip().lower()
    if name == "slot":
        return "slots"
    if name == "max slot":
        return "max_slot"
    if name == "earliest completion time":
        return "earliest_completion_time"
    return name


def objective_should_minimize(objective_metric: str) -> bool:
    return OBJECTIVE_MINIMIZE.get(objective_metric, False)


def parse_args():
    p = argparse.ArgumentParser(
        description="Generate task-based downtimes and compare objective robustness.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--output-dir", type=str, default="./scenario_sweeps",
                   help="Directory where output CSVs are written")
    p.add_argument("--results-csv", type=str, default="task_based_downtime_results.csv",
                   help="Detailed per-scenario per-objective CSV")
    p.add_argument("--summary-csv", type=str, default="task_based_downtime_summary.csv",
                   help="Objective-level summary CSV")

    p.add_argument("--seed", type=int, default=3000,
                   help="Base seed")
    p.add_argument("--scenarios-per-pair", type=int, default=100,
                   help="Number of scenarios per task/resource pair")

    p.add_argument("--objectives", type=str,
                   default="flexibility,makespan",
                   help="Comma-separated objectives to compare")

    # Pair-based sweep settings.
    p.add_argument(
        "--task-counts",
        type=parse_int_list,
        default=[5, 6, 9, 10, 12, 14, 16, 18, 20, 22, 24, 26],
        help="Candidate task counts used to build task/resource pairs",
    )
    p.add_argument(
        "--resource-counts",
        type=parse_int_list,
        default=[4, 5, 6, 10],
        help="Candidate resource counts used to build task/resource pairs",
    )
    p.add_argument(
        "--ratio-threshold",
        type=float,
        default=4.0,
        help="Only run pairs where n_tasks / n_resources is strictly greater than this value",
    )

    # Static scenario parameters
    p.add_argument("--n-locations", type=int, default=5)
    p.add_argument("--min-caps", type=int, default=1)
    p.add_argument("--max-caps", type=int, default=5)
    p.add_argument("--min-duration", type=int, default=15)
    p.add_argument("--max-duration", type=int, default=100)
    p.add_argument("--min-slack", type=int, default=0)
    p.add_argument("--max-slack", type=int, default=1440)
    p.add_argument("--horizon", type=int, default=1440)
    p.add_argument("--downtime-prob", type=float, default=0.0)
    p.add_argument("--capability-overlap", type=float, default=0.8)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)
    return p.parse_args()


def generate_scenarios_for_pair(
    static_config: dict,
    num_scenarios: int,
    seed_start: int,
    n_tasks: int,
    n_resources: int,
    pair_label: str,
    ratio: float,
) -> tuple[list[dict], int]:
    """Generate scenarios for one task/resource pair."""
    scenarios = []
    seed = seed_start

    for i in range(num_scenarios):
        request, travel_matrix, future_downtimes = generate_scenario(
            n_resources=n_resources,
            n_tasks=n_tasks,
            n_locations=static_config["n_locations"],
            caps_range=(static_config["min_caps"], static_config["max_caps"]),
            task_duration_range=(static_config["min_duration"], static_config["max_duration"]),
            due_date_slack_range=(static_config["min_slack"], static_config["max_slack"]),
            horizon=static_config["horizon"],
            downtime_prob=static_config["downtime_prob"],
            capability_overlap=static_config["capability_overlap"],
            travel_time_range=(static_config["min_travel"], static_config["max_travel"]),
            future_downtime_count=0,  # No pre-generated downtimes
            seed=seed,
        )

        scenarios.append(
            {
                "scenario_idx": len(scenarios),
                "pair": pair_label,
                "ratio": ratio,
                "seed": seed,
                "request": request,
                "travel_matrix": travel_matrix,
                "n_tasks": n_tasks,
                "n_resources": n_resources,
            }
        )
        seed += 1

    return scenarios, seed


def collect_scheduled_tasks(tds):
    """
    Collect all scheduled tasks from the schedule
    (excluding header, footer, and downtime tasks).
    Returns a list of (task, resource_name) tuples.
    """
    candidate_tasks = []

    for resource in tds.resources.values():
        for task in resource.timeline.tasks:
            # Skip header, footer, and downtime tasks
            if "header" not in task.name and "footer" not in task.name and "downtime" not in task.name:
                candidate_tasks.append((task, resource.name))

    return candidate_tasks


def create_task_based_downtime(task, resource_name) -> dict:
    """
    Create a downtime event that overlaps the given task.
    Duration is fixed to the task duration so comparable tasks get
    comparable disruptions across objectives.
    """
    # Get task timing (taking absolute value of each bound)
    task_start = int(np.abs(task.start.lb))

    task_duration = task.get_duration()
    if task_duration is None or int(task_duration) <= 0:
        # Fallback to bound-derived duration if duration metadata is unavailable.
        task_end_lb = int(np.abs(task.end.lb))
        downtime_duration = max(1, task_end_lb - task_start)
    else:
        downtime_duration = int(task_duration)
    
    # Downtime starts at task start time
    downtime_start = task_start
    downtime_end = task_start + downtime_duration
    
    # Use task's location (first location in the list is start location)
    location = task.locations[0] if task.locations else "0"
    
    return {
        "resource": resource_name,
        "start_time": downtime_start,
        "end_time": downtime_end,
        "duration": downtime_duration,
        "location": str(location),
    }


def run_objective_on_scenario(scenario: dict, objective_metric: str, reference_task_names: list = None) -> dict:
    """
    Run scheduler with objective, then generate and send task-based downtimes
    one at a time with rescheduling.
    
    If reference_task_names is provided, use those tasks as the downtime targets.
    Otherwise use tasks from this objective's initial schedule.
    
    For each target task:
    1. Find its current location in the schedule (may have moved due to previous downtimes)
    2. If it still exists, create and send a downtime
    3. Allow rescheduling to occur
    4. Repeat for next task
    
    If a task is removed from the schedule at any point, skip it.
    """
    minimize = objective_should_minimize(objective_metric)
    request = copy.deepcopy(scenario["request"])
    travel_matrix = copy.deepcopy(scenario["travel_matrix"])

    # Initial schedule
    tds = run_scheduler(
        request,
        travel_matrix,
        objective_metric=objective_metric,
        minimize=minimize,
    )

    initial_slots = tds.sum_total_slot()
    initial_makespan = tds.makespan()
    initial_flexibility = tds.sum_total_flexibility()

    # Identify which tasks to target for downtimes
    if reference_task_names is None:
        # Use this objective's initial scheduled tasks
        initial_scheduled_tasks = collect_scheduled_tasks(tds)
        target_task_names = [task.name for task, _ in initial_scheduled_tasks]
    else:
        # Use provided reference task names (e.g., from flexibility schedule)
        target_task_names = reference_task_names

    if not target_task_names:
        # No tasks to disrupt; return neutral metrics
        return {
            "initial_slots": initial_slots,
            "final_slots": initial_slots,
            "initial_makespan": initial_makespan,
            "final_makespan": initial_makespan,
            "initial_flexibility": initial_flexibility,
            "slot_degradation": 0,
            "makespan_change": 0,
            "dropped_task_count": 0,
            "dropped_any": False,
            "removed_task_count": 0,
            "task_selected": None,
            "downtime_start": None,
            "downtime_end": None,
        }

    # Send downtimes one at a time, selecting task time after each reschedule
    total_unscheduled = 0
    unscheduled_task_names = []
    num_downtimes_sent = 0

    for target_task_name in target_task_names:
        # Find the task in current schedule
        current_tasks = collect_scheduled_tasks(tds)
        current_task_pair = None

        for task, resource_name in current_tasks:
            if task.name == target_task_name:
                current_task_pair = (task, resource_name)
                break

        if current_task_pair is None:
            # Task has been removed from schedule; skip it
            continue

        task, resource_name = current_task_pair
        downtime_event = create_task_based_downtime(task, resource_name)

        # Send single downtime with rescheduling
        unscheduled_tasks = send_events(
            tds,
            pd.DataFrame([downtime_event]),
            objective_metric=objective_metric,
            minimize=minimize
        )

        num_downtimes_sent += 1
        total_unscheduled += len(unscheduled_tasks)
        for t in unscheduled_tasks:
            if t.name not in unscheduled_task_names:
                unscheduled_task_names.append(t.name)

    final_slots = tds.sum_total_slot()
    final_makespan = tds.makespan()
    num_unscheduled = len(set(unscheduled_task_names))

    return {
        "initial_slots": initial_slots,
        "final_slots": final_slots,
        "initial_makespan": initial_makespan,
        "final_makespan": final_makespan,
        "initial_flexibility": initial_flexibility,
        "slot_degradation": final_slots - initial_slots,
        "makespan_change": final_makespan - initial_makespan,
        "dropped_task_count": num_unscheduled,
        "dropped_any": num_unscheduled > 0,
        "removed_task_count": total_unscheduled,
        "num_downtimes_sent": num_downtimes_sent,
        "task_selected": ";".join(target_task_names),
        "downtime_start": None,
        "downtime_end": None,
    }


def main():
    args = parse_args()

    static_config = {
        "n_locations": args.n_locations,
        "min_caps": args.min_caps,
        "max_caps": args.max_caps,
        "min_duration": args.min_duration,
        "max_duration": args.max_duration,
        "min_slack": args.min_slack,
        "max_slack": args.max_slack,
        "horizon": args.horizon,
        "downtime_prob": args.downtime_prob,
        "capability_overlap": args.capability_overlap,
        "min_travel": args.min_travel,
        "max_travel": args.max_travel,
    }

    objectives = [normalize_objective(o) for o in args.objectives.split(",")]
    if set(objectives) != {"flexibility", "makespan"}:
        raise ValueError("This script only supports objectives: flexibility,makespan")

    ratio_pairs = []
    for n_tasks in sorted(set(args.task_counts)):
        for n_resources in sorted(set(args.resource_counts)):
            if n_resources <= 0:
                continue
            ratio = n_tasks / n_resources
            if ratio > args.ratio_threshold:
                ratio_pairs.append((n_tasks, n_resources, ratio))

    ratio_pairs.sort(key=lambda x: (x[2], x[0], x[1]))
    if not ratio_pairs:
        raise ValueError(
            f"No task/resource pairs satisfy n_tasks/n_resources > {args.ratio_threshold}."
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    seed = args.seed

    print("Generating scenarios with task-based downtimes (ratio-filtered pairs)")
    print(f"Objectives: {', '.join(objectives)}")
    print(f"Ratio threshold: n_tasks/n_resources > {args.ratio_threshold}")
    print(f"Eligible pairs ({len(ratio_pairs)}):")
    for n_tasks, n_resources, ratio in ratio_pairs:
        print(f"  - {n_tasks}/{n_resources} (ratio={ratio:.3f})")
    print(f"Scenarios per pair: {args.scenarios_per_pair}")
    print(f"Total runs: {len(ratio_pairs) * len(objectives) * args.scenarios_per_pair}")
    print()

    for n_tasks, n_resources, ratio in ratio_pairs:
        pair_label = f"{n_tasks}/{n_resources}"
        print(f"Processing pair: {pair_label} (ratio={ratio:.3f})")

        scenarios, seed = generate_scenarios_for_pair(
            static_config,
            args.scenarios_per_pair,
            seed,
            n_tasks,
            n_resources,
            pair_label,
            ratio,
        )

        # Run each scenario with each objective
        for scenario_idx, scenario in enumerate(scenarios):
            if (scenario_idx + 1) % 50 == 0:
                print(f"  Scenario {scenario_idx + 1}/{len(scenarios)}")

            # First, get the reference task names from flexibility schedule
            flex_request = copy.deepcopy(scenario["request"])
            flex_travel_matrix = copy.deepcopy(scenario["travel_matrix"])
            flex_tds = run_scheduler(
                flex_request,
                flex_travel_matrix,
                objective_metric="flexibility",
                minimize=False,
            )
            flex_initial_tasks = collect_scheduled_tasks(flex_tds)
            reference_task_names = [task.name for task, _ in flex_initial_tasks]

            # Run both objectives with the same reference task names
            for objective in objectives:
                try:
                    metrics = run_objective_on_scenario(
                        scenario,
                        objective,
                        reference_task_names=reference_task_names,
                    )
                    
                    result = {
                        "scenario_idx": scenario["scenario_idx"],
                        "pair": scenario["pair"],
                        "ratio": scenario["ratio"],
                        "seed": scenario["seed"],
                        "objective": objective,
                        "n_tasks": scenario["n_tasks"],
                        "n_resources": scenario["n_resources"],
                        **metrics,
                    }
                    all_results.append(result)

                except Exception as e:
                    print(f"  ERROR in scenario {scenario_idx} with {objective}: {e}")
                    continue

    # Write detailed results
    results_df = pd.DataFrame(all_results)
    results_path = output_dir / args.results_csv
    results_df.to_csv(results_path, index=False)
    print(f"\nDetailed results written to: {results_path}")

    # Compute summaries by objective and by pair/objective.
    summary_rows = []

    # Overall summary
    for objective in objectives:
        obj_data = results_df[results_df["objective"] == objective]
        summary_rows.append({
            "group": "all",
            "objective": objective,
            "num_scenarios": len(obj_data),
            "mean_downtimes_sent": obj_data["num_downtimes_sent"].mean(),
            "drop_any_rate": obj_data["dropped_any"].mean(),
            "mean_dropped": obj_data["dropped_task_count"].mean(),
            "median_dropped": obj_data["dropped_task_count"].median(),
            "max_dropped": obj_data["dropped_task_count"].max(),
            "mean_removed_task_count": obj_data["removed_task_count"].mean(),
        })

    # Per-pair summaries
    for n_tasks, n_resources, ratio in ratio_pairs:
        pair_label = f"{n_tasks}/{n_resources}"
        pair_data = results_df[results_df["pair"] == pair_label]
        for objective in objectives:
            obj_data = pair_data[pair_data["objective"] == objective]
            summary_rows.append({
                "group": pair_label,
                "ratio": ratio,
                "objective": objective,
                "num_scenarios": len(obj_data),
                "mean_downtimes_sent": obj_data["num_downtimes_sent"].mean(),
                "drop_any_rate": obj_data["dropped_any"].mean(),
                "mean_dropped": obj_data["dropped_task_count"].mean(),
                "median_dropped": obj_data["dropped_task_count"].median(),
                "max_dropped": obj_data["dropped_task_count"].max(),
                "mean_removed_task_count": obj_data["removed_task_count"].mean(),
            })

    summary_df = pd.DataFrame(summary_rows)
    summary_path = output_dir / args.summary_csv
    summary_df.to_csv(summary_path, index=False)
    print(f"Summary written to: {summary_path}")

    # Print summary
    print("\n=== SUMMARY (Task-Based Downtimes) ===\n")
    print("Overall Results (all ratio-filtered pairs):")
    overall = summary_df[summary_df["group"] == "all"].sort_values("drop_any_rate")
    print(overall.to_string(index=False))

    print("\n\nPer-Pair Results:")
    pair_summary = summary_df[summary_df["group"] != "all"].sort_values(["ratio", "group", "objective"])
    print(pair_summary.to_string(index=False))


if __name__ == "__main__":
    main()
