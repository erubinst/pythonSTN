#!/usr/bin/env python3
"""
Scheduling Scenario Generator
Generates random scheduling problem scenarios including request.json and travel_matrix.json
"""

import json
import random
import argparse
import os
from datetime import datetime


# ─── Capability pools ────────────────────────────────────────────────────────

CAPABILITY_POOL = [
    "welding", "assembly", "inspection", "repair", "programming",
    "delivery", "surveillance", "painting", "cutting", "drilling",
    "packaging", "testing", "calibration", "cleaning", "sorting",
    "scanning", "lifting", "navigation", "communication", "charging",
]

TASK_TYPE_POOL = [
    "manufacturing", "quality_check", "delivery", "maintenance",
    "logistics", "assembly", "inspection", "repair",
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_location_names(n: int) -> list[str]:
    """Generate n distinct location names."""
    base = ["WarehouseA", "Office", "LaunchPad", "QualityCheck", "CustomerSite",
            "StorageB", "ProductionFloor", "LoadingDock", "Lab", "Depot"]
    if n <= len(base):
        return base[:n]
    extras = [f"Location{i}" for i in range(1, n - len(base) + 1)]
    return base + extras


def make_travel_matrix(locations: list[str], min_t: int = 5, max_t: int = 60) -> dict:
    """Generate a symmetric travel-time matrix (0 on diagonal)."""
    matrix = {loc: {} for loc in locations}
    for i, a in enumerate(locations):
        matrix[a][a] = 0
        for j, b in enumerate(locations):
            if j <= i:
                continue
            t = random.randint(min_t, max_t)
            matrix[a][b] = t
            matrix[b][a] = t
    return matrix


def make_resources(n_resources: int, locations: list[str],
                   caps_range: tuple[int, int], downtime_prob: float,
                   horizon: int, capability_overlap: float = 0.8) -> list[dict]:
    """
    Each resource gets a random number of capabilities in [caps_range[0], caps_range[1]],
    plus its unique presence capability.

    capability_overlap controls how much of each resource's capabilities come from
    a shared capability pool (0.0 = no intended overlap, 1.0 = fully shared).
    """
    if not 0.0 <= capability_overlap <= 1.0:
        raise ValueError("capability_overlap must be between 0.0 and 1.0")

    common_pool_size = int(round(len(CAPABILITY_POOL) * capability_overlap))
    common_pool_size = max(0, min(common_pool_size, len(CAPABILITY_POOL)))
    common_caps = random.sample(CAPABILITY_POOL, common_pool_size) if common_pool_size > 0 else []

    resources = []
    for i in range(1, n_resources + 1):
        name = f"Resource{i}"
        loc = random.choice(locations)

        # Variable number of capabilities per resource
        n_caps = random.randint(*caps_range)

        overlap_count = int(round(n_caps * capability_overlap))
        overlap_count = min(overlap_count, n_caps, len(common_caps))

        shared_caps = random.sample(common_caps, overlap_count) if overlap_count > 0 else []

        remaining_count = n_caps - len(shared_caps)
        remaining_pool = [c for c in CAPABILITY_POOL if c not in shared_caps]
        unique_caps = random.sample(remaining_pool, min(remaining_count, len(remaining_pool)))

        caps = shared_caps + unique_caps

        # Always append the unique presence capability
        caps.append(f"{name.lower()}_presence")

        downtimes = []
        if random.random() < downtime_prob:
            dur = random.choice([30])
            start = random.randint(0, horizon - dur)
            downtimes.append({
                "start_time": start,
                "end_time": start + dur,
                "duration": dur,
                "location": loc,
            })

        resources.append({
            "name": name,
            "type": "reusable",
            "location": loc,
            "capabilities": caps,
            "downtimes": downtimes,
        })
    return resources


def make_templates(n_tasks: int, resources: list[dict],
                   task_duration_range: tuple[int, int]) -> list[dict]:
    """
    One template per task. Each template has a single executable subtask
    that requires one capability guaranteed to be held by at least one resource.
    """
    # Collect capabilities that at least one resource can satisfy (exclude presence caps)
    available_caps = list({
        c
        for r in resources
        for c in r["capabilities"]
        if not c.endswith("_presence")
    })

    templates = []
    for i in range(1, n_tasks + 1):
        task_name = f"Task{i}"
        req_caps = [random.choice(available_caps)] if available_caps else []
        duration = random.randint(*task_duration_range)
        task_type = random.choice(TASK_TYPE_POOL)

        templates.append({
            "name": task_name,
            "type": "meets",
            "requiredCapabilities": [],
            "subtasks": [
                {
                    "taskName": f"{task_name.lower()}_exec",
                    "type": "executable",
                    "requiredCapabilities": req_caps,
                    "duration": duration,
                    "start-location": "@start-location",
                    "end-location": "@end-location",
                    "task_type": task_type,
                }
            ],
        })
    return templates


def make_orders(templates: list[dict], locations: list[str],
                horizon: int, due_date_slack_range: tuple[int, int],
                max_travel: int) -> list[dict]:
    """
    One order per template, quantity always 1.
    Due date = earliest_start + task_duration + random slack in due_date_slack_range.
    """
    orders = []
    for tmpl in templates:
        subtask_duration = tmpl["subtasks"][0]["duration"]
        start_loc = random.choice(locations)
        end_loc = start_loc

        # Let orders start anywhere that still leaves room for the task duration
        # plus the maximum travel time.
        latest_earliest = max(0, horizon - (subtask_duration + max_travel))
        earliest = random.randint(0, latest_earliest)
        # Due date with variable slack
        slack = random.randint(*due_date_slack_range)
        due = min(horizon, earliest + subtask_duration + slack)

        orders.append({
            "name": tmpl["name"],
            "quantity": 1,
            "earlieststartdate": earliest,
            "duedate": due,
            "start-location": start_loc,
            "end-location": end_loc,
            "tasks": [tmpl["name"]],
        })
    return orders


def make_future_downtimes(resources: list[dict], orders: list[dict],
                          templates: list[dict], locations: list[str],
                         horizon: int, downtime_count: int, downtime_duration: int | None = None) -> list[dict]:
    """
    Generate future downtimes that each target exactly one task's execution window,
    only assigning downtime to a resource that has the required capability for that task.

    Each downtime is sized to cover a single task's window and no more, minimizing
    multi-task displacement so that recovery depends on finding exactly one alternate slot.
    """
    future_downtimes = []

    # Track used downtime start times per resource so names like
    # "resourceX_downtime_<start>" remain unique.
    used_starts_by_resource: dict[str, set[int]] = {}
    for resource in resources:
        res_name = resource["name"]
        used_starts_by_resource[res_name] = set(
            int(dt.get("start_time", -1))
            for dt in resource.get("downtimes", [])
            if "start_time" in dt
        )

    if not orders:
        return future_downtimes

    # Build lookup: task_name -> required capability (from template subtask)
    task_required_cap: dict[str, str | None] = {}
    for tmpl in templates:
        caps = tmpl["subtasks"][0]["requiredCapabilities"]
        task_required_cap[tmpl["name"]] = caps[0] if caps else None

    # Build eligible (resource, order) pairs where resource has the required capability
    # and the task window is wide enough to be meaningful
    eligible_pairs: list[tuple[dict, dict]] = []
    for order in orders:
        required_cap = task_required_cap.get(order["name"])
        task_duration = order["duedate"] - order["earlieststartdate"]
        if task_duration <= 0:
            continue

        capable_resources = []
        for resource in resources:
            if required_cap is not None:
                resource_caps = [
                    c for c in resource["capabilities"]
                    if not c.endswith("_presence")
                ]
                if required_cap not in resource_caps:
                    continue
            capable_resources.append(resource)

        # Only include tasks that have at least 2 capable resources —
        # tasks with only one capable resource can never be reassigned
        if len(capable_resources) >= 2:
            for resource in capable_resources:
                eligible_pairs.append((resource, order))

    if not eligible_pairs:
        return future_downtimes

    random.shuffle(eligible_pairs)

    seen_pairs: set[tuple[str, str]] = set()
    for resource, order in eligible_pairs:
        if len(future_downtimes) >= downtime_count:
            break

        pair_key = (resource["name"], order["name"])
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        earliest = order["earlieststartdate"]
        due = order["duedate"]
        task_duration = due - earliest

        # Duration: use explicit downtime_duration if provided, otherwise calculate from task
        if downtime_duration is None:
            # Duration: just longer than the task to displace exactly this one task
            # capped at 90 to avoid consuming too much of the horizon
            dt_duration = min(task_duration + 10, 90)
        else:
            dt_duration = min(downtime_duration, horizon)

        # Anchor downtime to start at or just before the task's earliest start,
        # covering the task window without reaching far beyond the due date
        min_start = max(0, earliest - 10)
        max_start = max(min_start, due - dt_duration)

        candidate_starts = [
            s for s in range(min_start, max_start + 1)
            if s not in used_starts_by_resource[resource["name"]]
        ]
        if not candidate_starts:
            continue

        start = random.choice(candidate_starts)
        end = min(horizon, start + dt_duration)
        used_starts_by_resource[resource["name"]].add(start)

        future_downtimes.append({
            "resource": resource["name"],
            "start_time": start,
            "end_time": end,
            "duration": end - start,
            "location": resource["location"],
        })

    return future_downtimes


# ─── Main generator ──────────────────────────────────────────────────────────

def generate_scenario(
    n_resources: int = 3,
    n_tasks: int = 3,
    n_locations: int = 5,
    caps_range: tuple = (1, 5),
    task_duration_range: tuple = (15, 90),
    due_date_slack_range: tuple = (0, 180),
    horizon: int = 1440,
    downtime_prob: float = 0.5,
    capability_overlap: float = 0.8,
    travel_time_range: tuple = (5, 60),
    future_downtime_count: int = 3,
    downtime_duration: int | None = None,
    seed: int | None = None,
) -> tuple[dict, dict, list[dict]]:
    """
    Returns (request_json, travel_matrix_json, future_downtimes_json).
    """
    if seed is not None:
        random.seed(seed)

    locations = make_location_names(n_locations)
    travel_matrix = make_travel_matrix(locations, *travel_time_range)
    resources = make_resources(n_resources, locations, caps_range,
                               downtime_prob, horizon, capability_overlap)
    templates = make_templates(n_tasks, resources, task_duration_range)
    orders = make_orders(
        templates,
        locations,
        horizon,
        due_date_slack_range,
        travel_time_range[1],
    )
    future_downtimes = make_future_downtimes(resources, orders, templates, locations,
                                             horizon, future_downtime_count, downtime_duration)

    request = {
        "resourceTypes": resources,
        "templates": templates,
        "orders": orders,
        "order-constraints": [],
    }

    return request, travel_matrix, future_downtimes


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate a random scheduling scenario.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--n-resources",        type=int,   default=3,
                   help="Number of resources")
    p.add_argument("--n-tasks",            type=int,   default=3,
                   help="Number of task templates / orders")
    p.add_argument("--n-locations",        type=int,   default=5,
                   help="Number of distinct locations")
    p.add_argument("--min-caps",           type=int,   default=1,
                   help="Minimum number of (non-presence) capabilities per resource")
    p.add_argument("--max-caps",           type=int,   default=5,
                   help="Maximum number of (non-presence) capabilities per resource")
    p.add_argument("--min-duration",       type=int,   default=15,
                   help="Minimum task duration (minutes)")
    p.add_argument("--max-duration",       type=int,   default=100,
                   help="Maximum task duration (minutes)")
    p.add_argument("--min-slack",          type=int,   default=0,
                   help="Minimum due-date slack beyond task duration (minutes)")
    p.add_argument("--max-slack",          type=int,   default=1440,
                   help="Maximum due-date slack beyond task duration (minutes)")
    p.add_argument("--horizon",            type=int,   default=1440,
                   help="Planning horizon in minutes (default = 1 day)")
    p.add_argument("--downtime-prob",      type=float, default=0,
                   help="Probability [0,1] that a resource has a downtime window")
    p.add_argument("--capability-overlap", type=float, default=0.7,
                   help="Percent overlap [0,1] of resource capabilities")
    p.add_argument("--min-travel",         type=int,   default=5,
                   help="Minimum travel time between locations (minutes)")
    p.add_argument("--max-travel",         type=int,   default=60,
                   help="Maximum travel time between locations (minutes)")
    p.add_argument("--seed",               type=int,   default=None,
                   help="Random seed for reproducibility")
    p.add_argument("--future-downtimes",  type=int,   default=5,
                   help="Number of future downtimes to generate")
    p.add_argument("--downtime-duration",  type=int,   default=None,
                   help="Explicit downtime duration in minutes (if None, calculated from task duration)")
    p.add_argument("--output-dir",         type=str,   default="./slack_scenarios",
                   help="Base directory where a unique scenario folder will be created")
    return p.parse_args()


