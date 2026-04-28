#!/usr/bin/env python3
"""Single-scenario, single-task downtime comparison for three objectives.

Workflow:
1) Generate one scenario.
2) Build three schedules (flexibility, makespan, and slack).
3) For each schedule, pick one task and inject one targeted downtime that overlaps it.
4) Report dropped task counts and alternative slots.
5) Display schedules before and after downtime.
"""

import sys
from pathlib import Path
import argparse
import copy

import numpy as np
import pandas as pd

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scenario_generator import generate_scenario
from tds_slack.executer import run_scheduler, send_events_no_reschedule
from tds_slack.utils import display_current_schedule


OBJECTIVE_MINIMIZE = {
    "flexibility": False,
    "makespan": True,
    "slack": False,
}


def parse_args():
    p = argparse.ArgumentParser(
        description="Run one scenario and compare one targeted downtime across flexibility, makespan, and slack schedules.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Scenario settings
    p.add_argument("--seed", type=int, default=4000)
    p.add_argument("--n-resources", type=int, default=4)
    p.add_argument("--n-tasks", type=int, default=20)
    p.add_argument("--n-locations", type=int, default=5)
    p.add_argument("--min-caps", type=int, default=1)
    p.add_argument("--max-caps", type=int, default=5)
    p.add_argument("--min-duration", type=int, default=15)
    p.add_argument("--max-duration", type=int, default=100)
    p.add_argument("--min-slack", type=int, default=0)
    p.add_argument("--max-slack", type=int, default=1440)
    p.add_argument("--horizon", type=int, default=1440)
    p.add_argument("--capability-overlap", type=float, default=0.8)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)

    # Downtime target settings
    p.add_argument(
        "--task-selection",
        type=str,
        choices=["first", "random"],
        default="first",
        help="How to pick the one task to disrupt in each schedule",
    )
    p.add_argument(
        "--show-plots",
        action="store_true",
        help="Display schedule plots before and after downtime for all objectives",
    )

    # Output
    p.add_argument("--output-dir", type=str, default="./scenario_sweeps")
    p.add_argument("--results-csv", type=str, default="single_task_downtime_comparison.csv")

    return p.parse_args()


def collect_scheduled_tasks(tds):
    """Collect scheduled tasks excluding header/footer/downtime tasks."""
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

    # Sort to make 'first' deterministic across runs.
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


