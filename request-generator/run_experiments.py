#!/usr/bin/env python3
"""
Experiment runner scaffold.

1) Generates fullness-level experiment scenarios.
2) Iterates generated cases.
3) Runs scheduler for each case and objective.
4) Logs per-event, per-task diagnostics to a separate CSV.
"""

from tds_slack.utils import display_current_schedule
import argparse
import json
import os
import pandas as pd
from pathlib import Path

from fullness_experiment_generator import FULLNESS_PROFILES, generate_fullness_experiments
from tds_slack.executer import run_scheduler, send_event, schedule_task


# True => minimize objective, False => maximize objective
OBJECTIVE_MINIMIZE = {
    "travel": True,
    "makespan": True,
    "flexibility": False,
    "slots": False,
    "total_slack": False,
    "slack": False,
    "max_slot": False
}


def objective_should_minimize(objective_metric: str) -> bool:
    """Return optimization direction for an objective metric."""
    return OBJECTIVE_MINIMIZE.get(objective_metric, False)


def parse_args():
    p = argparse.ArgumentParser(
        description="Generate fullness experiments and run scheduler scaffold.",
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
    p.add_argument("--objectives", type=str, default="flexibility,makespan,slots,total_slack,max_slot",
                   help="Comma-separated objective metrics to test (e.g. slack,flexibility)")
    p.add_argument("--results-csv", type=str, default="experiment_results.csv",
                   help="CSV filename for case/objective dropped-task results (written in experiment root)")
    p.add_argument("--diagnostics-csv", type=str, default="task_diagnostics.csv",
                   help="CSV filename for per-task reschedule diagnostics (written in experiment root)")
    p.add_argument("--display-schedules", action="store_true",
                   help="Display schedules before/after events for each run")
    return p.parse_args()


def iter_experiment_cases(experiment_root: str):
    """Yield (level_name, case_name, case_dir) in sorted order."""
    root = Path(experiment_root)
    if not root.exists():
        return

    for level_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for case_dir in sorted(p for p in level_dir.iterdir() if p.is_dir()):
            yield level_dir.name, case_dir.name, str(case_dir)


def collect_pre_event_metrics(tds) -> dict:
    """Collect baseline schedule metrics before downtime events are sent."""
    return {
        "pre_event_flexibility": tds.sum_total_flexibility(),
        "pre_event_makespan": tds.makespan(),
        "pre_event_total_slack": tds.sum_total_slack(),
        "pre_event_slots": tds.sum_total_slot(),
        "pre_event_max_slot": tds.sum_max_slot_flexibility(),
    }


def collect_initial_task_slots(tds) -> dict:
    """
    Snapshot per-task slot flexibility for every scheduled task immediately
    after initial schedule construction, before any downtime events are sent.
    Returns dict: task_name -> {initial_task_slots, initial_task_max_slot}
    """
    task_slots = {}
    for resource in tds.resources.values():
        for task in resource.timeline.tasks:
            if (task.name.endswith('_header') or
                    task.name.endswith('_footer') or
                    'downtime' in task.name):
                continue
            task_slots[task.name] = {
                'initial_task_slots': task.get_slot_flexibility(),
                'initial_task_max_slot': task.get_max_slot_flexibility(),
            }
    return task_slots


def run_scheduler_for_case(case_dir: str, objective_metric: str, display_schedules: bool = False) -> dict:
    """
    Run scheduler for one case, then replay downtime events one at a time,
    capturing per-task reschedule diagnostics at each step.
    """
    request_path = os.path.join(case_dir, "request.json")
    travel_path = os.path.join(case_dir, "travel_matrix.json")
    events_path = os.path.join(case_dir, "future_downtimes.json")

    with open(request_path, 'r') as f:
        request_dict = json.load(f)
    with open(travel_path, 'r') as f:
        travel_matrix = json.load(f)

    events_df = pd.read_json(events_path)
    minimize = objective_should_minimize(objective_metric)

    tds = run_scheduler(
        request_dict,
        travel_matrix,
        objective_metric=objective_metric,
        minimize=minimize,
    )

    pre_event_metrics = collect_pre_event_metrics(tds)
    initial_task_slots = collect_initial_task_slots(tds)

    if display_schedules:
        display_current_schedule(tds)

    # --- per-event diagnostic loop ---
    unscheduled_tasks = []
    task_diagnostics = []

    for _, row in events_df.iterrows():
        # snapshot aggregate metrics before this downtime event
        slots_before_event = tds.sum_total_slot()
        max_slot_before_event = tds.sum_max_slot_flexibility()
        slack_before_event = tds.sum_total_slack()
        flex_before_event = tds.sum_total_flexibility()

        removed_tasks = send_event(tds, row)

        # snapshot immediately after downtime inserted, before any rescheduling
        slots_after_insert = tds.sum_total_slot()
        max_slot_after_insert = tds.sum_max_slot_flexibility()
        slack_after_insert = tds.sum_total_slack()
        flex_after_insert = tds.sum_total_flexibility()

        print('---')
        print(removed_tasks)

        for task in removed_tasks:
            # look up this task's slot values from the initial schedule
            initial_slots = initial_task_slots.get(task.name, {})
            initial_task_slots_val = initial_slots.get('initial_task_slots', None)
            initial_task_max_slot_val = initial_slots.get('initial_task_max_slot', None)

            # per-task slots right before rescheduling (after downtime inserted)
            task_slots_before = task.get_slot_flexibility()
            task_max_slot_before = task.get_max_slot_flexibility()

            # snapshot aggregate before attempting to reschedule this specific task
            slots_before_reschedule = tds.sum_total_slot()
            max_slot_before_reschedule = tds.sum_max_slot_flexibility()
            slack_before_reschedule = tds.sum_total_slack()

            schedule_attempt = schedule_task(
                tds, task,
                objective_metric=objective_metric,
                minimize=minimize,
            )

            # snapshot after reschedule attempt
            slots_after_reschedule = tds.sum_total_slot()
            max_slot_after_reschedule = tds.sum_max_slot_flexibility()
            slack_after_reschedule = tds.sum_total_slack()

            rescheduled = schedule_attempt is not False

            if rescheduled:
                print(f"Successfully rescheduled task {task.name} after removal due to downtime event.")
            else:
                print(f"Could not reschedule task {task.name} after removal due to downtime event. Task remains unscheduled.")
                unscheduled_tasks.append(task)

            task_diagnostics.append({
                "case_dir": case_dir,
                "objective_metric": objective_metric,
                "event_resource": row['resource'],
                "event_start": row['start_time'],
                "event_end": row['end_time'],
                "event_duration": row['end_time'] - row['start_time'],
                "task_name": task.name,
                "rescheduled": rescheduled,
                # per-task slots at initial schedule construction (before any events)
                "initial_task_slots": initial_task_slots_val,
                "initial_task_max_slot": initial_task_max_slot_val,
                # per-task slots after downtime inserted, before rescheduling
                "task_slots_before_reschedule": task_slots_before,
                "task_max_slot_before_reschedule": task_max_slot_before,
                # how much did the downtime degrade this specific task's slots?
                "task_slot_degraded_by_event": (
                    initial_task_slots_val - task_slots_before
                    if initial_task_slots_val is not None else None
                ),
                "task_max_slot_degraded_by_event": (
                    initial_task_max_slot_val - task_max_slot_before
                    if initial_task_max_slot_val is not None else None
                ),
                # aggregate slot capacity before this event fired
                "slots_before_event": slots_before_event,
                "max_slot_before_event": max_slot_before_event,
                "slack_before_event": slack_before_event,
                "flex_before_event": flex_before_event,
                # how much did inserting the downtime destroy aggregate capacity?
                "slots_after_insert": slots_after_insert,
                "max_slot_after_insert": max_slot_after_insert,
                "slack_after_insert": slack_after_insert,
                "slot_destroyed_by_event": slots_before_event - slots_after_insert,
                "max_slot_destroyed_by_event": max_slot_before_event - max_slot_after_insert,
                "slack_destroyed_by_event": slack_before_event - slack_after_insert,
                # aggregate available when rescheduler ran
                "slots_before_reschedule": slots_before_reschedule,
                "max_slot_before_reschedule": max_slot_before_reschedule,
                "slack_before_reschedule": slack_before_reschedule,
                # did rescheduling consume capacity (success) or not (failure)?
                "slots_after_reschedule": slots_after_reschedule,
                "max_slot_after_reschedule": max_slot_after_reschedule,
                "slack_after_reschedule": slack_after_reschedule,
                "slot_delta_reschedule": slots_after_reschedule - slots_before_reschedule,
                "max_slot_delta_reschedule": max_slot_after_reschedule - max_slot_before_reschedule,
            })

            print(
                f"  [{objective_metric}] task={task.name} "
                f"rescheduled={rescheduled} "
                f"initial_task_slots={initial_task_slots_val} "
                f"task_slots_after_insert={task_slots_before:.0f} "
                f"degraded={initial_task_slots_val - task_slots_before if initial_task_slots_val is not None else 'N/A'}"
            )

        print('===')

    if display_schedules:
        display_current_schedule(tds)

    post_event_metrics = {
        "post_event_flexibility": tds.sum_total_flexibility(),
        "post_event_makespan": tds.makespan(),
        "post_event_total_slack": tds.sum_total_slack(),
        "post_event_slots": tds.sum_total_slot(),
        "post_event_max_slot": tds.sum_max_slot_flexibility(),
    }

    unscheduled_task_names = [task.name for task in unscheduled_tasks]

    print(
        f"[{objective_metric}] {os.path.basename(case_dir)} "
        f"dropped={len(unscheduled_task_names)} "
        f"slot_delta={post_event_metrics['post_event_slots'] - pre_event_metrics['pre_event_slots']:+.0f} "
        f"max_slot_delta={post_event_metrics['post_event_max_slot'] - pre_event_metrics['pre_event_max_slot']:+.0f}"
    )

    return {
        "case_dir": case_dir,
        "objective_metric": objective_metric,
        **pre_event_metrics,
        **post_event_metrics,
        "slot_delta": post_event_metrics["post_event_slots"] - pre_event_metrics["pre_event_slots"],
        "max_slot_delta": post_event_metrics["post_event_max_slot"] - pre_event_metrics["pre_event_max_slot"],
        "slack_delta": post_event_metrics["post_event_total_slack"] - pre_event_metrics["pre_event_total_slack"],
        "flex_delta": post_event_metrics["post_event_flexibility"] - pre_event_metrics["pre_event_flexibility"],
        "dropped_task_count": len(unscheduled_task_names),
        "dropped_tasks": unscheduled_task_names,
        "task_diagnostics": task_diagnostics,
    }


def run_scheduler_on_experiments(experiment_root: str, objective_metrics: list[str],
                                 display_schedules: bool = False) -> list[dict]:
    """Run each case for each objective metric and collect results."""
    results = []
    for level_name, case_name, case_dir in iter_experiment_cases(experiment_root):
        for objective_metric in objective_metrics:
            result = run_scheduler_for_case(
                case_dir,
                objective_metric=objective_metric,
                display_schedules=display_schedules,
            )
            result["level"] = level_name
            result["case"] = case_name
            results.append(result)
    return results


def main():
    args = parse_args()
    objective_metrics = [m.strip() for m in args.objectives.split(",") if m.strip()]
    if not objective_metrics:
        raise ValueError("At least one objective metric must be provided via --objectives")

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

    print(f"Generated experiment root: {experiment_root}")
    print(f"Using level profile: {args.level_profile}")

    results = run_scheduler_on_experiments(
        experiment_root,
        objective_metrics=objective_metrics,
        display_schedules=args.display_schedules,
    )

    # save main results CSV (drop task_diagnostics — it's a list, doesn't flatten)
    results_df = pd.DataFrame(results).drop(columns=["task_diagnostics"], errors="ignore")
    csv_path = os.path.join(experiment_root, args.results_csv)
    results_df.to_csv(csv_path, index=False)
    print(f"Results CSV: {csv_path}")

    # flatten and save per-task diagnostics to a separate CSV
    all_diagnostics = []
    for r in results:
        # attach level and case to each diagnostic row for easy filtering
        for diag in r.get("task_diagnostics", []):
            diag["level"] = r.get("level")
            diag["case"] = r.get("case")
            all_diagnostics.append(diag)

    if all_diagnostics:
        diag_path = os.path.join(experiment_root, args.diagnostics_csv)
        pd.DataFrame(all_diagnostics).to_csv(diag_path, index=False)
        print(f"Task diagnostics CSV: {diag_path}")

    print(f"Completed scheduler runs: {len(results)}")


if __name__ == "__main__":
    main()
