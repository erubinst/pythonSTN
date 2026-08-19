#!/usr/bin/env python3
"""
Plot a workload x pressure crossover heatmap from the combined combination
summary CSV produced by combine_summaries.py.

Each cell of the grid shows how much better/worse the makespan approach
did vs. the flexibility approach on a chosen metric, for a given
workload_tier x pressure_tier combination. Positive values (one color) mean
flexibility outperformed makespan; negative values (the other color) mean
makespan outperformed flexibility -- a diverging colormap centered at 0
makes the crossover visually obvious.

Usage:
    python plot_grid_heatmap.py --input generated_scenarios/combined_combination_summary.csv
    python plot_grid_heatmap.py --input combined.csv --metric reschedule_time --max-moves 0
    python plot_grid_heatmap.py --input combined.csv --max-moves both
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Tier display order (not alphabetical) so the grid reads low-to-high.
WORKLOAD_ORDER = ["sparse", "medium", "dense"]
PRESSURE_ORDER = ["loose", "medium", "tight"]

# metric name -> (numerator column, denominator column or None, display label)
# denominator column, if given, converts a total into a per-scenario average.
# 'dropped' uses the raw total rather than an average -- with only a handful
# of drops per scenario across 100 scenarios, per-scenario averages round
# down to near-meaningless fractions, so the total is the more legible
# number here.
METRICS = {
    "dropped": ("total_tasks_dropped", None, "total tasks dropped (across all scenarios)"),
    "reschedule_time": ("total_reschedule_time", "num_scenarios", "avg reschedule time per scenario (s)"),
    "schedule_gen_time": ("total_schedule_gen_time", "num_scenarios", "avg schedule gen time per scenario (s)"),
}


def _select_combo(df: pd.DataFrame, task_swap_metric: str, max_moves: int) -> pd.DataFrame:
    """Rows for one (approach, max_moves) combination, e.g. makespan with
    task-swap enabled. Matches on task_swap_metric since that's set the
    same way for both the old single-'metric' and new split-metric
    combination forms (see _normalize_combination in simulation.py)."""
    return df[(df["task_swap_metric"] == task_swap_metric) & (df["max_moves"] == max_moves)]


def build_grid(df: pd.DataFrame, metric: str, max_moves: int) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by workload_tier, columns pressure_tier,
    values = makespan_value - flexibility_value for the chosen metric
    (positive => flexibility did better, i.e. lower drops/time).
    """
    numer_col, denom_col, _ = METRICS[metric]

    grid_rows = df[df["profile_type"].isin(["grid", "grid_alias"])].copy()
    if grid_rows.empty:
        raise SystemExit(
            "No rows with profile_type in ['grid', 'grid_alias'] found. "
            "Did you run combine_summaries.py on the updated representative_set.py output?"
        )

    grid_rows["value"] = grid_rows[numer_col]
    if denom_col is not None:
        grid_rows["value"] = grid_rows["value"] / grid_rows[denom_col]

    makespan = _select_combo(grid_rows, "makespan", max_moves)
    flexibility = _select_combo(grid_rows, "flexibility", max_moves)

    for name, subset in [("makespan", makespan), ("flexibility", flexibility)]:
        dupes = subset.duplicated(subset=["workload_tier", "pressure_tier"])
        if dupes.any():
            dupe_cells = subset.loc[dupes, ["workload_tier", "pressure_tier"]].to_dict("records")
            print(f"Warning: multiple '{name}' rows for the same grid cell, using the first: {dupe_cells}")
        subset.drop_duplicates(subset=["workload_tier", "pressure_tier"], keep="first", inplace=True)

    merged = pd.merge(
        makespan[["workload_tier", "pressure_tier", "value", "num_scenarios"]].rename(
            columns={"value": "makespan_value", "num_scenarios": "makespan_num_scenarios"}
        ),
        flexibility[["workload_tier", "pressure_tier", "value", "num_scenarios"]].rename(
            columns={"value": "flexibility_value", "num_scenarios": "flexibility_num_scenarios"}
        ),
        on=["workload_tier", "pressure_tier"],
        how="outer",
    )
    merged["diff"] = merged["makespan_value"] - merged["flexibility_value"]

    if denom_col is None:
        mismatched = merged[merged["makespan_num_scenarios"] != merged["flexibility_num_scenarios"]]
        if not mismatched.empty:
            print(f"Warning: comparing raw totals ('{metric}') but num_scenarios differs for some cells "
                  f"(not a fair total-vs-total comparison there):")
            print(mismatched[["workload_tier", "pressure_tier", "makespan_num_scenarios", "flexibility_num_scenarios"]]
                  .to_string(index=False))

    grid = merged.pivot(index="workload_tier", columns="pressure_tier", values="diff")
    grid = grid.reindex(index=WORKLOAD_ORDER, columns=PRESSURE_ORDER)
    return grid


