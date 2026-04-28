#!/usr/bin/env python3
"""Generate one scenario, run one objective, and export scenario/schedule artifacts.

Outputs per run (inside a unique scenario folder):
- request.json
- travel_matrix.json
- future_downtimes.json
- schedule.csv
- schedule.html
"""

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scenario_generator import generate_scenario, make_unique_output_dir, write_scenario_files
from tds_slack.executer import run_scheduler
from tds_slack.utils import save_current_schedule_html


OBJECTIVE_MINIMIZE_DEFAULTS = {
    "flexibility": False,
    "makespan": True,
    "earliest_completion_time": True,
    "slack": False,
}


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Generate a single scenario, run one objective schedule, "
            "and save scenario JSON plus schedule CSV."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Objective settings.
    p.add_argument(
        "--objective",
        type=str,
        choices=sorted(OBJECTIVE_MINIMIZE_DEFAULTS.keys()),
        default="flexibility",
        help="Objective metric to run",
    )
    p.add_argument(
        "--minimize",
        type=str,
        choices=["auto", "true", "false"],
        default="auto",
        help="Whether to minimize objective: auto uses objective defaults",
    )

    # Scenario settings.
    p.add_argument("--seed", type=int, default=1000)
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
    p.add_argument("--downtime-prob", type=float, default=0.0)
    p.add_argument("--future-downtimes", type=int, default=0)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)

    # Output settings.
    p.add_argument("--output-dir", type=str, default="./single_runs")
    p.add_argument("--schedule-csv", type=str, default="schedule.csv")
    p.add_argument("--schedule-html", type=str, default="schedule.html")

    return p.parse_args()


def resolve_minimize_flag(objective: str, minimize_arg: str) -> bool:
    if minimize_arg == "true":
        return True
    if minimize_arg == "false":
        return False
    return OBJECTIVE_MINIMIZE_DEFAULTS[objective]


def main():
    args = parse_args()
    minimize = resolve_minimize_flag(args.objective, args.minimize)

    request, travel_matrix, future_downtimes = generate_scenario(
        n_resources=args.n_resources,
        n_tasks=args.n_tasks,
        n_locations=args.n_locations,
        caps_range=(args.min_caps, args.max_caps),
        task_duration_range=(args.min_duration, args.max_duration),
        due_date_slack_range=(args.min_slack, args.max_slack),
        horizon=args.horizon,
        downtime_prob=args.downtime_prob,
        capability_overlap=args.capability_overlap,
        travel_time_range=(args.min_travel, args.max_travel),
        future_downtime_count=args.future_downtimes,
        seed=args.seed,
    )

    scenario_output_dir = make_unique_output_dir(args.output_dir)
    req_path, tm_path, fd_path = write_scenario_files(
        scenario_output_dir,
        request,
        travel_matrix,
        future_downtimes,
    )

    started = datetime.now()
    tds = run_scheduler(
        copy.deepcopy(request),
        copy.deepcopy(travel_matrix),
        objective_metric=args.objective,
        minimize=minimize,
    )
    elapsed_seconds = (datetime.now() - started).total_seconds()

    schedule_df = tds.export_to_df()
    schedule_path = Path(scenario_output_dir) / args.schedule_csv
    schedule_df.to_csv(schedule_path, index=False)

    schedule_html_path = Path(scenario_output_dir) / args.schedule_html
    save_current_schedule_html(tds, str(schedule_html_path))

    print("=" * 90)
    print("SINGLE SCENARIO OBJECTIVE RUN")
    print("=" * 90)
    print(f"Objective: {args.objective}")
    print(f"Minimize: {minimize}")
    print(f"Seed: {args.seed}")
    print(f"Resources/Tasks: {args.n_resources}/{args.n_tasks}")
    print(f"Solve time (s): {elapsed_seconds:.4f}")
    print()
    print(f"request.json         -> {req_path}")
    print(f"travel_matrix.json   -> {tm_path}")
    print(f"future_downtimes.json -> {fd_path}")
    print(f"schedule.csv         -> {schedule_path}")
    print(f"schedule.html        -> {schedule_html_path}")
    print(f"Scheduled tasks rows: {len(schedule_df)}")


if __name__ == "__main__":
    main()
