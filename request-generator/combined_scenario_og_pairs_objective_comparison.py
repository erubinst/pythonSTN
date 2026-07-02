#!/usr/bin/env python3
"""Run the combined_scenario_og_pairs 0-crossover scenario under two objectives.

This script reuses the existing request.json and travel_matrix.json in the
combined_scenario_og_pairs folder, runs two scheduling configurations, and
writes per-objective schedule/piggybacking outputs plus a summary CSV into the
same folder.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import pandas as pd

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from tds.config import EPOCH_DATE
from tds.executer import upload_request
from tds.search import ObjectiveType, SortType, schedule_dependent_task, schedule_independent_tasks
from tds.surface_piggybacking import detect_piggybacking
from tds.utils import export_schedule_to_df, path_to_dict


DEFAULT_SCENARIO_DIR = Path(
    "/Users/erubinst/ICLL/pythonSTN/tds/scenarios/combined_scenario_4_pairs"
)

OBJECTIVE_RUNS = [
    ("min_travel", ObjectiveType.MIN_TRAVEL_TIME, SortType.CAREGIVER_ROUTINE),
    ("min_makespan", ObjectiveType.MIN_MAKESPAN, SortType.FLEXIBILITY),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the 0-crossover combined_scenario_og_pairs scenario under min travel and min makespan objectives.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scenario-dir", type=Path, default=DEFAULT_SCENARIO_DIR)
    parser.add_argument(
        "--summary-csv",
        type=str,
        default="objective_comparison_summary.csv",
        help="Filename for the combined comparison table written into the scenario folder.",
    )
    parser.add_argument(
        "--schedule-prefix",
        type=str,
        default="schedule",
        help="Prefix for objective-specific schedule CSV outputs.",
    )
    parser.add_argument(
        "--piggyback-prefix",
        type=str,
        default="piggybacking",
        help="Prefix for objective-specific piggybacking CSV outputs.",
    )
    return parser.parse_args()


def run_objective(
    request: dict,
    travel_matrix: dict,
    objective: ObjectiveType,
    sort_type: SortType,
) -> tuple[object, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    tds = upload_request(copy.deepcopy(request), copy.deepcopy(travel_matrix), EPOCH_DATE)

    dependent_tasks = schedule_independent_tasks(tds, objective, sort_type)
    for task in dependent_tasks:
        schedule_dependent_task(tds, task, objective)

    schedule_df = export_schedule_to_df(tds, EPOCH_DATE)
    piggyback_df = detect_piggybacking(schedule_df, travel_matrix)
    caregiver_time = tds.get_caregiver_total_time()

    return tds, schedule_df, piggyback_df, caregiver_time


def flatten_caregiver_time(caregiver_time: dict[str, int]) -> dict[str, int]:
    return {f"caregiver_time__{name}": int(total_time) for name, total_time in caregiver_time.items()}


def main() -> None:
    args = parse_args()
    scenario_dir = args.scenario_dir

    if not scenario_dir.exists():
        raise FileNotFoundError(f"Scenario directory does not exist: {scenario_dir}")

    request_path = scenario_dir / "request.json"
    travel_matrix_path = scenario_dir / "travel_matrix.json"
    if not request_path.exists() or not travel_matrix_path.exists():
        raise FileNotFoundError(
            "Expected request.json and travel_matrix.json inside "
            f"{scenario_dir}"
        )

    request = path_to_dict(request_path)
    travel_matrix = path_to_dict(travel_matrix_path)

    summary_rows = []

    for objective_label, objective, sort_type in OBJECTIVE_RUNS:
        _, schedule_df, piggyback_df, caregiver_time = run_objective(
            request,
            travel_matrix,
            objective,
            sort_type,
        )

        schedule_path = scenario_dir / f"{args.schedule_prefix}_{objective_label}.csv"
        piggyback_path = scenario_dir / f"{args.piggyback_prefix}_{objective_label}.csv"
        schedule_df.to_csv(schedule_path, index=False)
        piggyback_df.to_csv(piggyback_path, index=False)

        row = {
            "objective_label": objective_label,
            "objective_metric": objective.value,
            "sort_type": sort_type.value,
            "schedule_path": str(schedule_path),
            "piggybacking_path": str(piggyback_path),
            "piggybacking_count": int(len(piggyback_df)),
            "total_caregiver_time": int(sum(caregiver_time.values())),
        }
        row.update(flatten_caregiver_time(caregiver_time))
        summary_rows.append(row)

        print(f"Wrote {schedule_path}")
        print(f"Wrote {piggyback_path}")

    summary_df = pd.DataFrame(summary_rows).fillna(0)
    summary_path = scenario_dir / args.summary_csv
    summary_df.to_csv(summary_path, index=False)

    print(f"Wrote summary table: {summary_path}")
    print(f"Processed {len(summary_rows)} objective runs in {scenario_dir}")


if __name__ == "__main__":
    main()