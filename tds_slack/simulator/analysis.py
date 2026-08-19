"""
Merge the per-scenario detail CSV (one row per scenario x combination) into a
wide, per-scenario table so combinations can be compared directly, and pull
out some summary statistics.

Input CSV columns (as produced by run_profile_across_combinations):
    profile, scenario, initial_metric, task_swap_metric, max_moves, minimize,
    dropped_tasks, schedule_generation_time, reschedule_time,
    reschedule_call_count, avg_reschedule_time

Usage:
    python compare_scenarios.py path/to/scenario_detail.csv \
        [--out wide_comparison.csv] [--metrics-csv metric_comparisons.csv]
"""

import argparse
import itertools
from pathlib import Path

import pandas as pd


VALUE_COLS = [
    "dropped_tasks",
    "schedule_generation_time",
    "reschedule_time",
    "reschedule_call_count",
    "avg_reschedule_time",
]


def load_detail_csv(path):
    df = pd.read_csv(path)

    # Backward compatibility: older detail CSVs had a single "metric"
    # column applied to both initialization and task swap.
    if "metric" in df.columns and not {"initial_metric", "task_swap_metric"} <= set(df.columns):
        df["initial_metric"] = df["metric"]
        df["task_swap_metric"] = df["metric"]

    missing = {"profile", "scenario", "initial_metric", "task_swap_metric", "max_moves"} - set(df.columns)
    if missing:
        raise ValueError(f"Input CSV is missing expected columns: {missing}")
    return df


def add_combo_label(df):
    """
    Give each (initial_metric, task_swap_metric, max_moves) combination a
    short, readable label. When the initial and task-swap objectives are
    the same (the common case), the label is just "<metric>_mm<max_moves>";
    when they differ, it's "<initial_metric>-<task_swap_metric>_mm<max_moves>".
    """
    df = df.copy()
    same_metric = df["initial_metric"] == df["task_swap_metric"]
    metric_label = df["initial_metric"].astype(str).where(
        same_metric,
        df["initial_metric"].astype(str) + "-" + df["task_swap_metric"].astype(str),
    )
    df["combo"] = metric_label + "_mm" + df["max_moves"].astype(str)
    return df


def make_wide_table(df):
    """
    Pivot so each row is a single scenario (within a profile), and each
    combination's metrics become their own columns, e.g.
    dropped_tasks__makespan_mm10, dropped_tasks__flexibility_mm0, etc.
    """
    df = add_combo_label(df)

    wide = df.pivot_table(
        index=["profile", "scenario"],
        columns="combo",
        values=VALUE_COLS,
        aggfunc="first",  # one row per (scenario, combo) expected; 'first' guards dupes
    )

    # Flatten the MultiIndex columns: (value_col, combo) -> "value_col__combo"
    wide.columns = [f"{value_col}__{combo}" for value_col, combo in wide.columns]
    wide = wide.reset_index().sort_values(["profile", "scenario"])
    return wide


def pairwise_combo_comparisons(df, value_col="dropped_tasks"):
    """
    For every pair of combinations, compare `value_col` scenario-by-scenario
    and return summary stats: how often they agree, how often each combo
    "wins" (lower value), and the average/max difference.
    """
    df = add_combo_label(df)
    combos = sorted(df["combo"].unique())

    rows = []
    for combo_a, combo_b in itertools.combinations(combos, 2):
        sub_a = df[df["combo"] == combo_a][["profile", "scenario", value_col]]
        sub_b = df[df["combo"] == combo_b][["profile", "scenario", value_col]]
        merged = sub_a.merge(
            sub_b, on=["profile", "scenario"], suffixes=(f"__{combo_a}", f"__{combo_b}")
        )

        col_a = f"{value_col}__{combo_a}"
        col_b = f"{value_col}__{combo_b}"
        diff = merged[col_a] - merged[col_b]

        rows.append(
            {
                "combo_a": combo_a,
                "combo_b": combo_b,
                "n_scenarios_compared": len(merged),
                "n_equal": int((diff == 0).sum()),
                "n_a_lower": int((diff < 0).sum()),
                "n_b_lower": int((diff > 0).sum()),
                "mean_diff_a_minus_b": diff.mean(),
                "max_abs_diff": diff.abs().max(),
            }
        )

    return pd.DataFrame(rows)


def combo_level_summary(df):
    """One row per combination: totals/means across all scenarios."""
    df = add_combo_label(df)
    summary = (
        df.groupby("combo")[VALUE_COLS]
        .agg(["sum", "mean", "std"])
    )
    summary.columns = [f"{col}__{stat}" for col, stat in summary.columns]
    return summary.reset_index().sort_values("combo")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("detail_csv", type=Path, help="Path to the scenario_detail.csv")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Where to write the wide per-scenario CSV (default: <input>_wide.csv)",
    )
    parser.add_argument(
        "--metrics-csv", type=Path, default=None,
        help="Where to write pairwise dropped_tasks comparison CSV (default: <input>_pairwise.csv)",
    )
    args = parser.parse_args()

    out_path = args.out or args.detail_csv.with_name(args.detail_csv.stem + "_wide.csv")
    pairwise_path = args.metrics_csv or args.detail_csv.with_name(args.detail_csv.stem + "_pairwise.csv")

    df = load_detail_csv(args.detail_csv)

    wide = make_wide_table(df)
    wide.to_csv(out_path, index=False)
    print(f"Wrote wide per-scenario comparison ({len(wide)} rows) to {out_path}")

    pairwise = pairwise_combo_comparisons(df, value_col="dropped_tasks")
    pairwise.to_csv(pairwise_path, index=False)
    print(f"\nPairwise dropped_tasks comparison across combinations:")
    print(pairwise.to_string(index=False))
    print(f"\nWrote pairwise comparison to {pairwise_path}")

    combo_summary = combo_level_summary(df)
    print(f"\nPer-combination summary (totals/mean/std across scenarios):")
    print(combo_summary.to_string(index=False))


if __name__ == "__main__":
    main()