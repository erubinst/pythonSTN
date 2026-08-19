#!/usr/bin/env python3
"""
Compute pairwise percent differences between the 4 objectives (makespan,
makespan+swap, flexibility, flexibility+swap) at the FINAL level of each
sweep axis, from the combined combination summary CSV produced by
combine_summaries.py.

The line plots show the shape of each trend, but axes with very different
absolute drop counts (e.g. disruption vs. scale) are hard to compare by
eye. This produces one combined table of all 6 pairwise comparisons at
each axis's last (most extreme) level, in percent terms, so the relative
size of the makespan/flexibility gap is directly comparable across axes.

pct_diff for a pair (A, B) is (value_A - value_B) / value_B * 100 -- i.e.
"A is X% higher/lower than B". Pair order is fixed (see COMBOS below) so
the sign convention is consistent across every row.

Usage:
    python sweep_final_level_diff.py --input generated_scenarios/combined_combination_summary.csv
    python sweep_final_level_diff.py --input combined.csv --axis scale --axis disruption
    python sweep_final_level_diff.py --input combined.csv --metric reschedule_time
"""

import argparse
import itertools
from pathlib import Path

import pandas as pd

# metric name -> (numerator column, denominator column or None, display label)
# Matches plot_grid_heatmap.py / plot_sweep_lines.py: 'dropped' uses the raw
# total (per-scenario averages are near-meaningless with only a handful of
# drops per scenario), the timing metrics use a per-scenario average.
METRICS = {
    "dropped": ("total_tasks_dropped", None, "total tasks dropped (across all scenarios)"),
    "reschedule_time": ("total_reschedule_time", "num_scenarios", "avg reschedule time per scenario (s)"),
    "schedule_gen_time": ("total_schedule_gen_time", "num_scenarios", "avg schedule gen time per scenario (s)"),
}

# Canonical order for the 4 objectives -- fixes the sign convention for
# every pairwise comparison (see pct_diff formula above).
COMBOS = [
    ("makespan", 0, "makespan"),
    ("makespan", 10, "makespan_swap"),
    ("flexibility", 0, "flexibility"),
    ("flexibility", 10, "flexibility_swap"),
]


def get_final_level_values(df: pd.DataFrame, axis_name: str, metric: str) -> dict:
    """
    Returns dict(label -> (value, num_scenarios)) for the 4 objectives at
    the highest sweep_level_value for this axis (not the highest
    sweep_level_index -- some axes, like pressure, define their levels in
    decreasing order, so the last index doesn't necessarily mean the
    largest value), plus that level's index/value under keys
    '_level_index' / '_level_value'. Missing combos are simply absent from
    the dict (caller should check).
    """
    numer_col, denom_col, _ = METRICS[metric]

    sub = df[(df["profile_type"] == "sweep") & (df["sweep_axis"] == axis_name)].copy()
    sub = sub[sub["sweep_level_value"].notna()]
    if sub.empty:
        return {}

    last_level_value = sub["sweep_level_value"].max()
    sub = sub[sub["sweep_level_value"] == last_level_value]

    result = {"_level_value": last_level_value}
    level_indices = sub["sweep_level_index"].dropna().unique().tolist()
    result["_level_index"] = level_indices[0] if level_indices else None

    for task_swap_metric, max_moves, label in COMBOS:
        combo = sub[(sub["task_swap_metric"] == task_swap_metric) & (sub["max_moves"] == max_moves)]
        if combo.empty:
            continue
        if len(combo) > 1:
            print(f"Warning: multiple rows for '{label}' at axis '{axis_name}' final level "
                  f"(value {last_level_value}) -- using the first.")
            combo = combo.iloc[[0]]
        value = combo[numer_col].iloc[0]
        if denom_col is not None:
            value = value / combo[denom_col].iloc[0]
        result[label] = (value, combo["num_scenarios"].iloc[0])

    return result


def pct_diff(value_a: float, value_b: float):
    if value_b == 0:
        return None
    return (value_a - value_b) / value_b * 100


def build_table(df: pd.DataFrame, axes_to_use: list, metric: str) -> pd.DataFrame:
    numer_col, denom_col, metric_label = METRICS[metric]
    rows = []

    for axis_name in axes_to_use:
        final = get_final_level_values(df, axis_name, metric)
        if not final or "_level_value" not in final:
            print(f"Warning: no sweep data found for axis '{axis_name}' -- skipping.")
            continue

        labels = [c[2] for c in COMBOS]
        present = [l for l in labels if l in final]
        missing = [l for l in labels if l not in final]
        if missing:
            print(f"Warning: axis '{axis_name}' final level (value {final['_level_value']}) is "
                  f"missing objective(s) {missing} -- pairs involving them will be skipped.")

        scenario_counts = {final[l][1] for l in present}
        if len(scenario_counts) > 1:
            print(f"Warning: num_scenarios differs across objectives at axis '{axis_name}' final level "
                  f"(value {final['_level_value']}) ({sorted(scenario_counts)}) -- percent differences "
                  f"there aren't a perfectly fair comparison.")

        for label_a, label_b in itertools.combinations(labels, 2):
            if label_a not in final or label_b not in final:
                continue
            value_a, _ = final[label_a]
            value_b, _ = final[label_b]
            diff = pct_diff(value_a, value_b)
            rows.append(dict(
                axis=axis_name,
                level_index=int(final["_level_index"]) if final["_level_index"] is not None else None,
                level_value=final["_level_value"],
                metric=metric,
                metric_label=metric_label,
                objective_a=label_a,
                objective_b=label_b,
                value_a=value_a,
                value_b=value_b,
                pct_diff=diff,
            ))

    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=str, required=True, help="Path to combined_combination_summary.csv")
    ap.add_argument("--output", type=str, default=None,
                     help="Output CSV path (default: <input dir>/sweep_final_level_pct_diff.csv)")
    ap.add_argument("--axis", type=str, default=None, action="append",
                     help="Sweep axis to include (e.g. scale, disruption, workload, pressure). "
                          "Repeatable. Omit to include every sweep axis present in the data.")
    ap.add_argument("--metric", type=str, default="dropped", choices=list(METRICS.keys()),
                     help="Which metric to compare (default: dropped)")
    args = ap.parse_args()

    input_path = Path(args.input)
    df = pd.read_csv(input_path)

    required_cols = ["profile_type", "sweep_axis", "sweep_level_index", "sweep_level_value",
                      "task_swap_metric", "max_moves", "num_scenarios"]
    for col in required_cols:
        if col not in df.columns:
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
        axes_to_use = args.axis
    else:
        axes_to_use = available_axes

    table = build_table(df, axes_to_use, args.metric)
    if table.empty:
        raise SystemExit("No comparisons could be computed -- check the warnings above.")

    output_path = Path(args.output) if args.output else input_path.parent / "sweep_final_level_pct_diff.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(output_path, index=False)

    print(f"\n{table.to_string(index=False)}")
    print(f"\nSaved {len(table)} row(s) to {output_path}")


if __name__ == "__main__":
    main()