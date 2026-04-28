#!/usr/bin/env python3
"""
Empirical sweep to compare flexibility values between the flexibility schedule
and the slack schedule.

This script mirrors flexibility_objective_sweep.py, but only evaluates:
- objective_metric='flexibility'
- objective_metric='slack'
"""

import sys
from pathlib import Path

# Add parent directory to path to import local modules
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import copy
from collections import defaultdict
import pandas as pd
import plotly.graph_objects as go

from scenario_generator import generate_scenario
from tds_slack.executer import run_scheduler


OBJECTIVES = {
    "flexibility": {"minimize": False},
    "slack": {"minimize": False},
}


# Default pair set spanning ratios 3.0 to 8.0 in 0.5 increments.
TARGET_PAIRS = [
    (12, 5),
    (14, 5),
    (16, 5),
    (18, 5),
    (20, 5),
    (22, 5),
    (24, 5),
    (26, 5),
    (28, 5),
    (30, 5),
    (32, 5)
]


SCENARIO_PROFILES = {
    "sparse": {
        "task_counts": [4, 5, 6, 8],
        "resource_counts": [11, 13],
        "location_count": 3,
    },
    "medium_sparse": {
        "task_counts": [9, 11, 12, 15, 17],
        "resource_counts": [6, 7, 9],
        "location_count": 3,
    },
    "dense": {
        "task_counts": [19, 22, 25],
        "resource_counts": [4, 5],
        "location_count": 3,
    },
}


def validate_profile_ratios(selected_profiles):
    """No-op: duplicate/overlapping ratios are allowed."""
    _ = selected_profiles