def create_downtime_for_task(task, resource_name, duration_override=None):
    task_start = int(np.abs(task.start.lb))
    duration = int(duration_override) if duration_override is not None else get_task_duration(task)

    location = task.locations[0] if task.locations else "0"
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
    """Find task/resource tuple by task name in candidate list."""
    for task, resource_name in candidates:
        if task.name == task_name:
            return task, resource_name
    return None


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ratio = args.n_tasks / args.n_resources if args.n_resources else float("inf")
    print("=" * 90)
    print("SINGLE TASK DOWNTIME COMPARISON")
    print("=" * 90)
    print(f"Scenario: tasks={args.n_tasks}, resources={args.n_resources}, ratio={ratio:.3f}, seed={args.seed}")

    request, travel_matrix, _ = generate_scenario(
        n_resources=args.n_resources,
        n_tasks=args.n_tasks,
        n_locations=args.n_locations,
        caps_range=(args.min_caps, args.max_caps),
        task_duration_range=(args.min_duration, args.max_duration),
        due_date_slack_range=(args.min_slack, args.max_slack),
        horizon=args.horizon,
        downtime_prob=0.0,
        capability_overlap=args.capability_overlap,
        travel_time_range=(args.min_travel, args.max_travel),
        future_downtime_count=0,
        seed=args.seed,
    )

    rows = []

    # Build all schedules first from the same generated scenario.
    tds_by_objective = {}
    for objective in ["flexibility", "makespan", "slack"]:
        tds_by_objective[objective] = run_scheduler(
            copy.deepcopy(request),
            copy.deepcopy(travel_matrix),
            objective_metric=objective,
            minimize=OBJECTIVE_MINIMIZE[objective],
        )

    # Flexibility schedule is the reference for task choice and downtime duration.
    print("\n" + "-" * 70)
    print("Objective: flexibility (reference)")
    flex_tds = tds_by_objective["flexibility"]
    if args.show_plots:
        print("Showing schedule BEFORE downtime: flexibility")
        display_current_schedule(flex_tds)

    flex_candidates = collect_scheduled_tasks(flex_tds)
    flex_target = pick_target_task(flex_candidates, args.task_selection, args.seed)
    if flex_target is None:
        raise RuntimeError("No scheduled tasks found in flexibility schedule for downtime injection.")

    flex_task, flex_resource = flex_target
    reference_task_name = flex_task.name
    reference_duration = get_task_duration(flex_task)

    flex_event = create_downtime_for_task(flex_task, flex_resource, duration_override=reference_duration)
    flex_unscheduled_tasks, flex_alternative_slots, flex_removed_task_count = send_events_no_reschedule(
        flex_tds,
        pd.DataFrame([flex_event]),
        objective_metric="flexibility",
    )
    flex_dropped_names = [t.name for t in flex_unscheduled_tasks]

    print(
        f"Reference task={reference_task_name} on {flex_resource}, "
        f"downtime=[{flex_event['start_time']}, {flex_event['end_time']}], "
        f"duration={reference_duration}, "
        f"alternative_slots={flex_alternative_slots}, removed={flex_removed_task_count}, "
        f"dropped={len(flex_dropped_names)}"
    )

    if args.show_plots:
        print("Showing schedule AFTER downtime: flexibility")
        display_current_schedule(flex_tds)

    rows.append(
        {
            "objective": "flexibility",
            "seed": args.seed,
            "n_tasks": args.n_tasks,
            "n_resources": args.n_resources,
            "ratio": ratio,
            "reference_task": reference_task_name,
            "reference_duration": reference_duration,
            "target_task": reference_task_name,
            "target_resource": flex_resource,
            "downtime_start": flex_event["start_time"],
            "downtime_end": flex_event["end_time"],
            "downtime_duration": flex_event["duration"],
            "alternative_slots": flex_alternative_slots,
            "removed_task_count": flex_removed_task_count,
            "dropped_task_count": len(flex_dropped_names),
            "dropped_tasks": ";".join(flex_dropped_names),
        }
    )

    for objective in ["makespan", "slack"]:
        print("\n" + "-" * 70)
        print(f"Objective: {objective} (same task + same downtime duration)")
        objective_tds = tds_by_objective[objective]
        if args.show_plots:
            print(f"Showing schedule BEFORE downtime: {objective}")
            display_current_schedule(objective_tds)

        objective_candidates = collect_scheduled_tasks(objective_tds)
        objective_target = find_task_by_name(objective_candidates, reference_task_name)

        if objective_target is None:
            print(
                f"Reference task {reference_task_name} is not present in {objective} schedule; "
                f"skipping downtime injection for {objective}."
            )
            rows.append(
                {
                    "objective": objective,
                    "seed": args.seed,
                    "n_tasks": args.n_tasks,
                    "n_resources": args.n_resources,
                    "ratio": ratio,
                    "reference_task": reference_task_name,
                    "reference_duration": reference_duration,
                    "target_task": None,
                    "target_resource": None,
                    "downtime_start": None,
                    "downtime_end": None,
                    "downtime_duration": reference_duration,
                    "alternative_slots": 0,
                    "removed_task_count": 0,
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
        )
        objective_unscheduled_tasks, objective_alternative_slots, objective_removed_task_count = send_events_no_reschedule(
            objective_tds,
            pd.DataFrame([objective_event]),
            objective_metric=objective,
        )
        objective_dropped_names = [t.name for t in objective_unscheduled_tasks]

        print(
            f"Reference task={reference_task_name} on {objective_resource}, "
            f"downtime=[{objective_event['start_time']}, {objective_event['end_time']}], "
            f"duration={reference_duration}, "
            f"alternative_slots={objective_alternative_slots}, removed={objective_removed_task_count}, "
            f"dropped={len(objective_dropped_names)}"
        )

        if args.show_plots:
            print(f"Showing schedule AFTER downtime: {objective}")
            display_current_schedule(objective_tds)

        rows.append(
            {
                "objective": objective,
                "seed": args.seed,
                "n_tasks": args.n_tasks,
                "n_resources": args.n_resources,
                "ratio": ratio,
                "reference_task": reference_task_name,
                "reference_duration": reference_duration,
                "target_task": reference_task_name,
                "target_resource": objective_resource,
                "downtime_start": objective_event["start_time"],
                "downtime_end": objective_event["end_time"],
                "downtime_duration": objective_event["duration"],
                "alternative_slots": objective_alternative_slots,
                "removed_task_count": objective_removed_task_count,
                "dropped_task_count": len(objective_dropped_names),
                "dropped_tasks": ";".join(objective_dropped_names),
            }
        )

    df = pd.DataFrame(rows)
    out_path = output_dir / args.results_csv
    df.to_csv(out_path, index=False)

    print("\n" + "=" * 90)
    print("RESULTS")
    print("=" * 90)
    print(df[["objective", "target_task", "alternative_slots", "dropped_task_count", "dropped_tasks"]].to_string(index=False))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