def plot_heatmap(grid: pd.DataFrame, ax, title: str):
    values = grid.values.astype(float)
    vmax = np.nanmax(np.abs(values)) if not np.all(np.isnan(values)) else 1.0
    vmax = vmax if vmax > 0 else 1.0

    im = ax.imshow(values, cmap="RdBu", vmin=-vmax, vmax=vmax, aspect="auto")

    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels(grid.columns)
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels(grid.index)
    ax.set_xlabel("pressure tier")
    ax.set_ylabel("workload tier")
    ax.set_title(title)

    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            v = values[i, j]
            label = "n/a" if np.isnan(v) else f"{v:.1f}"
            ax.text(j, i, label, ha="center", va="center", color="black", fontsize=10)

    return im


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=str, required=True, help="Path to combined_combination_summary.csv")
    ap.add_argument("--output", type=str, default=None,
                     help="Output image path (default: <input dir>/grid_heatmap_<metric>.png)")
    ap.add_argument("--metric", type=str, default="dropped", choices=list(METRICS.keys()),
                     help="Which metric to compare (default: dropped)")
    ap.add_argument("--max-moves", type=str, default="10",
                     help="max_moves value to compare ('10', '0', or 'both' for side-by-side subplots)")
    args = ap.parse_args()

    input_path = Path(args.input)
    df = pd.read_csv(input_path)

    numer_col, denom_col, metric_label = METRICS[args.metric]
    for col in [numer_col, denom_col, "task_swap_metric", "max_moves", "profile_type", "workload_tier", "pressure_tier"]:
        if col is not None and col not in df.columns:
            raise SystemExit(f"Expected column '{col}' not found in {input_path}. Is this a combine_summaries.py output?")

    if args.max_moves == "both":
        fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
        for i, (ax, max_moves) in enumerate(zip(axes, [10, 0])):
            grid = build_grid(df, args.metric, max_moves)
            swap_label = "with task swap" if max_moves else "no task swap"
            plot_heatmap(grid, ax, f"{swap_label} (max_moves={max_moves})")
            if i > 0:
                ax.set_ylabel("")
        fig.suptitle(f"Makespan vs. flexibility: {metric_label}\n(positive = flexibility better, negative = makespan better)")
        fig.subplots_adjust(wspace=0.35)
        fig.tight_layout(rect=(0, 0, 0.9, 1))
        fig.subplots_adjust(wspace=0.35)
        fig.colorbar(axes[0].images[0], ax=axes, shrink=0.8, label="makespan - flexibility")
    else:
        max_moves = int(args.max_moves)
        grid = build_grid(df, args.metric, max_moves)
        fig, ax = plt.subplots(figsize=(6.5, 5.5))
        swap_label = "with task swap" if max_moves else "no task swap"
        plot_heatmap(grid, ax, f"Makespan vs. flexibility: {metric_label}\n{swap_label} (max_moves={max_moves})")
        fig.tight_layout(rect=(0, 0, 0.88, 1))
        fig.colorbar(ax.images[0], ax=ax, shrink=0.8, label="makespan - flexibility")

    output_path = Path(args.output) if args.output else input_path.parent / f"grid_heatmap_{args.metric}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    print(f"Saved heatmap to {output_path}")


if __name__ == "__main__":
    main()