def parse_args():
    p = argparse.ArgumentParser(
        description="Sweep scenarios to compare flexibility values: flexibility schedule vs slack schedule",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Sweep parameters
    p.add_argument("--num-scenarios", type=int, default=50,
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

    # Fixed parameters
    p.add_argument("--min-caps", type=int, default=1)
    p.add_argument("--max-caps", type=int, default=5)
    p.add_argument("--min-duration", type=int, default=15)
    p.add_argument("--max-duration", type=int, default=200)
    p.add_argument("--min-slack", type=int, default=0)
    p.add_argument("--max-slack", type=int, default=800)
    p.add_argument("--horizon", type=int, default=1440)
    p.add_argument("--capability-overlap", type=float, default=0.8)
    p.add_argument("--min-travel", type=int, default=3)
    p.add_argument("--max-travel", type=int, default=30)

    return p.parse_args()


def get_flexibility_value(tds):
    return tds.sum_total_flexibility()


def validate_target_pairs(target_pairs):
    """No-op: duplicate ratios in target pairs are allowed."""
    _ = target_pairs


def run_scenario(request, travel_matrix):
    """Run flexibility and slack objectives on a scenario and return values."""
    results = {}

    for objective in OBJECTIVES.keys():
        request_copy = copy.deepcopy(request)
        travel_matrix_copy = copy.deepcopy(travel_matrix)

        tds = run_scheduler(
            request_copy,
            travel_matrix_copy,
            objective_metric=objective,
            minimize=OBJECTIVES[objective]["minimize"],
        )

        results[objective] = {
            "flexibility": get_flexibility_value(tds),
        }

    return results


def build_avg_flexibility_comparison_plot(df_results):
    """Build an interactive plot comparing average flexibility by ratio."""
    grouped = (
        df_results.groupby(["n_tasks", "n_resources"], as_index=False)
        .agg(
            mean_flex=("flexibility_objective_value", "mean"),
            mean_slack=("slack_objective_flexibility_value", "mean"),
        )
        .sort_values(["n_tasks", "n_resources"])
        .reset_index(drop=True)
    )

    grouped["tasks_per_resource"] = grouped["n_tasks"] / grouped["n_resources"]
    grouped["label"] = grouped["n_tasks"].astype(str) + "/" + grouped["n_resources"].astype(str)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_flex"],
            mode="markers+lines+text",
            text=grouped["label"],
            textposition="top center",
            name="Flexibility Schedule",
            marker={"size": 9},
            line={"width": 2},
            hovertemplate=(
                "pair=%{text}<br>"
                "ratio=%{x:.2f}<br>"
                "avg flexibility=%{y:.2f}<extra></extra>"
            ),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_slack"],
            mode="markers+lines",
            name="Slack Schedule",
            marker={"size": 9},
            line={"width": 2},
            customdata=grouped["label"],
            hovertemplate=(
                "pair=%{customdata}<br>"
                "ratio=%{x:.2f}<br>"
                "avg flexibility=%{y:.2f}<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title="Average Flexibility vs Task/Resource Ratio",
        xaxis_title="Task/Resource Ratio (n_tasks / n_resources)",
        yaxis_title="Average Flexibility Value",
        template="plotly_white",
    )
    return fig


def main():
    args = parse_args()

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
    print("FLEXIBILITY VS SLACK FLEXIBILITY SWEEP")
    print("=" * 100)
    print("\nParameters:")
    print(f"  Combination mode: {args.combination_mode}")
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

    all_results = []
    flexibility_wins_vs_slack = 0
    total_scenarios = 0
    wins_by_param = defaultdict(lambda: {"wins": 0, "total": 0})
    profile_stats = defaultdict(lambda: {"wins": 0, "total": 0, "pct_diff_sum": 0.0})

    seed = args.seed_start
    scenario_num = 1

    for combo in run_combinations:
        group_name = combo["group"]
        n_tasks = combo["n_tasks"]
        n_resources = combo["n_resources"]
        n_locations = combo["n_locations"]
        pair_label = combo["pair_label"]

        for i in range(args.num_scenarios):
            print(
                f"[{scenario_num:4d}/{total_scenarios_to_run}] "
                f"Group={group_name:<13s} Pair={pair_label:<7s} "
                f"Tasks={n_tasks:2d} Resources={n_resources} Locations={n_locations} Scenario={i+1}"
            )

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

            objective_results = run_scenario(request, travel_matrix)
            flexibility_value = objective_results["flexibility"]["flexibility"]
            slack_value = objective_results["slack"]["flexibility"]

            is_flexibility_winner_vs_slack = flexibility_value >= slack_value

            pct_diff = 0.0
            if slack_value != 0:
                pct_diff = ((flexibility_value - slack_value) / abs(slack_value)) * 100.0

            flexibility_wins_vs_slack += is_flexibility_winner_vs_slack
            total_scenarios += 1

            param_key = f"T{n_tasks}_R{n_resources}_L{n_locations}"
            wins_by_param[param_key]["total"] += 1
            if is_flexibility_winner_vs_slack:
                wins_by_param[param_key]["wins"] += 1

            profile_stats[group_name]["wins"] += is_flexibility_winner_vs_slack
            profile_stats[group_name]["total"] += 1
            profile_stats[group_name]["pct_diff_sum"] += pct_diff

            result = {
                "scenario_num": scenario_num,
                "profile": group_name,
                "pair": pair_label,
                "n_tasks": n_tasks,
                "n_resources": n_resources,
                "n_locations": n_locations,
                "seed": seed,
                "flexibility_wins_vs_slack": is_flexibility_winner_vs_slack,
                "flexibility_objective_value": flexibility_value,
                "slack_objective_flexibility_value": slack_value,
                "flexibility_vs_slack_flexibility_delta": flexibility_value - slack_value,
                "flexibility_vs_slack_flexibility_pct_diff": pct_diff,
            }
            all_results.append(result)

            seed += 1
            scenario_num += 1

    print("\n" + "=" * 100)
    print("RESULTS SUMMARY")
    print("=" * 100)

    win_rate = (flexibility_wins_vs_slack / total_scenarios) * 100 if total_scenarios > 0 else 0
    print(f"\nFLEXIBILITY VS SLACK FLEXIBILITY WIN RATE: {flexibility_wins_vs_slack}/{total_scenarios} ({win_rate:.1f}%)")

    summary_label = "Scenario Level Summary" if args.combination_mode == "profiles" else "Scenario Pair Summary"
    print(f"\n{summary_label}:")
    print(f"{'Level':<15s} {'Scenarios':<10s} {'Win% Slack':<11s} {'Avg Flex% Slack':<15s}")
    print(f"{'-'*56}")

    level_order = ["sparse", "medium_sparse", "dense"] if args.combination_mode == "profiles" else [f"T{t}_R{r}" for t, r in TARGET_PAIRS]
    profile_summary_rows = []
    for level_name in level_order:
        data = profile_stats[level_name]
        if data["total"] == 0:
            continue
        slack_flex_rate = (data["wins"] / data["total"]) * 100
        avg_pct_diff = data["pct_diff_sum"] / data["total"]
        profile_summary_rows.append({
            "Level": level_name,
            "Scenarios": data["total"],
            "Flexibility_Win_Pct_Vs_Slack": slack_flex_rate,
            "Avg_Flexibility_Pct_Diff_Vs_Slack": avg_pct_diff,
        })
        print(
            f"{level_name:<15s} {data['total']:<10d} "
            f"{slack_flex_rate:>10.1f}% {avg_pct_diff:>14.2f}%"
        )

    print("\nWin Rate by Parameter Combination:")
    print(f"{'Parameter':<30s} {'WinsSlack':<10s} {'RateSlack':<10s}")
    print(f"{'-'*56}")

    for param_key in sorted(wins_by_param.keys()):
        data = wins_by_param[param_key]
        rate = (data["wins"] / data["total"]) * 100
        print(f"{param_key:<30s} {data['wins']:<10d} {rate:>9.1f}%")

    df_results = pd.DataFrame(all_results)

    print("\n" + "=" * 100)
    print("FLEXIBILITY VALUE STATISTICS")
    print("=" * 100)

    flexibility_col = df_results["flexibility_objective_value"]
    slack_flexibility_col = df_results["slack_objective_flexibility_value"]
    slack_flex_pct_diff_col = df_results["flexibility_vs_slack_flexibility_pct_diff"]

    print("\nFlexibility schedule - Flexibility Values:")
    print(f"  Mean:   {flexibility_col.mean():.2f}")
    print(f"  Median: {flexibility_col.median():.2f}")
    print(f"  Std:    {flexibility_col.std():.2f}")
    print(f"  Min:    {flexibility_col.min():.2f}")
    print(f"  Max:    {flexibility_col.max():.2f}")

    print("\nSlack schedule - Flexibility Values:")
    print(f"  Mean:   {slack_flexibility_col.mean():.2f}")
    print(f"  Median: {slack_flexibility_col.median():.2f}")
    print(f"  Std:    {slack_flexibility_col.std():.2f}")
    print(f"  Min:    {slack_flexibility_col.min():.2f}")
    print(f"  Max:    {slack_flexibility_col.max():.2f}")

    print("\nAverage Percent Difference vs Slack Schedule:")
    print(f"  Flexibility value pct diff: {slack_flex_pct_diff_col.mean():.2f}%")

    plot_fig = build_avg_flexibility_comparison_plot(df_results)
    print("\nDisplaying plot: Average Flexibility vs Task/Resource Ratio")
    plot_fig.show()

    print("\n" + "=" * 100)
    print("Sweep complete!")
    print("=" * 100)


if __name__ == "__main__":
    main()
