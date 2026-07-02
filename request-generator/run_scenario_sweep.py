#!/usr/bin/env python3
"""Run the scheduler across a folder of scenario subsets.

For each scenario subfolder, this script:
- loads request.json and travel_matrix.json
- runs the scheduler
- writes schedule.csv into that scenario folder

It also writes a single caregiver-time summary table at the sweep root.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

# Add parent directory to path to import local modules.
sys.path.insert(0, str(Path(__file__).parent.parent))

from tds.config import EPOCH_DATE
from tds.executer import execute_scheduling_run
from tds.surface_piggybacking import detect_piggybacking


DEFAULT_SWEEP_ROOT = Path("/Users/erubinst/ICLL/pythonSTN/tds/scenarios/og_pairs_crossover_4")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the scheduler for each scenario folder in a sweep.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sweep-root", type=Path, default=DEFAULT_SWEEP_ROOT)
    parser.add_argument(
        "--summary-csv",
        type=str,
        default="caregiver_time_summary.csv",
        help="Filename for the aggregated caregiver-time table saved at the sweep root.",
    )
    parser.add_argument(
        "--schedule-filename",
        type=str,
        default="schedule.csv",
        help="Filename written into each scenario folder.",
    )
    parser.add_argument(
        "--piggybacking-filename",
        type=str,
        default="piggybacking.csv",
        help="Filename written into each scenario folder for piggybacking results.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_scenario_dirs(sweep_root: Path) -> list[Path]:
    scenario_dirs = []
    for candidate in sorted(sweep_root.iterdir()):
        if not candidate.is_dir():
            continue
        if (candidate / "request.json").exists() and (candidate / "travel_matrix.json").exists():
            scenario_dirs.append(candidate)
    return scenario_dirs


def combined_caregiver_count(scenario_dir: Path) -> int:
    match = re.match(r"^k(\d+)_", scenario_dir.name)
    if not match:
        raise ValueError(f"Could not determine combined caregiver count from folder name: {scenario_dir.name}")
    return int(match.group(1))


def flatten_caregiver_time(caregiver_time: dict[str, int]) -> dict[str, int]:
    return {f"caregiver_time__{name}": int(total_time) for name, total_time in caregiver_time.items()}


def total_caregiver_time(caregiver_time: dict[str, int]) -> int:
    return int(sum(caregiver_time.values()))


def total_piggybacking_count(piggyback_df: pd.DataFrame) -> int:
    return int(len(piggyback_df))


def main() -> None:
    args = parse_args()
    sweep_root = args.sweep_root

    if not sweep_root.exists():
        raise FileNotFoundError(f"Sweep root does not exist: {sweep_root}")

    scenario_dirs = iter_scenario_dirs(sweep_root)
    if not scenario_dirs:
        raise FileNotFoundError(
            f"No scenario folders found under {sweep_root}. Expected subfolders containing request.json and travel_matrix.json."
        )

    summary_rows = []

    for scenario_dir in scenario_dirs:
        request_path = scenario_dir / "request.json"
        travel_matrix_path = scenario_dir / "travel_matrix.json"

        request = load_json(request_path)
        travel_matrix = load_json(travel_matrix_path)

        _, schedule_df, caregiver_time = execute_scheduling_run(request, travel_matrix, EPOCH_DATE)

        schedule_path = scenario_dir / args.schedule_filename
        schedule_df.to_csv(schedule_path, index=False)

        piggyback_df = detect_piggybacking(schedule_df, travel_matrix)
        piggyback_path = scenario_dir / args.piggybacking_filename
        piggyback_df.to_csv(piggyback_path, index=False)

        row = {
            "scenario_name": scenario_dir.name,
            "scenario_path": str(scenario_dir),
            "combined_caregiver_count": combined_caregiver_count(scenario_dir),
            "schedule_path": str(schedule_path),
            "piggybacking_path": str(piggyback_path),
            "total_caregiver_time": total_caregiver_time(caregiver_time),
            "piggybacking_count": total_piggybacking_count(piggyback_df),
        }
        row.update(flatten_caregiver_time(caregiver_time))
        summary_rows.append(row)

        print(f"Wrote {schedule_path}")
        print(f"Wrote {piggyback_path}")

    detailed_df = pd.DataFrame(summary_rows).fillna(0)
    summary_df = (
        detailed_df.groupby("combined_caregiver_count", as_index=False)
        .agg(
            scenario_count=("scenario_name", "count"),
            avg_total_caregiver_time=("total_caregiver_time", "mean"),
            avg_piggybacking_count=("piggybacking_count", "mean"),
        )
        .sort_values("combined_caregiver_count")
    )

    summary_path = sweep_root / args.summary_csv
    summary_df.to_csv(summary_path, index=False)

    detailed_path = sweep_root / f"detailed_{args.summary_csv}"
    detailed_df.to_csv(detailed_path, index=False)

    print(f"Wrote summary table: {summary_path}")
    print(f"Wrote detailed table: {detailed_path}")
    print(f"Processed {len(summary_rows)} scenarios under {sweep_root}")


if __name__ == "__main__":
    main()