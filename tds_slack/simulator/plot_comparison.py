"""
General-purpose plot for a combination-summary CSV produced by
run_profile_across_combinations / run_comparison.py: initial-unscheduled and
runtime-dropped task totals side by side per metric/max_moves combination.
Replaces the one-off plot_full_flex_comparison.py (hardcoded to one CSV).

    python -m tds_slack.simulator.plot_comparison \\
        tds_slack/simulator/generated_scenarios/baseline/baseline_300_combination_summary.csv \\
        --highlight full_flex \\
        --out full_flex_comparison.png
"""
import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def total_task_count(manifest_path):
    if not manifest_path.exists():
        return None
    with open(manifest_path) as f:
        manifest = json.load(f)
    return sum(e["n_tasks"] for e in manifest)


def plot(summary_df, metrics, highlight, title, total_tasks, out_path):
    task_swap = summary_df[summary_df.reschedule_mode == "task_swap"]

    labels, dropped, unscheduled, is_highlight = [], [], [], []
    for metric in metrics:
        rows = task_swap[task_swap.initial_metric == metric].sort_values("max_moves", ascending=False)
        for _, row in rows.iterrows():
            labels.append(f"{metric}\nmax_moves={int(row.max_moves)}")
            dropped.append(row.total_tasks_dropped)
            unscheduled.append(row.total_initial_unscheduled)
            is_highlight.append(metric == highlight)

    x = np.arange(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(1.5 * len(labels) + 2, 5))
    colors = ["#4C72B0" if h else "#888888" for h in is_highlight]
    bars1 = ax.bar(x - width / 2, unscheduled, width, label="Initial unscheduled", color=colors, alpha=0.55)
    bars2 = ax.bar(x + width / 2, dropped, width, label="Runtime dropped", color=colors)

    for bars in (bars1, bars2):
        for b in bars:
            text = f"{int(b.get_height())}"
            if total_tasks:
                text += f"\n({100 * b.get_height() / total_tasks:.1f}%)"
            ax.annotate(text, (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", va="bottom", fontsize=8)

    ax.set_ylabel(f"Tasks (sum over {int(summary_df.num_scenarios.iloc[0])} scenarios)")
    subtitle = f"\n(out of {total_tasks:,} total tasks)" if total_tasks else ""
    ax.set_title(title + subtitle)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.margins(y=0.12)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"Saved {out_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("summary_csv", help="Path to a *_combination_summary.csv / *_summary.csv file")
    ap.add_argument("--metrics", nargs="+", default=None, help="Metrics to include, in order (default: every initial_metric found in the CSV, in first-seen order)")
    ap.add_argument("--highlight", default=None, help="Metric name to color blue; others gray")
    ap.add_argument("--title", default=None, help="Chart title (default derived from the CSV filename)")
    ap.add_argument("--out", default=None, help="Output PNG path (default: <summary_csv stem>.png next to the CSV)")
    args = ap.parse_args()

    summary_path = Path(args.summary_csv)
    summary_df = pd.read_csv(summary_path)

    metrics = args.metrics or list(dict.fromkeys(summary_df[summary_df.reschedule_mode == "task_swap"].initial_metric))
    title = args.title or f"{summary_path.stem}: initial-unscheduled and runtime-dropped tasks by objective"
    out_path = Path(args.out) if args.out else summary_path.with_suffix(".png")

    total_tasks = total_task_count(summary_path.parent / "manifest.json")

    plot(summary_df, metrics, args.highlight, title, total_tasks, out_path)


if __name__ == "__main__":
    main()
