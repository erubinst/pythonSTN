#!/usr/bin/env python3
"""
Empirical sweep to determine how often the flexibility objective produces 
the highest flexibility value compared to other objectives.

Tests across different scenario parameters (tasks, resources, locations, etc.)
to show that flexibility objective is consistently better at maximizing flexibility.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path to import local modules
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import copy
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict
from math import gcd

from scenario_generator import generate_scenario
from tds_slack.executer import run_scheduler


# Objectives to compare
BASE_OBJECTIVES = {
    "flexibility": {"minimize": False},
    "earliest_completion_time": {"minimize": False},
    "makespan": {"minimize": True},
}

SLACK_OBJECTIVE = {"slack": {"minimize": False}}


# Default pair set for plot-focused sweeps.
TARGET_PAIRS = [
    (5, 10), (6, 6), (9, 6), (10, 5), (10, 4),
    (12, 4), (14, 4), (16, 4), (18, 4), (20, 4), (22, 4), (24, 4), (26, 4)
]


SCENARIO_PROFILES = {
    "sparse": {
        "task_counts": [4, 5, 6, 8],
        "resource_counts": [11, 13],   # ratios: 0.31, 0.38, 0.55, 0.73
        "location_count": 3,
    },
    "medium_sparse": {
        "task_counts": [9, 11, 12, 15, 17],
        "resource_counts": [6, 7, 9],  # ratios: 1.00, 1.22, 1.71, 2.00, 2.50, 2.83
        "location_count": 3,
    },
    "dense": {
        "task_counts": [19, 22, 25],
        "resource_counts": [4, 5],     # ratios: 3.80, 4.40, 6.25
        "location_count": 3,
    },
}


def _reduced_ratio_pair(n_tasks, n_resources):
    d = gcd(n_tasks, n_resources)
    return n_tasks // d, n_resources // d


def validate_profile_ratios(selected_profiles):
    """Ensure ratios are unique and strictly increase across sparsity levels."""
    ratio_owner = {}
    level_order = ["sparse", "medium_sparse", "dense"]
    selected_order = [lvl for lvl in level_order if lvl in selected_profiles]

    min_ratio = {}
    max_ratio = {}

    for profile_name in selected_order:
        profile = selected_profiles[profile_name]
        numeric_ratios = []
        for n_tasks in profile["task_counts"]:
            for n_resources in profile["resource_counts"]:
                reduced = _reduced_ratio_pair(n_tasks, n_resources)
                if reduced in ratio_owner:
                    owner = ratio_owner[reduced]
                    raise ValueError(
                        "Duplicate task/resource ratio detected: "
                        f"{n_tasks}/{n_resources} duplicates {owner}. "
                        "Choose non-overlapping task/resource values."
                    )
                ratio_owner[reduced] = f"{profile_name}:{n_tasks}/{n_resources}"
                numeric_ratios.append(n_tasks / n_resources)

        min_ratio[profile_name] = min(numeric_ratios)
        max_ratio[profile_name] = max(numeric_ratios)

    for idx in range(1, len(selected_order)):
        prev_level = selected_order[idx - 1]
        curr_level = selected_order[idx]
        if min_ratio[curr_level] <= max_ratio[prev_level]:
            raise ValueError(
                f"Profile ratio overlap: max({prev_level})={max_ratio[prev_level]:.3f} "
                f"must be < min({curr_level})={min_ratio[curr_level]:.3f}."
            )


def parse_args():
    p = argparse.ArgumentParser(
        description="Sweep scenarios to compare objective performance on flexibility",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    
    # Sweep parameters
    p.add_argument("--num-scenarios", type=int, default=100,
                   help="Number of scenarios per parameter combination")
    p.add_argument("--seed-start", type=int, default=1000,
                   help="Starting random seed")
    
    # Scenario profile settings are built into the script.
    p.add_argument("--profiles", type=str, default="sparse,medium_sparse,dense",
                   help="Comma-separated scenario profiles to run")
    p.add_argument(
        "--combination-mode",
        type=str,
        choices=["target_pairs", "profiles"],
        default="target_pairs",
        help="Use explicit TARGET_PAIRS or profile-based cartesian products",
    )
    p.add_argument(
        "--include-slack",
        action="store_true",
        help="Include slack as an additional objective in the comparison and reporting.",
    )
    
    # Fixed parameters
    p.add_argument("--min-caps", type=int, default=1)
    p.add_argument("--max-caps", type=int, default=5)
    p.add_argument("--min-duration", type=int, default=15)
    p.add_argument("--max-duration", type=int, default=200)
    p.add_argument("--min-slack", type=int, default=0)
    p.add_argument("--max-slack", type=int, default=800)
    p.add_argument("--horizon", type=int, default=1440)
    p.add_argument("--capability-overlap", type=float, default=0.7)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)
    
    # Output
    p.add_argument("--output-dir", type=str, default="./flexibility_sweep",
                   help="Output directory for results")
    
    return p.parse_args()


def get_objective_value(tds, objective_metric):
    """Get the flexibility value from the TDS manager."""
    return tds.sum_total_flexibility()


def validate_target_pairs(target_pairs):
    """Ensure target pairs have unique reduced ratios."""
    ratio_owner = {}
    for n_tasks, n_resources in target_pairs:
        reduced = _reduced_ratio_pair(n_tasks, n_resources)
        if reduced in ratio_owner:
            raise ValueError(
                "Duplicate task/resource ratio detected in TARGET_PAIRS: "
                f"{n_tasks}/{n_resources} duplicates {ratio_owner[reduced]}"
            )
        ratio_owner[reduced] = f"{n_tasks}/{n_resources}"


def run_scenario(request, travel_matrix, objectives):
    """Run all configured objectives on a scenario and return their values."""
    results = {}
    
    for objective in objectives.keys():
        request_copy = copy.deepcopy(request)
        travel_matrix_copy = copy.deepcopy(travel_matrix)
        
        minimize = objectives[objective]["minimize"]
        tds = run_scheduler(
            request_copy,
            travel_matrix_copy,
            objective_metric=objective,
            minimize=minimize,
        )
        
        results[objective] = {
            "flexibility": get_objective_value(tds, objective),
            "earliest_completion_time": tds.sum_completion_time_diff(),
        }
    
    return results


def main():
    args = parse_args()
    objectives = dict(BASE_OBJECTIVES)
    if args.include_slack:
        objectives.update(SLACK_OBJECTIVE)
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    selected_profiles = {}
    run_combinations = []
    if args.combination_mode == "profiles":
        profile_names = [x.strip() for x in args.profiles.split(",") if x.strip()]
        invalid_profiles = [name for name in profile_names if name not in SCENARIO_PROFILES]
        if invalid_profiles:
            raise ValueError(
                f"Unknown profiles: {', '.join(invalid_profiles)}. "
                f"Available: {', '.join(SCENARIO_PROFILES.keys())}"
            )

        selected_profiles = {name: SCENARIO_PROFILES[name] for name in profile_names}
        validate_profile_ratios(selected_profiles)
        for profile_name, profile in selected_profiles.items():
            for n_tasks in profile["task_counts"]:
                for n_resources in profile["resource_counts"]:
                    run_combinations.append(
                        {
                            "group": profile_name,
                            "n_tasks": n_tasks,
                            "n_resources": n_resources,
                            "n_locations": profile["location_count"],
                            "pair_label": f"{n_tasks}/{n_resources}",
                        }
                    )
    else:
        validate_target_pairs(TARGET_PAIRS)
        for n_tasks, n_resources in TARGET_PAIRS:
            run_combinations.append(
                {
                    "group": f"T{n_tasks}_R{n_resources}",
                    "n_tasks": n_tasks,
                    "n_resources": n_resources,
                    "n_locations": 3,
                    "pair_label": f"{n_tasks}/{n_resources}",
                }
            )

    print("=" * 100)
    print("FLEXIBILITY OBJECTIVE EMPIRICAL SWEEP")
    print("=" * 100)
    print(f"\nParameters:")
    print(f"  Combination mode: {args.combination_mode}")
    print(f"  Include slack objective: {args.include_slack}")
    if args.combination_mode == "profiles":
        for profile_name, profile in selected_profiles.items():
            print(f"  {profile_name}: tasks={profile['task_counts']}, resources={profile['resource_counts']}, locations={profile['location_count']}")
    else:
        pair_text = ", ".join([f"{t}/{r}" for t, r in TARGET_PAIRS])
        print(f"  target_pairs ({len(TARGET_PAIRS)}): {pair_text}")
        print("  target_pairs location_count: 3")
    print(f"  Scenarios per combination: {args.num_scenarios}")
    total_scenarios_to_run = len(run_combinations) * args.num_scenarios
    print(f"  Total scenarios to run: {total_scenarios_to_run}")
    print()

    # Track results
    all_results = []
    flexibility_wins = 0
    flexibility_wins_vs_makespan = 0
    flexibility_wins_vs_slack = 0
    flexibility_wins_vs_all = 0
    earliest_completion_time_matches = 0
    slack_matches = 0
    total_scenarios = 0
    wins_by_param = defaultdict(lambda: {"wins": 0, "total": 0})
    makespan_wins_by_param = defaultdict(lambda: {"wins": 0, "total": 0})
    slack_wins_by_param = defaultdict(lambda: {"wins": 0, "total": 0})
    all_wins_by_param = defaultdict(lambda: {"wins": 0, "total": 0})
    earliest_completion_time_matches_by_param = defaultdict(lambda: {"matches": 0, "total": 0})
    slack_matches_by_param = defaultdict(lambda: {"matches": 0, "total": 0})
    profile_stats = defaultdict(lambda: {"wins": 0, "matches": 0, "total": 0})
    
    seed = args.seed_start
    scenario_num = 1
    total_combos = total_scenarios_to_run
    
    # Sweep through parameter combinations
    for combo in run_combinations:
        group_name = combo["group"]
        n_tasks = combo["n_tasks"]
        n_resources = combo["n_resources"]
        n_locations = combo["n_locations"]
        pair_label = combo["pair_label"]

        for i in range(args.num_scenarios):
            print(
                f"[{scenario_num:4d}/{total_combos}] "
                f"Group={group_name:<13s} Pair={pair_label:<7s} "
                f"Tasks={n_tasks:2d} Resources={n_resources} Locations={n_locations} Scenario={i+1}"
            )
                    # Generate scenario
            request, travel_matrix, _ = generate_scenario(
                n_resources=n_resources,
                n_tasks=n_tasks,
                n_locations=n_locations,
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

            # Run the configured objective schedules.
            objective_results = run_scenario(request, travel_matrix, objectives)
            flexibility_value = objective_results["flexibility"]["flexibility"]
            earliest_completion_time_flexibility_value = objective_results["earliest_completion_time"]["flexibility"]
            makespan_flexibility_value = objective_results["makespan"]["flexibility"]
            is_flexibility_winner = flexibility_value >= earliest_completion_time_flexibility_value
            is_flexibility_winner_vs_makespan = flexibility_value >= makespan_flexibility_value
            if args.include_slack:
                slack_flexibility_value = objective_results["slack"]["flexibility"]
                is_flexibility_winner_vs_slack = flexibility_value >= slack_flexibility_value
                is_flexibility_winner_vs_all = flexibility_value >= max(
                    earliest_completion_time_flexibility_value,
                    makespan_flexibility_value,
                    slack_flexibility_value,
                )
            else:
                slack_flexibility_value = np.nan
                is_flexibility_winner_vs_slack = False
                is_flexibility_winner_vs_all = flexibility_value >= max(
                    earliest_completion_time_flexibility_value,
                    makespan_flexibility_value,
                )

            # Compare the flexibility schedule's earliest completion value to other schedules.
            flexibility_earliest_completion_time = objective_results["flexibility"]["earliest_completion_time"]
            earliest_completion_time_schedule_value = objective_results["earliest_completion_time"]["earliest_completion_time"]
            makespan_schedule_earliest_completion_time = objective_results["makespan"]["earliest_completion_time"]
            is_earliest_completion_time_match = (
                flexibility_earliest_completion_time == earliest_completion_time_schedule_value
            )
            if args.include_slack:
                slack_schedule_earliest_completion_time = objective_results["slack"]["earliest_completion_time"]
                is_slack_match = (
                    flexibility_earliest_completion_time == slack_schedule_earliest_completion_time
                )
            else:
                slack_schedule_earliest_completion_time = np.nan
                is_slack_match = False

            flexibility_pct_diff = 0.0
            if earliest_completion_time_flexibility_value != 0:
                flexibility_pct_diff = (
                    (flexibility_value - earliest_completion_time_flexibility_value)
                    / abs(earliest_completion_time_flexibility_value)
                ) * 100.0

            earliest_completion_time_pct_diff = 0.0
            if earliest_completion_time_schedule_value != 0:
                earliest_completion_time_pct_diff = (
                    (flexibility_earliest_completion_time - earliest_completion_time_schedule_value)
                    / abs(earliest_completion_time_schedule_value)
                ) * 100.0

            makespan_earliest_completion_time_pct_diff = 0.0
            if makespan_schedule_earliest_completion_time != 0:
                makespan_earliest_completion_time_pct_diff = (
                    (flexibility_earliest_completion_time - makespan_schedule_earliest_completion_time)
                    / abs(makespan_schedule_earliest_completion_time)
                ) * 100.0

            makespan_flexibility_pct_diff = 0.0
            if makespan_flexibility_value != 0:
                makespan_flexibility_pct_diff = (
                    (flexibility_value - makespan_flexibility_value)
                    / abs(makespan_flexibility_value)
                ) * 100.0

            slack_flexibility_pct_diff = 0.0
            slack_earliest_completion_time_pct_diff = 0.0
            if args.include_slack and slack_flexibility_value != 0:
                slack_flexibility_pct_diff = ((flexibility_value - slack_flexibility_value) / abs(slack_flexibility_value)) * 100.0

            if args.include_slack and slack_schedule_earliest_completion_time != 0:
                slack_earliest_completion_time_pct_diff = (
                    (flexibility_earliest_completion_time - slack_schedule_earliest_completion_time)
                    / abs(slack_schedule_earliest_completion_time)
                ) * 100.0

            flexibility_wins += is_flexibility_winner
            flexibility_wins_vs_makespan += is_flexibility_winner_vs_makespan
            flexibility_wins_vs_slack += is_flexibility_winner_vs_slack
            flexibility_wins_vs_all += is_flexibility_winner_vs_all
            earliest_completion_time_matches += is_earliest_completion_time_match
            slack_matches += is_slack_match
            total_scenarios += 1

            # Track wins by parameter combination
            param_key = f"T{n_tasks}_R{n_resources}_L{n_locations}"
            wins_by_param[param_key]["total"] += 1
            if is_flexibility_winner:
                wins_by_param[param_key]["wins"] += 1

            if args.include_slack:
                slack_wins_by_param[param_key]["total"] += 1
                if is_flexibility_winner_vs_slack:
                    slack_wins_by_param[param_key]["wins"] += 1

            makespan_wins_by_param[param_key]["total"] += 1
            if is_flexibility_winner_vs_makespan:
                makespan_wins_by_param[param_key]["wins"] += 1

            all_wins_by_param[param_key]["total"] += 1
            if is_flexibility_winner_vs_all:
                all_wins_by_param[param_key]["wins"] += 1

            earliest_completion_time_matches_by_param[param_key]["total"] += 1
            if is_earliest_completion_time_match:
                earliest_completion_time_matches_by_param[param_key]["matches"] += 1

            if args.include_slack:
                slack_matches_by_param[param_key]["total"] += 1
                if is_slack_match:
                    slack_matches_by_param[param_key]["matches"] += 1

            profile_stats[group_name]["wins"] += is_flexibility_winner
            profile_stats[group_name].setdefault("makespan_wins", 0)
            profile_stats[group_name]["makespan_wins"] += is_flexibility_winner_vs_makespan
            profile_stats[group_name].setdefault("all_wins", 0)
            profile_stats[group_name]["all_wins"] += is_flexibility_winner_vs_all
            profile_stats[group_name]["matches"] += is_earliest_completion_time_match
            profile_stats[group_name]["total"] += 1
            profile_stats[group_name].setdefault("flexibility_pct_diff_sum", 0.0)
            profile_stats[group_name].setdefault("earliest_completion_time_pct_diff_sum", 0.0)
            profile_stats[group_name].setdefault("makespan_earliest_completion_time_pct_diff_sum", 0.0)
            profile_stats[group_name].setdefault("makespan_flexibility_pct_diff_sum", 0.0)
            profile_stats[group_name]["flexibility_pct_diff_sum"] += flexibility_pct_diff
            profile_stats[group_name]["earliest_completion_time_pct_diff_sum"] += earliest_completion_time_pct_diff
            profile_stats[group_name]["makespan_earliest_completion_time_pct_diff_sum"] += makespan_earliest_completion_time_pct_diff
            profile_stats[group_name]["makespan_flexibility_pct_diff_sum"] += makespan_flexibility_pct_diff
            if args.include_slack:
                profile_stats[group_name].setdefault("slack_wins", 0)
                profile_stats[group_name]["slack_wins"] += is_flexibility_winner_vs_slack
                profile_stats[group_name].setdefault("slack_matches", 0)
                profile_stats[group_name]["slack_matches"] += is_slack_match
                profile_stats[group_name].setdefault("slack_flexibility_pct_diff_sum", 0.0)
                profile_stats[group_name].setdefault("slack_earliest_completion_time_pct_diff_sum", 0.0)
                profile_stats[group_name]["slack_flexibility_pct_diff_sum"] += slack_flexibility_pct_diff
                profile_stats[group_name]["slack_earliest_completion_time_pct_diff_sum"] += slack_earliest_completion_time_pct_diff

            # Store detailed result
            result = {
                "scenario_num": scenario_num,
                "profile": group_name,
                "pair": pair_label,
                "n_tasks": n_tasks,
                "n_resources": n_resources,
                "n_locations": n_locations,
                "seed": seed,
                "flexibility_wins": is_flexibility_winner,
                "flexibility_wins_vs_makespan": is_flexibility_winner_vs_makespan,
                "flexibility_wins_vs_slack": is_flexibility_winner_vs_slack,
                "flexibility_wins_vs_all": is_flexibility_winner_vs_all,
                "flexibility_objective_value": flexibility_value,
                "earliest_completion_time_objective_flexibility_value": earliest_completion_time_flexibility_value,
                "makespan_objective_flexibility_value": makespan_flexibility_value,
                "slack_objective_flexibility_value": slack_flexibility_value,
                "flexibility_earliest_completion_time": flexibility_earliest_completion_time,
                "earliest_completion_time_schedule_value": earliest_completion_time_schedule_value,
                "makespan_schedule_earliest_completion_time": makespan_schedule_earliest_completion_time,
                "slack_schedule_earliest_completion_time": slack_schedule_earliest_completion_time,
                "same_earliest_completion_time_as_earliest_completion_time_schedule": is_earliest_completion_time_match,
                "same_earliest_completion_time_as_slack_schedule": is_slack_match,
                "flexibility_vs_earliest_completion_time_flexibility_delta": (
                    flexibility_value - earliest_completion_time_flexibility_value
                ),
                "flexibility_vs_earliest_completion_time_flexibility_pct_diff": flexibility_pct_diff,
                "flexibility_vs_earliest_completion_time_schedule_earliest_completion_time_pct_diff": earliest_completion_time_pct_diff,
                "flexibility_vs_makespan_flexibility_delta": (
                    flexibility_value - makespan_flexibility_value
                ),
                "flexibility_vs_makespan_flexibility_pct_diff": makespan_flexibility_pct_diff,
                "flexibility_vs_makespan_schedule_earliest_completion_time_pct_diff": makespan_earliest_completion_time_pct_diff,
                "flexibility_vs_slack_flexibility_delta": (flexibility_value - slack_flexibility_value if args.include_slack else np.nan),
                "flexibility_vs_slack_flexibility_pct_diff": slack_flexibility_pct_diff if args.include_slack else np.nan,
                "flexibility_vs_slack_schedule_earliest_completion_time_pct_diff": slack_earliest_completion_time_pct_diff if args.include_slack else np.nan,
            }

            all_results.append(result)

            seed += 1
            scenario_num += 1
    
    # Generate report
    print("\n" + "=" * 100)
    print("RESULTS SUMMARY")
    print("=" * 100)
    
    win_rate = (flexibility_wins / total_scenarios) * 100 if total_scenarios > 0 else 0
    print(
        f"\nFLEXIBILITY VS EARLIEST_COMPLETION_TIME FLEXIBILITY WIN RATE: "
        f"{flexibility_wins}/{total_scenarios} ({win_rate:.1f}%)"
    )

    makespan_win_rate = (flexibility_wins_vs_makespan / total_scenarios) * 100 if total_scenarios > 0 else 0
    print(
        f"FLEXIBILITY VS MAKESPAN FLEXIBILITY WIN RATE: "
        f"{flexibility_wins_vs_makespan}/{total_scenarios} ({makespan_win_rate:.1f}%)"
    )

    if args.include_slack:
        slack_win_rate = (flexibility_wins_vs_slack / total_scenarios) * 100 if total_scenarios > 0 else 0
        print(f"FLEXIBILITY VS SLACK FLEXIBILITY WIN RATE: {flexibility_wins_vs_slack}/{total_scenarios} ({slack_win_rate:.1f}%)")

    all_win_rate = (flexibility_wins_vs_all / total_scenarios) * 100 if total_scenarios > 0 else 0
    print(f"FLEXIBILITY VS ALL OBJECTIVES WIN RATE: {flexibility_wins_vs_all}/{total_scenarios} ({all_win_rate:.1f}%)")

    earliest_completion_time_match_rate = (
        (earliest_completion_time_matches / total_scenarios) * 100 if total_scenarios > 0 else 0
    )
    print(
        "FLEXIBILITY VS EARLIEST_COMPLETION_TIME MATCH RATE: "
        f"{earliest_completion_time_matches}/{total_scenarios} ({earliest_completion_time_match_rate:.1f}%)"
    )

    if args.include_slack:
        slack_match_rate = (slack_matches / total_scenarios) * 100 if total_scenarios > 0 else 0
        print(f"FLEXIBILITY VS SLACK MATCH RATE: {slack_matches}/{total_scenarios} ({slack_match_rate:.1f}%)")

    summary_label = "Scenario Level Summary" if args.combination_mode == "profiles" else "Scenario Pair Summary"
    print(f"\n{summary_label}:")
    if args.include_slack:
        print(
            f"{'Level':<15s} {'Scenarios':<10s} {'Win% ECT':<10s} {'Win% MS':<10s} {'Win% Slack':<11s} "
            f"{'Win% All':<9s} {'Same ECT%':<11s} {'Same Slack%':<12s} "
            f"{'Avg Flex% ECT':<14s} {'Avg ECT% ECT':<14s} {'Avg Flex% MS':<13s} {'Avg Flex% Slack':<15s} {'Avg ECT% Slack':<15s}"
        )
        print(f"{'-'*165}")
    else:
        print(f"{'Level':<15s} {'Scenarios':<10s} {'Win% ECT':<10s} {'Win% MS':<10s} {'Win% All':<9s} {'Same ECT%':<11s} {'Avg Flex% ECT':<14s} {'Avg ECT% ECT':<14s} {'Avg Flex% MS':<13s}")
        print(f"{'-'*115}")

    if args.combination_mode == "profiles":
        level_order = ["sparse", "medium_sparse", "dense"]
    else:
        level_order = [f"T{t}_R{r}" for t, r in TARGET_PAIRS]
    profile_summary_rows = []
    for level_name in level_order:
        data = profile_stats[level_name]
        if data["total"] == 0:
            continue
        flex_rate = (data["wins"] / data["total"]) * 100
        all_flex_rate = (data.get("all_wins", 0) / data["total"]) * 100
        makespan_flex_rate = (data.get("makespan_wins", 0) / data["total"]) * 100
        ms_rate = (data["matches"] / data["total"]) * 100
        flex_pct_diff = data.get("flexibility_pct_diff_sum", 0.0) / data["total"]
        earliest_completion_time_pct_diff = data.get("earliest_completion_time_pct_diff_sum", 0.0) / data["total"]
        makespan_earliest_completion_time_pct_diff = data.get("makespan_earliest_completion_time_pct_diff_sum", 0.0) / data["total"]
        makespan_flex_pct_diff = data.get("makespan_flexibility_pct_diff_sum", 0.0) / data["total"]
        if args.include_slack:
            slack_flex_rate = (data.get("slack_wins", 0) / data["total"]) * 100
            slack_ms_rate = (data.get("slack_matches", 0) / data["total"]) * 100
            slack_flex_pct_diff = data.get("slack_flexibility_pct_diff_sum", 0.0) / data["total"]
            slack_earliest_completion_time_pct_diff = data.get("slack_earliest_completion_time_pct_diff_sum", 0.0) / data["total"]
            profile_summary_rows.append({
                "Level": level_name,
                "Scenarios": data["total"],
                "Flexibility_Win_Pct_Vs_Earliest_Completion_Time": flex_rate,
                "Flexibility_Win_Pct_Vs_Makespan": makespan_flex_rate,
                "Flexibility_Win_Pct_Vs_Slack": slack_flex_rate,
                "Flexibility_Win_Pct_Vs_All": all_flex_rate,
                "Same_Earliest_Completion_Time_Pct_Vs_Earliest_Completion_Time": ms_rate,
                "Same_Earliest_Completion_Time_Pct_Vs_Slack": slack_ms_rate,
                "Avg_Flexibility_Pct_Diff_Vs_Earliest_Completion_Time": flex_pct_diff,
                "Avg_Earliest_Completion_Time_Pct_Diff_Vs_Earliest_Completion_Time": earliest_completion_time_pct_diff,
                "Avg_Flexibility_Pct_Diff_Vs_Makespan": makespan_flex_pct_diff,
                "Avg_Earliest_Completion_Time_Pct_Diff_Vs_Makespan": makespan_earliest_completion_time_pct_diff,
                "Avg_Flexibility_Pct_Diff_Vs_Slack": slack_flex_pct_diff,
                "Avg_Earliest_Completion_Time_Pct_Diff_Vs_Slack": slack_earliest_completion_time_pct_diff,
            })
            print(
                f"{level_name:<15s} {data['total']:<10d} "
                f"{flex_rate:>8.1f}% {makespan_flex_rate:>8.1f}% {slack_flex_rate:>10.1f}% {all_flex_rate:>8.1f}% "
                f"{ms_rate:>10.1f}% {slack_ms_rate:>11.1f}% "
                f"{flex_pct_diff:>13.2f}% {earliest_completion_time_pct_diff:>13.2f}% {makespan_flex_pct_diff:>12.2f}% "
                f"{slack_flex_pct_diff:>14.2f}% {slack_earliest_completion_time_pct_diff:>14.2f}%"
            )
        else:
            profile_summary_rows.append({
                "Level": level_name,
                "Scenarios": data["total"],
                "Flexibility_Win_Pct_Vs_Earliest_Completion_Time": flex_rate,
                "Flexibility_Win_Pct_Vs_Makespan": makespan_flex_rate,
                "Flexibility_Win_Pct_Vs_All": all_flex_rate,
                "Same_Earliest_Completion_Time_Pct_Vs_Earliest_Completion_Time": ms_rate,
                "Avg_Flexibility_Pct_Diff_Vs_Earliest_Completion_Time": flex_pct_diff,
                "Avg_Earliest_Completion_Time_Pct_Diff_Vs_Earliest_Completion_Time": earliest_completion_time_pct_diff,
                "Avg_Flexibility_Pct_Diff_Vs_Makespan": makespan_flex_pct_diff,
                "Avg_Earliest_Completion_Time_Pct_Diff_Vs_Makespan": makespan_earliest_completion_time_pct_diff,
            })
            print(
                f"{level_name:<15s} {data['total']:<10d} "
                f"{flex_rate:>8.1f}% {makespan_flex_rate:>8.1f}% {all_flex_rate:>8.1f}% "
                f"{ms_rate:>10.1f}% {flex_pct_diff:>13.2f}% {earliest_completion_time_pct_diff:>13.2f}% {makespan_flex_pct_diff:>12.2f}%"
            )
    
    # Breakdown by parameter combination
    print(f"\nWin Rate by Parameter Combination:")
    if args.include_slack:
        print(f"{'Parameter':<30s} {'WinsECT':<10s} {'RateECT':<10s} {'WinsMS':<10s} {'RateMS':<10s} {'WinsSlack':<10s} {'RateSlack':<10s} {'WinsAll':<10s} {'RateAll':<10s}")
        print(f"{'-'*115}")
    else:
        print(f"{'Parameter':<30s} {'WinsECT':<10s} {'RateECT':<10s} {'WinsMS':<10s} {'RateMS':<10s} {'WinsAll':<10s} {'RateAll':<10s}")
        print(f"{'-'*95}")
    
    for param_key in sorted(wins_by_param.keys()):
        data_mksp = wins_by_param[param_key]
        rate_mksp = (data_mksp["wins"] / data_mksp["total"]) * 100
        data_makespan = makespan_wins_by_param[param_key]
        rate_makespan = (data_makespan["wins"] / data_makespan["total"]) * 100
        data_all = all_wins_by_param[param_key]
        rate_all = (data_all["wins"] / data_all["total"]) * 100
        if args.include_slack:
            data_slack = slack_wins_by_param[param_key]
            rate_slack = (data_slack["wins"] / data_slack["total"]) * 100
            print(
                f"{param_key:<30s} {data_mksp['wins']:<10d} {rate_mksp:>8.1f}% "
                f"{data_makespan['wins']:<10d} {rate_makespan:>8.1f}% "
                f"{data_slack['wins']:<10d} {rate_slack:>9.1f}% "
                f"{data_all['wins']:<10d} {rate_all:>7.1f}%"
            )
        else:
            print(
                f"{param_key:<30s} {data_mksp['wins']:<10d} {rate_mksp:>8.1f}% "
                f"{data_makespan['wins']:<10d} {rate_makespan:>8.1f}% "
                f"{data_all['wins']:<10d} {rate_all:>7.1f}%"
            )

    print(f"\nEarliest Completion Time Match Rate by Parameter Combination:")
    if args.include_slack:
        print(f"{'Parameter':<30s} {'MatchECT':<10s} {'RateECT':<10s} {'MatchSlack':<11s} {'RateSlack':<10s}")
        print(f"{'-'*80}")
    else:
        print(f"{'Parameter':<30s} {'MatchECT':<10s} {'RateECT':<10s}")
        print(f"{'-'*50}")

    for param_key in sorted(earliest_completion_time_matches_by_param.keys()):
        data_mksp = earliest_completion_time_matches_by_param[param_key]
        rate_mksp = (data_mksp["matches"] / data_mksp["total"]) * 100
        if args.include_slack:
            data_slack = slack_matches_by_param[param_key]
            rate_slack = (data_slack["matches"] / data_slack["total"]) * 100
            print(
                f"{param_key:<30s} {data_mksp['matches']:<10d} {rate_mksp:>8.1f}% "
                f"{data_slack['matches']:<11d} {rate_slack:>9.1f}%"
            )
        else:
            print(f"{param_key:<30s} {data_mksp['matches']:<10d} {rate_mksp:>8.1f}%")
    
    # Save detailed results to CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Detailed results
    df_results = pd.DataFrame(all_results)
    csv_detailed = output_dir / f"flexibility_sweep_detailed_{timestamp}.csv"
    df_results.to_csv(csv_detailed, index=False)
    print(f"\nDetailed results saved to: {csv_detailed}")
    
    # Summary by parameter combination
    summary_data = []
    for param_key in sorted(wins_by_param.keys()):
        data = wins_by_param[param_key]
        rate = (data["wins"] / data["total"]) * 100
        # Extract parameters
        parts = param_key.split("_")
        n_tasks = int(parts[0][1:])
        n_resources = int(parts[1][1:])
        n_locations = int(parts[2][1:])
        
        summary_data.append({
            "Tasks": n_tasks,
            "Resources": n_resources,
            "Locations": n_locations,
            "Total_Scenarios": data["total"],
            "Flexibility_Wins_Vs_Earliest_Completion_Time": data["wins"],
            "Win_Rate_Vs_Earliest_Completion_Time": rate,
            "Flexibility_Wins_Vs_Makespan": makespan_wins_by_param[param_key]["wins"],
            "Win_Rate_Vs_Makespan": (
                makespan_wins_by_param[param_key]["wins"]
                / makespan_wins_by_param[param_key]["total"]
            ) * 100,
            "Flexibility_Wins_Vs_All": all_wins_by_param[param_key]["wins"],
            "Win_Rate_Vs_All": (all_wins_by_param[param_key]["wins"] / all_wins_by_param[param_key]["total"]) * 100,
            "Earliest_Completion_Time_Matches": earliest_completion_time_matches_by_param[param_key]["matches"],
            "Earliest_Completion_Time_Match_Rate": (
                earliest_completion_time_matches_by_param[param_key]["matches"]
                / earliest_completion_time_matches_by_param[param_key]["total"]
            ) * 100,
        })
        if args.include_slack:
            summary_data[-1].update({
                "Flexibility_Wins_Vs_Slack": slack_wins_by_param[param_key]["wins"],
                "Win_Rate_Vs_Slack": (slack_wins_by_param[param_key]["wins"] / slack_wins_by_param[param_key]["total"]) * 100,
                "Slack_Matches": slack_matches_by_param[param_key]["matches"],
                "Slack_Match_Rate": (slack_matches_by_param[param_key]["matches"] / slack_matches_by_param[param_key]["total"]) * 100,
            })
    
    df_summary = pd.DataFrame(summary_data)
    csv_summary = output_dir / f"flexibility_sweep_summary_{timestamp}.csv"
    df_summary.to_csv(csv_summary, index=False)
    print(f"Summary results saved to: {csv_summary}")

    df_profile_summary = pd.DataFrame(profile_summary_rows)
    csv_profile_summary = output_dir / f"flexibility_sweep_profile_summary_{timestamp}.csv"
    df_profile_summary.to_csv(csv_profile_summary, index=False)
    print(f"Profile summary saved to: {csv_profile_summary}")
    
    # Statistics on flexibility values
    print(f"\n" + "=" * 100)
    print("FLEXIBILITY VALUE STATISTICS")
    print("=" * 100)
    
    flexibility_col = df_results["flexibility_objective_value"]
    earliest_completion_time_flexibility_col = df_results["earliest_completion_time_objective_flexibility_value"]
    makespan_flexibility_col = df_results["makespan_objective_flexibility_value"]
    slack_flexibility_col = df_results["slack_objective_flexibility_value"] if args.include_slack else None

    print(f"\nFlexibility schedule - Flexibility Values:")
    print(f"  Mean:   {flexibility_col.mean():.2f}")
    print(f"  Median: {flexibility_col.median():.2f}")
    print(f"  Std:    {flexibility_col.std():.2f}")
    print(f"  Min:    {flexibility_col.min():.2f}")
    print(f"  Max:    {flexibility_col.max():.2f}")
    
    print(f"\nEarliest completion time schedule - Flexibility Values:")
    print(f"  Mean:   {earliest_completion_time_flexibility_col.mean():.2f}")
    print(f"  Median: {earliest_completion_time_flexibility_col.median():.2f}")
    print(f"  Std:    {earliest_completion_time_flexibility_col.std():.2f}")
    print(f"  Min:    {earliest_completion_time_flexibility_col.min():.2f}")
    print(f"  Max:    {earliest_completion_time_flexibility_col.max():.2f}")

    print(f"\nMakespan schedule - Flexibility Values:")
    print(f"  Mean:   {makespan_flexibility_col.mean():.2f}")
    print(f"  Median: {makespan_flexibility_col.median():.2f}")
    print(f"  Std:    {makespan_flexibility_col.std():.2f}")
    print(f"  Min:    {makespan_flexibility_col.min():.2f}")
    print(f"  Max:    {makespan_flexibility_col.max():.2f}")

    if args.include_slack:
        print(f"\nSlack schedule - Flexibility Values:")
        print(f"  Mean:   {slack_flexibility_col.mean():.2f}")
        print(f"  Median: {slack_flexibility_col.median():.2f}")
        print(f"  Std:    {slack_flexibility_col.std():.2f}")
        print(f"  Min:    {slack_flexibility_col.min():.2f}")
        print(f"  Max:    {slack_flexibility_col.max():.2f}")

    flex_pct_diff_col = df_results["flexibility_vs_earliest_completion_time_flexibility_pct_diff"]
    makespan_flex_pct_diff_col = df_results["flexibility_vs_makespan_flexibility_pct_diff"]
    makespan_earliest_completion_time_pct_diff_col = (
        df_results["flexibility_vs_makespan_schedule_earliest_completion_time_pct_diff"]
    )
    earliest_completion_time_pct_diff_col = (
        df_results["flexibility_vs_earliest_completion_time_schedule_earliest_completion_time_pct_diff"]
    )
    slack_flex_pct_diff_col = df_results["flexibility_vs_slack_flexibility_pct_diff"] if args.include_slack else None
    slack_earliest_completion_time_pct_diff_col = (
        df_results["flexibility_vs_slack_schedule_earliest_completion_time_pct_diff"] if args.include_slack else None
    )

    print(f"\nAverage Percent Difference vs Earliest Completion Time Schedule:")
    print(f"  Flexibility value pct diff: {flex_pct_diff_col.mean():.2f}%")
    print(f"  Earliest completion time pct diff: {earliest_completion_time_pct_diff_col.mean():.2f}%")

    print(f"\nAverage Percent Difference vs Makespan Schedule:")
    print(f"  Flexibility value pct diff: {makespan_flex_pct_diff_col.mean():.2f}%")
    print(f"  Earliest completion time pct diff: {makespan_earliest_completion_time_pct_diff_col.mean():.2f}%")

    if args.include_slack:
        print(f"\nAverage Percent Difference vs Slack Schedule:")
        print(f"  Flexibility value pct diff: {slack_flex_pct_diff_col.mean():.2f}%")
        print(f"  Earliest completion time pct diff: {slack_earliest_completion_time_pct_diff_col.mean():.2f}%")

    flex_earliest_completion_time_col = df_results["flexibility_earliest_completion_time"]
    earliest_completion_time_sched_col = df_results["earliest_completion_time_schedule_value"]
    makespan_sched_col = df_results["makespan_schedule_earliest_completion_time"]
    slack_sched_col = df_results["slack_schedule_earliest_completion_time"] if args.include_slack else None
    print(f"\nEarliest Completion Time Comparison:")
    print(
        "  Flexibility schedule mean earliest completion time value: "
        f"{flex_earliest_completion_time_col.mean():.2f}"
    )
    print(
        "  Earliest completion time schedule mean value:             "
        f"{earliest_completion_time_sched_col.mean():.2f}"
    )
    print(
        "  Makespan schedule mean earliest completion time value:    "
        f"{makespan_sched_col.mean():.2f}"
    )
    if args.include_slack:
        print(
            "  Slack schedule mean earliest completion time value:       "
            f"{slack_sched_col.mean():.2f}"
        )
    print(
        "  Equal earliest completion time count:                     "
        f"{earliest_completion_time_matches}/{total_scenarios}"
    )
    if args.include_slack:
        print(
            "  Equal slack earliest completion time count:               "
            f"{slack_matches}/{total_scenarios}"
        )

    print(f"\n" + "=" * 100)
    print(f"Sweep complete!")
    print(f"=" * 100)


if __name__ == "__main__":
    main()
