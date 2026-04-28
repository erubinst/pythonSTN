#!/usr/bin/env python3
"""Plot dropped-task preservation trends from ratio-sweep results.

Reads the summary CSV produced by single_task_downtime_reschedule_sweep.py
and writes interactive plots for pair-level ratio trends.
"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


OBJECTIVE_COLORS = {
    "flexibility": "#8e44ad",
    "makespan": "#1abc9c",
    "earliest_completion_time": "#e74c3c",
    "slack": "#7f8c8d",
}


def parse_args():
    p = argparse.ArgumentParser(
        description="Plot ratio trend for dropped tasks: flexibility vs makespan vs earliest completion time",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--summary-csv",
        type=Path,
        required=True,
        help="Path to single_task_downtime_reschedule_summary.csv",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("scenario_sweeps/plots"),
        help="Directory for output plot files",
    )
    p.add_argument(
        "--no-slack",
        action="store_true",
        help="Exclude Slack bars even if slack data exists in the summary CSV",
    )
    return p.parse_args()


def make_double_bar_plot(pair_df, include_slack=True):
    """Create a grouped bar chart: ratio on x-axis, total dropped tasks on y-axis, bars for each objective."""
    pivot = (
        pair_df.pivot_table(
            index=["pair", "ratio"],
            columns="objective",
            values="total_dropped_tasks",
            aggfunc="first",
        )
        .reset_index()
        .sort_values("ratio")
    )
    if "flexibility" not in pivot.columns:
        pivot["flexibility"] = 0
    if "makespan" not in pivot.columns:
        pivot["makespan"] = 0
    if "earliest_completion_time" not in pivot.columns:
        pivot["earliest_completion_time"] = 0

    has_slack = include_slack and ("slack" in pivot.columns)

    fig = go.Figure()

    # Add makespan bars first so it gets the first default trace color.
    fig.add_trace(
        go.Bar(
            x=pivot["ratio"],
            y=pivot["makespan"],
            name="Min Makespan",
            marker={"color": OBJECTIVE_COLORS["makespan"]},
            hovertemplate=(
                "ratio=%{x:.3f}<br>"
                "pair=%{customdata}<br>"
                "total dropped=%{y}<extra></extra>"
            ),
            customdata=pivot["pair"],
        )
    )

    # Add flexibility bars second so it gets the second default trace color.
    fig.add_trace(
        go.Bar(
            x=pivot["ratio"],
            y=pivot["flexibility"],
            name="Max Flexibility",
            marker={"color": OBJECTIVE_COLORS["flexibility"]},
            hovertemplate=(
                "ratio=%{x:.3f}<br>"
                "pair=%{customdata}<br>"
                "total dropped=%{y}<extra></extra>"
            ),
            customdata=pivot["pair"],
        )
    )

    # Add earliest completion time bars third.
    fig.add_trace(
        go.Bar(
            x=pivot["ratio"],
            y=pivot["earliest_completion_time"],
            name="Max Task Completion Margin",
            marker={"color": OBJECTIVE_COLORS["earliest_completion_time"]},
            hovertemplate=(
                "ratio=%{x:.3f}<br>"
                "pair=%{customdata}<br>"
                "total dropped=%{y}<extra></extra>"
            ),
            customdata=pivot["pair"],
        )
    )

    if has_slack:
        # Add slack bars only when requested and present in data.
        fig.add_trace(
            go.Bar(
                x=pivot["ratio"],
                y=pivot["slack"],
                name="Max Slack",
                marker={"color": OBJECTIVE_COLORS["slack"]},
                hovertemplate=(
                    "ratio=%{x:.3f}<br>"
                    "pair=%{customdata}<br>"
                    "total dropped=%{y}<extra></extra>"
                ),
                customdata=pivot["pair"],
            )
        )

    fig.update_layout(
        title="Total Dropped Tasks by Objective and Ratio",
        xaxis_title="Number of Tasks/Number of Agents",
        yaxis_title="Total Dropped Tasks",
        barmode="group",
        template="plotly_white",
        hovermode="x unified",
        font={"size": 16},
        legend={
            "orientation": "v",
            "x": 0.01,
            "y": 0.99,
            "xanchor": "left",
            "yanchor": "top",
            "bgcolor": "rgba(255,255,255,0.75)",
        },
    )
    return fig


def main():
    args = parse_args()

    if not args.summary_csv.exists():
        raise FileNotFoundError(f"Summary CSV not found: {args.summary_csv}")

    df = pd.read_csv(args.summary_csv)
    pair_df = df[df["summary_scope"] == "pair"].copy()
    if pair_df.empty:
        raise ValueError(
            "No pair-level rows found. Run the sweep with --mode ratio_sweep first."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    fig = make_double_bar_plot(pair_df, include_slack=not args.no_slack)
    plot_path = args.output_dir / f"total_dropped_double_bar_{ts}.html"
    fig.write_html(plot_path)

    print(f"Generated plot: {plot_path}")


if __name__ == "__main__":
    main()
