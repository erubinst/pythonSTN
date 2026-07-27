#!/usr/bin/env python3
"""Generate an increasing-crossover scenario family from an og_pairs request.

The script keeps the original request structure intact and only broadens the
capabilities of selected caregiver resources. For each crossover level, the
first k caregivers in file order are merged into one combined network by
unioning their original non-presence capabilities. The remaining caregivers
keep their original capabilities.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from itertools import combinations
from pathlib import Path


DEFAULT_REQUEST_PATH = Path(
    "/Users/erubinst/ICLL/pythonSTN/tds/projected_week/no_crossover/request.json"
)
DEFAULT_TRAVEL_MATRIX_PATH = Path(
    "/Users/erubinst/ICLL/pythonSTN/tds/projected_week/no_crossover/travel_matrix.json"
)
DEFAULT_OUTPUT_DIR = Path("/Users/erubinst/ICLL/pythonSTN/tds/projected_week/crossover")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a scenario sweep by increasing caregiver capability crossover.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--request-path", type=Path, default=DEFAULT_REQUEST_PATH)
    parser.add_argument("--travel-matrix-path", type=Path, default=DEFAULT_TRAVEL_MATRIX_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--prefix",
        type=str,
        default="og_pairs_crossover_4",
        help="Prefix for generated scenario folders.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: object) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)


def is_caregiver(resource: dict) -> bool:
    return str(resource.get("type", "")).lower() == "cg"


def non_presence_capabilities(resource: dict) -> list[str]:
    return [cap for cap in resource.get("capabilities", []) if not cap.endswith("_presence")]


def get_caregiver_resources(request: dict) -> list[dict]:
    return [resource for resource in request.get("resourceTypes", []) if is_caregiver(resource)]


def upgrade_caregiver_capabilities(resource: dict, shared_capabilities: list[str]) -> dict:
    upgraded = copy.deepcopy(resource)
    existing_caps = upgraded.get("capabilities", [])
    upgraded["capabilities"] = sorted({*existing_caps, *shared_capabilities})
    return upgraded


def build_crossover_request(request: dict, upgraded_caregivers: set[str], shared_capabilities: list[str]) -> dict:
    output_request = copy.deepcopy(request)

    updated_resources = []
    for resource in output_request.get("resourceTypes", []):
        if resource.get("name") in upgraded_caregivers:
            updated_resources.append(upgrade_caregiver_capabilities(resource, shared_capabilities))
        else:
            updated_resources.append(resource)

    output_request["resourceTypes"] = updated_resources
    return output_request


def make_sweep_root(base_dir: Path, prefix: str) -> Path:
    sweep_root = base_dir / prefix
    sweep_root.mkdir(parents=True, exist_ok=True)
    return sweep_root


def make_scenario_dir(sweep_root: Path, combo_size: int, combo_index: int, total_combos: int) -> Path:
    scenario_dir = sweep_root / f"k{combo_size:02d}_{combo_index:04d}_of_{total_combos:04d}"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    return scenario_dir


def main() -> None:
    args = parse_args()

    request = load_json(args.request_path)

    caregivers = get_caregiver_resources(request)
    caregiver_names = [resource["name"] for resource in caregivers]
    original_capabilities = {
        resource["name"]: non_presence_capabilities(resource)
        for resource in caregivers
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)

    sweep_root = make_sweep_root(args.output_dir, args.prefix)

    all_combinations = []
    for combo_size in range(len(caregiver_names) + 1):
        if combo_size == 1:
            continue
        for combo in combinations(caregiver_names, combo_size):
            all_combinations.append(list(combo))

    total_combos = len(all_combinations)
    for combo_index, merged_names in enumerate(all_combinations, start=1):
        merged_capabilities: list[str] = []
        for caregiver_name in merged_names:
            merged_capabilities.extend(original_capabilities[caregiver_name])

        scenario_request = build_crossover_request(request, set(merged_names), merged_capabilities)
        scenario_dir = make_scenario_dir(sweep_root, len(merged_names), combo_index, total_combos)

        write_json(scenario_dir / "request.json", scenario_request)
        shutil.copy2(args.travel_matrix_path, scenario_dir / "travel_matrix.json")

        print(
            f"Wrote {scenario_dir.name}: merged caregivers = "
            f"{', '.join(merged_names) if merged_names else 'none'}"
        )


if __name__ == "__main__":
    main()