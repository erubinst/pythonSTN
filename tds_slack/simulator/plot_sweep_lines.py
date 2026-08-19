#!/usr/bin/env python3
"""
Plot line charts of the 4 objectives (makespan/flexibility x with/without
task swap) across sweep levels, from the combined combination summary CSV
produced by combine_summaries.py.

One image is saved per requested sweep axis (scale, disruption, workload,
pressure), with sweep_level_value on the x-axis and the chosen metric
(total tasks dropped by default) on the y-axis, one line per objective.

Usage:
    python plot_sweep_lines.py --input generated_scenarios/combined_combination_summary.csv
    python plot_sweep_lines.py --input combined.csv --axis scale --axis disruption
    python plot_sweep_lines.py --input combined.csv --metric reschedule_time
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# metric name -> (numerator column, denominator column or None, display label)
# 'dropped' uses the raw total rather than a per-scenario average -- with
# only a handful of drops per scenario across 100 scenarios, per-scenario
# averages round down to near-meaningless fractions (same reasoning as the
# grid heatmap).
METRICS = {
    "dropped": ("total_tasks_dropped", None, "total tasks dropped (across all scenarios)"),
    "reschedule_time": ("total_reschedule_time", "num_scenarios", "avg reschedule time per scenario (s)"),
    "schedule_gen_time": ("total_schedule_gen_time", "num_scenarios", "avg schedule gen time per scenario (s)"),
}

# The 4 objectives, in a fixed draw order/style so every plot is consistent.
# Makespan = red family, flexibility = blue family; task-swap = dark/solid,
# no-swap = light/dashed, so the two variants of the same objective read as
# shades of one color rather than unrelated colors.
COMBOS = [
    ("makespan", 10, "Makespan (task swap)", "#b30000", "o", "-"),
    ("makespan", 0, "Makespan (no swap)", "#e68365", "s", "--"),
    ("flexibility", 10, "Flexibility (task swap)", "#0047ab", "o", "-"),
    ("flexibility", 0, "Flexibility (no swap)", "#7eb0df", "s", "--"),
]

# Friendlier x-axis labels than the raw axis name; falls back to the axis
# name itself if not listed here (e.g. a new axis added later).
AXIS_X_LABELS = {
    "scale": "n_resources",
    "disruption": "future_downtime_count",
    "workload": "ratio (tasks per resource)",
    "pressure": "due_date_slack multiplier (k)",
}


def load_sweep_axis_data(df: pd.DataFrame, axis_name: str, metric: str) -> pd.DataFrame:
    numer_col, denom_col, _ = METRICS[metric]

    sub = df[(df["profile_type"] == "sweep") & (df["sweep_axis"] == axis_name)].copy()

    missing_value = sub["sweep_level_value"].isna()
    if missing_value.any():
        names = sub.loc[missing_value, "profile"].unique().tolist()
        print(f"Warning: {len(names)} '{axis_name}' sweep profile(s) have no sweep_level_value "
              f"(missing/unreadable manifest.json) and will be skipped: {names}")
        sub = sub[~missing_value]

    if sub.empty:
        return sub

    sub["value"] = sub[numer_col]
    if denom_col is not None:
        sub["value"] = sub["value"] / sub[denom_col]

    return sub


def plot_axis(sub: pd.DataFrame, axis_name: str, metric_label: str, ax):
    scenario_counts = set()

    for task_swap_metric, max_moves, label, color, marker, linestyle in COMBOS:
        combo = sub[(sub["task_swap_metric"] == task_swap_metric) & (sub["max_moves"] == max_moves)]
        combo = combo.sort_values("sweep_level_value")

        dupes = combo.duplicated(subset=["sweep_level_value"])
        if dupes.any():
            dupe_levels = combo.loc[dupes, "sweep_level_value"].tolist()
            print(f"Warning: multiple '{label}' rows at the same sweep_level_value for axis "
                  f"'{axis_name}', using the first: {dupe_levels}")
            combo = combo.drop_duplicates(subset=["sweep_level_value"], keep="first")

        if combo.empty:
            print(f"Warning: no data for '{label}' on axis '{axis_name}' -- skipping this line.")
            continue

        scenario_counts.update(combo["num_scenarios"].unique().tolist())

        ax.plot(
            combo["sweep_level_value"], combo["value"],
            label=label, color=color, marker=marker, linestyle=linestyle,
        )

    if len(scenario_counts) > 1:
        print(f"Warning: num_scenarios varies across levels/combos for axis '{axis_name}' "
              f"({sorted(scenario_counts)}) -- raw totals aren't a perfectly fair comparison there.")

    ax.set_xlabel(AXIS_X_LABELS.get(axis_name, axis_name))
    ax.set_ylabel(metric_label)
    ax.set_title(f"{axis_name} sweep")
    ax.legend()
    ax.grid(True, alpha=0.3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=str, required=True, help="Path to combined_combination_summary.csv")
    ap.add_argument("--output-dir", type=str, default=None,
                     help="Directory for output images (default: same directory as --input)")
    ap.add_argument("--axis", type=str, default=None, action="append",
                     help="Sweep axis to plot (e.g. scale, disruption, workload, pressure). "
                          "Repeatable. Omit to plot every sweep axis present in the data.")
    ap.add_argument("--metric", type=str, default="dropped", choices=list(METRICS.keys()),
                     help="Which metric to plot on the y-axis (default: dropped)")
    args = ap.parse_args()

    input_path = Path(args.input)
    df = pd.read_csv(input_path)

    numer_col, denom_col, metric_label = METRICS[args.metric]
    required_cols = [numer_col, "task_swap_metric", "max_moves", "profile_type",
                      "sweep_axis", "sweep_level_value", "num_scenarios"]
    for col in required_cols:
        if col is not None and col not in df.columns:
            raise SystemExit(f"Expected column '{col}' not found in {input_path}. Is this a combine_summaries.py output?")

    sweep_rows = df[df["profile_type"] == "sweep"]
    if sweep_rows.empty:
        raise SystemExit(
            "No rows with profile_type == 'sweep' found. "
            "Did you generate sweep_* profiles and run combine_summaries.py on them?"
        )

    available_axes = sorted(sweep_rows["sweep_axis"].dropna().unique().tolist())
    if args.axis:
        unknown = [a for a in args.axis if a not in available_axes]
        if unknown:
            raise SystemExit(f"Unknown sweep axis/axes {unknown}. Available: {available_axes}")
        axes_to_plot = args.axis
    else:
        axes_to_plot = available_axes

    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    for axis_name in axes_to_plot:
        sub = load_sweep_axis_data(df, axis_name, args.metric)
        if sub.empty:
            print(f"No usable data for axis '{axis_name}' -- skipping.")
            continue

        fig, ax = plt.subplots(figsize=(7.5, 5.5))
        plot_axis(sub, axis_name, metric_label, ax)
        fig.tight_layout()

        output_path = output_dir / f"sweep_line_{axis_name}_{args.metric}.png"
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        print(f"Saved {output_path}")


if __name__ == "__main__":
    main()