def make_unique_output_dir(base_dir: str) -> str:
    """Create a unique scenario subdirectory under base_dir and return its path."""
    os.makedirs(base_dir, exist_ok=True)

    # Use local timestamp for easy sorting and human-readable scenario folders.
    timestamp = datetime.now().strftime("scenario_%Y%m%d_%H%M%S")
    scenario_dir = os.path.join(base_dir, timestamp)

    if not os.path.exists(scenario_dir):
        os.makedirs(scenario_dir, exist_ok=False)
        return scenario_dir

    # If runs happen within the same second, append a numeric suffix.
    counter = 1
    while True:
        candidate = os.path.join(base_dir, f"{timestamp}_{counter:02d}")
        if not os.path.exists(candidate):
            os.makedirs(candidate, exist_ok=False)
            return candidate
        counter += 1


def write_scenario_files(output_dir: str, request: dict,
                         travel_matrix: dict,
                         future_downtimes: list[dict]) -> tuple[str, str, str]:
    os.makedirs(output_dir, exist_ok=True)

    req_path = os.path.join(output_dir, "request.json")
    tm_path = os.path.join(output_dir, "travel_matrix.json")
    fd_path = os.path.join(output_dir, "future_downtimes.json")

    with open(req_path, "w") as f:
        json.dump(request, f, indent=4)
    with open(tm_path, "w") as f:
        json.dump(travel_matrix, f, indent=4)
    with open(fd_path, "w") as f:
        json.dump(future_downtimes, f, indent=4)

    return req_path, tm_path, fd_path


def main():
    args = parse_args()

    scenario_output_dir = make_unique_output_dir(args.output_dir)

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
        downtime_duration=args.downtime_duration,
        seed=args.seed,
    )

    req_path, tm_path, fd_path = write_scenario_files(
        scenario_output_dir, request, travel_matrix, future_downtimes
    )

    print(f"✓ scenario folder      → {scenario_output_dir}")
    print(f"✓ request.json         → {req_path}")
    print(f"✓ travel_matrix.json   → {tm_path}")
    print(f"✓ future_downtimes.json → {fd_path}")
    print()
    print("Summary")
    print(f"  Resources         : {args.n_resources}")
    print(f"  Tasks             : {args.n_tasks}")
    print(f"  Locations         : {args.n_locations}")
    print(f"  Caps range        : {args.min_caps}–{args.max_caps} per resource")
    print(f"  Cap overlap       : {args.capability_overlap}")
    print(f"  Slack range       : {args.min_slack}–{args.max_slack} min")
    print(f"  Horizon           : {args.horizon} min")
    print(f"  Future downtimes  : {args.future_downtimes}")
    print(f"  Seed              : {args.seed}")


if __name__ == "__main__":
    main()