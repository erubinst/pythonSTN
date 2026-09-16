#!/usr/bin/env python3
"""
Plot line charts of one or more objectives (metric x with/without task
swap) across sweep levels, from the combined combination summary CSV
produced by combine_summaries.py.

One image is saved per requested sweep axis (scale, disruption, workload,
pressure, overlap), with sweep_level_value on the x-axis and the chosen
y-axis metric (total tasks dropped by default) on the y-axis, one line per
objective x max_moves combination.

Usage:
    python plot_sweep_lines.py --input generated_scenarios/combined_combination_summary.csv
    python plot_sweep_lines.py --input combined.csv --axis scale --axis disruption
    python plot_sweep_lines.py --input combined.csv --y-metric reschedule_time
    python plot_sweep_lines.py --input combined_full_flex_summary.csv \\
        --objectives full_flex makespan --sum-initial-and-dropped
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# y-axis metric name -> (numerator column, denominator column or None, display label)
# 'dropped' uses the raw total rather than a per-scenario average -- with
# only a handful of drops per scenario across 100 scenarios, per-scenario
# averages round down to near-meaningless fractions (same reasoning as the
# grid heatmap).
Y_METRICS = {
    "dropped": ("total_tasks_dropped", None, "total tasks dropped (across all scenarios)"),
    "reschedule_time": ("total_reschedule_time", "num_scenarios", "avg reschedule time per scenario (s)"),
    "schedule_gen_time": ("total_schedule_gen_time", "num_scenarios", "avg schedule gen time per scenario (s)"),
    # Runtime-dropped as a fraction of what each objective actually started
    # with (total_tasks - total_initial_unscheduled), not of total_tasks --
    # an objective that leaves fewer tasks unscheduled initially exposes
    # more tasks to disruption, so raw dropped counts alone aren't a fair
    # comparison of rescheduling behavior specifically. Numerator/denominator
    # aren't plain columns here, so this is handled specially in
    # load_sweep_axis_data rather than via the (numer_col, denom_col) tuple.
    "dropped_ratio": (None, None, "runtime dropped / starting tasks"),
}

# Default objectives to plot when --objectives isn't given, kept for
# backward compatibility with earlier invocations of this script.
DEFAULT_OBJECTIVES = ["makespan", "flexibility"]

# One base color per known objective; task-swap = solid, no-swap = dashed,
# so the two variants of one objective read as shades of one color rather
# than unrelated colors. Unknown objectives (e.g. a new metric added later)
# fall back to a neutral gray rather than erroring.
OBJECTIVE_COLORS = {
    "makespan": "#b30000",
    "flexibility": "#0047ab",
    "save_flexibility": "#7030a0",
    "full_flex": "#0a7d3c",
}
FALLBACK_COLOR = "#555555"


def build_combos(objectives):
    """
    One (task_swap_metric, max_moves, label, color, marker, linestyle) tuple
    per (objective, max_moves) pair, in the given objective order.
    """
    combos = []
    for objective in objectives:
        color = OBJECTIVE_COLORS.get(objective, FALLBACK_COLOR)
        display = objective.replace("_", " ")
        combos.append((objective, 10, f"{display} (task swap)", color, "o", "-"))
        combos.append((objective, 0, f"{display} (no swap)", color, "s", "--"))
    return combos


# Friendlier x-axis labels than the raw axis name; falls back to the axis
# name itself if not listed here (e.g. a new axis added later).
AXIS_X_LABELS = {
    "scale": "n_resources",
    "disruption": "future_downtime_count",
    "workload": "ratio (tasks per resource)",
    "pressure": "due_date_slack multiplier (k)",
    "overlap": "capability_overlap",
}


def load_sweep_axis_data(df: pd.DataFrame, axis_name: str, y_metric: str, sum_initial_and_dropped: bool = False, drop_max_level: bool = False) -> pd.DataFrame:
    numer_col, denom_col, _ = Y_METRICS[y_metric]

    sub = df[(df["profile_type"] == "sweep") & (df["sweep_axis"] == axis_name)].copy()

    missing_value = sub["sweep_level_value"].isna()
    if missing_value.any():
        names = sub.loc[missing_value, "profile"].unique().tolist()
        print(f"Warning: {len(names)} '{axis_name}' sweep profile(s) have no sweep_level_value "
              f"(missing/unreadable manifest.json) and will be skipped: {names}")
        sub = sub[~missing_value]

    if sub.empty:
        return sub

    if drop_max_level:
        max_level = sub["sweep_level_value"].max()
        print(f"Dropping highest sweep level for axis '{axis_name}' (sweep_level_value={max_level}).")
        sub = sub[sub["sweep_level_value"] != max_level]
        if sub.empty:
            return sub

    if y_metric == "dropped_ratio":
        required = ["total_tasks", "total_initial_unscheduled", "total_tasks_dropped"]
        missing_cols = [c for c in required if c not in sub.columns]
        if missing_cols:
            raise SystemExit(f"--y-metric dropped_ratio requires columns {required} in the input CSV "
                              f"(missing: {missing_cols}). Regenerate with the current combine_summaries.py.")
        missing_total_tasks = sub["total_tasks"].isna()
        if missing_total_tasks.any():
            names = sub.loc[missing_total_tasks, "profile"].unique().tolist()
            print(f"Warning: {len(names)} '{axis_name}' sweep profile(s) have no total_tasks "
                  f"(missing/unreadable manifest.json) and will be skipped: {names}")
            sub = sub[~missing_total_tasks]
        starting_tasks = sub["total_tasks"] - sub["total_initial_unscheduled"]
        sub["value"] = sub["total_tasks_dropped"] / starting_tasks
        return sub

    sub["value"] = sub[numer_col]
    if sum_initial_and_dropped:
        if "total_initial_unscheduled" not in sub.columns:
            raise SystemExit("--sum-initial-and-dropped requires a 'total_initial_unscheduled' column in the input CSV.")
        sub["value"] = sub["value"] + sub["total_initial_unscheduled"]
    if denom_col is not None:
        sub["value"] = sub["value"] / sub[denom_col]

    return sub


def plot_axis(sub: pd.DataFrame, axis_name: str, metric_label: str, ax, combos):
    scenario_counts = set()

    for task_swap_metric, max_moves, label, color, marker, linestyle in combos:
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

    ax.set_ylim(bottom=0)
    ax.set_xlabel(AXIS_X_LABELS.get(axis_name, axis_name))
    ax.set_ylabel(metric_label)
    ax.set_title(f"{axis_name} sweep")
    ax.legend()
    ax.grid(True, alpha=0.3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=str, required=True, help="Path to a combine_summaries.py output CSV")
    ap.add_argument("--output-dir", type=str, default=None,
                     help="Directory for output images (default: same directory as --input)")
    ap.add_argument("--axis", type=str, default=None, action="append",
                     help="Sweep axis to plot (e.g. scale, disruption, workload, pressure, overlap). "
                          "Repeatable. Omit to plot every sweep axis present in the data.")
    ap.add_argument("--objectives", nargs="+", default=DEFAULT_OBJECTIVES,
                     help=f"Objectives (initial_metric/task_swap_metric values) to plot, in order "
                          f"(default: {DEFAULT_OBJECTIVES})")
    ap.add_argument("--y-metric", type=str, default="dropped", choices=list(Y_METRICS.keys()),
                     help="Which metric to plot on the y-axis (default: dropped)")
    ap.add_argument("--sum-initial-and-dropped", action="store_true",
                     help="When --y-metric=dropped, add total_initial_unscheduled to total_tasks_dropped")
    ap.add_argument("--drop-max-level", action="store_true",
                     help="Drop the highest sweep_level_value for each plotted axis (e.g. to exclude an "
                          "extreme observation-only data point without regenerating the underlying data)")
    args = ap.parse_args()

    input_path = Path(args.input)
    df = pd.read_csv(input_path)

    numer_col, denom_col, metric_label = Y_METRICS[args.y_metric]
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

    combos = build_combos(args.objectives)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.sum_initial_and_dropped and args.y_metric != "dropped":
        raise SystemExit("--sum-initial-and-dropped only makes sense with --y-metric dropped")
    if args.sum_initial_and_dropped:
        metric_label = "total dropped tasks (initial + runtime)"

    for axis_name in axes_to_plot:
        sub = load_sweep_axis_data(df, axis_name, args.y_metric, sum_initial_and_dropped=args.sum_initial_and_dropped, drop_max_level=args.drop_max_level)
        if sub.empty:
            print(f"No usable data for axis '{axis_name}' -- skipping.")
            continue

        fig, ax = plt.subplots(figsize=(7.5, 5.5))
        plot_axis(sub, axis_name, metric_label, ax, combos)
        fig.tight_layout()

        suffix = f"{args.y_metric}_summed" if args.sum_initial_and_dropped else args.y_metric
        output_path = output_dir / f"sweep_line_{axis_name}_{suffix}.png"
        fig.savefig(output_path, dpi=150)
        plt.close(fig)
        print(f"Saved {output_path}")


if __name__ == "__main__":
    main()