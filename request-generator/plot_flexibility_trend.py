#!/usr/bin/env python3
"""Plot grouped average schedule-quality values from sweep results.

This script reads a detailed flexibility sweep CSV and writes interactive plots:
1) Aggregated mean flexibility by task/resource combination with 95% CI for
    flexibility, earliest completion time, makespan, and optional slack schedules.
2) Aggregated mean earliest completion time by task/resource combination with 95%
    CI for all available schedules/objectives.
"""

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go


OBJECTIVE_COLORS = {
    "flexibility": "#8e44ad",
    "makespan": "#1abc9c",
    "earliest_completion_time": "#e74c3c",
    "slack": "#7f8c8d",
}

OBJECTIVE_MARKERS = {
    "flexibility": "circle",
    "earliest_completion_time": "square",
    "makespan": "diamond",
    "slack": "triangle-up",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot flexibility objective percent-difference trends."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help="Path to flexibility_sweep_detailed_*.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("flexibility_sweep/plots"),
        help="Directory for generated plot files (default: flexibility_sweep/plots)",
    )
    return parser.parse_args()


def resolve_baseline_columns(df: pd.DataFrame) -> tuple[str, str, str]:
    """Return baseline flexibility column, pct-diff column, and display name."""
    ect_cols = {
        "earliest_completion_time_objective_flexibility_value",
        "flexibility_vs_earliest_completion_time_flexibility_pct_diff",
    }
    missing_ect = sorted(ect_cols - set(df.columns))
    if missing_ect:
        raise ValueError(
            "Missing required earliest completion time columns: "
            f"{missing_ect}"
        )

    return (
        "earliest_completion_time_objective_flexibility_value",
        "flexibility_vs_earliest_completion_time_flexibility_pct_diff",
        "Earliest Completion Time",
    )


def validate_columns(df: pd.DataFrame) -> None:
    common_required = {
        "n_tasks",
        "n_resources",
        "flexibility_objective_value",
    }
    missing_common = common_required - set(df.columns)
    if missing_common:
        raise ValueError(f"Missing required columns: {sorted(missing_common)}")

    ect_required = {
        "earliest_completion_time_objective_flexibility_value",
    }
    missing_ect = sorted(ect_required - set(df.columns))
    if missing_ect:
        raise ValueError(
            "Missing required earliest completion time columns for average flexibility comparison: "
            f"{missing_ect}"
        )

    makespan_required = {"makespan_objective_flexibility_value"}
    missing_makespan = sorted(makespan_required - set(df.columns))
    if missing_makespan:
        raise ValueError(
            "Missing required makespan columns for average flexibility comparison: "
            f"{missing_makespan}"
        )

    completion_required = {
        "flexibility_earliest_completion_time",
        "earliest_completion_time_schedule_value",
    }
    missing_completion = sorted(completion_required - set(df.columns))
    if missing_completion:
        raise ValueError(
            "Missing required earliest completion time columns for task completion margin comparison: "
            f"{missing_completion}"
        )


def build_scatter_with_trend(
    df: pd.DataFrame,
    pct_diff_col: str,
    comparison_name: str,
) -> go.Figure:
    x = df["tasks_per_resource"].to_numpy(dtype=float)
    y = df[pct_diff_col].to_numpy(dtype=float)

    slope, intercept = np.polyfit(x, y, deg=1)
    x_line = np.linspace(x.min(), x.max(), 100)
    y_line = slope * x_line + intercept

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="markers",
            marker={"size": 6, "opacity": 0.35},
            name="Scenarios",
            hovertemplate=(
                "tasks/resources=%{x:.2f}<br>"
                "pct diff=%{y:.2f}%<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_line,
            y=y_line,
            mode="lines",
            line={"width": 3},
            name=f"Linear trend (slope={slope:.2f})",
            hovertemplate="trend=%{y:.2f}%<extra></extra>",
        )
    )

    fig.update_layout(
        title=f"Flexibility % Difference vs Task/Resource Ratio ({comparison_name})",
        xaxis_title="Number of Tasks/Number of Agents",
        yaxis_title=f"Flexibility % Difference vs {comparison_name} Schedule",
        template="plotly_white",
    )
    return fig


def build_grouped_ci_plot(
    df: pd.DataFrame,
    pct_diff_col: str,
    comparison_name: str,
) -> go.Figure:
    grouped = (
        df.groupby(["n_tasks", "n_resources"], as_index=False)
        .agg(
            mean=(pct_diff_col, "mean"),
            std=(pct_diff_col, "std"),
            count=(pct_diff_col, "count"),
        )
        .reset_index(drop=True)
    )
    grouped["tasks_per_resource"] = grouped["n_tasks"] / grouped["n_resources"]
    grouped["sem"] = grouped["std"] / np.sqrt(grouped["count"])
    grouped["ci95"] = 1.96 * grouped["sem"]
    grouped["label"] = grouped["n_tasks"].astype(str) + "/" + grouped["n_resources"].astype(str)
    grouped = grouped.sort_values(["tasks_per_resource", "n_tasks", "n_resources"]).reset_index(drop=True)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean"],
            mode="markers+lines+text",
            text=grouped["label"],
            textposition="top center",
            error_y={"type": "data", "array": grouped["ci95"], "visible": True},
            marker={"size": 9},
            line={"width": 2},
            name="Mean +/- 95% CI",
            hovertemplate=(
                "pair=%{text}<br>"
                "ratio=%{x:.2f}<br>"
                "mean=%{y:.2f}%<br>"
                "95% CI +/- %{error_y.array:.2f}<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=f"Mean Flexibility % Difference by Task/Agent Combination ({comparison_name})",
        xaxis_title="Number of Tasks/Number of Agents",
        yaxis_title=f"Mean Flexibility % Difference vs {comparison_name} Schedule",
        template="plotly_white",
    )
    return fig


def build_grouped_avg_flexibility_comparison_plot(
    df: pd.DataFrame,
    include_slack: bool,
) -> go.Figure:
    has_slack = include_slack

    grouped = (
        df.groupby(["n_tasks", "n_resources"], as_index=False)
        .agg(
            mean_flex=("flexibility_objective_value", "mean"),
            std_flex=("flexibility_objective_value", "std"),
            mean_ect=("earliest_completion_time_objective_flexibility_value", "mean"),
            std_ect=("earliest_completion_time_objective_flexibility_value", "std"),
            mean_makespan=("makespan_objective_flexibility_value", "mean"),
            std_makespan=("makespan_objective_flexibility_value", "std"),
            mean_slack=("slack_objective_flexibility_value", "mean") if has_slack else ("flexibility_objective_value", "mean"),
            std_slack=("slack_objective_flexibility_value", "std") if has_slack else ("flexibility_objective_value", "std"),
            count=("flexibility_objective_value", "count"),
        )
        .reset_index(drop=True)
    )

    grouped["tasks_per_resource"] = grouped["n_tasks"] / grouped["n_resources"]
    grouped["label"] = grouped["n_tasks"].astype(str) + "/" + grouped["n_resources"].astype(str)

    grouped["sem_flex"] = grouped["std_flex"] / np.sqrt(grouped["count"])
    grouped["sem_ect"] = grouped["std_ect"] / np.sqrt(grouped["count"])
    grouped["sem_makespan"] = grouped["std_makespan"] / np.sqrt(grouped["count"])
    grouped["sem_slack"] = grouped["std_slack"] / np.sqrt(grouped["count"])
    grouped["ci95_flex"] = 1.96 * grouped["sem_flex"]
    grouped["ci95_ect"] = 1.96 * grouped["sem_ect"]
    grouped["ci95_makespan"] = 1.96 * grouped["sem_makespan"]
    grouped["ci95_slack"] = 1.96 * grouped["sem_slack"]
    grouped = grouped.sort_values(["tasks_per_resource", "n_tasks", "n_resources"]).reset_index(drop=True)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_flex"],
            mode="markers+lines+text",
            text=grouped["label"],
            textposition="top center",
            textfont={"size": 14},
            error_y={"type": "data", "array": grouped["ci95_flex"], "visible": True},
            marker={
                "size": 9,
                "color": OBJECTIVE_COLORS["flexibility"],
                "symbol": OBJECTIVE_MARKERS["flexibility"],
            },
            line={"width": 2, "color": OBJECTIVE_COLORS["flexibility"]},
            name="Max Flexibility ",
            hovertemplate=(
                "pair=%{text}<br>"
                "ratio=%{x:.2f}<br>"
                "mean flexibility=%{y:.2f}<br>"
                "95% CI +/- %{error_y.array:.2f}<extra></extra>"
            ),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_ect"],
            mode="markers+lines",
            error_y={"type": "data", "array": grouped["ci95_ect"], "visible": True},
            marker={
                "size": 9,
                "color": OBJECTIVE_COLORS["earliest_completion_time"],
                "symbol": OBJECTIVE_MARKERS["earliest_completion_time"],
            },
            line={"width": 2, "color": OBJECTIVE_COLORS["earliest_completion_time"]},
            name="Max Task Completion Margin",
            hovertemplate=(
                "pair=%{customdata}<br>"
                "ratio=%{x:.2f}<br>"
                "mean flexibility=%{y:.2f}<br>"
                "95% CI +/- %{error_y.array:.2f}<extra></extra>"
            ),
            customdata=grouped["label"],
        )
    )

    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_makespan"],
            mode="markers+lines",
            error_y={"type": "data", "array": grouped["ci95_makespan"], "visible": True},
            marker={
                "size": 9,
                "color": OBJECTIVE_COLORS["makespan"],
                "symbol": OBJECTIVE_MARKERS["makespan"],
            },
            line={"width": 2, "color": OBJECTIVE_COLORS["makespan"]},
            name="Min Makespan",
            hovertemplate=(
                "pair=%{customdata}<br>"
                "ratio=%{x:.2f}<br>"
                "mean flexibility=%{y:.2f}<br>"
                "95% CI +/- %{error_y.array:.2f}<extra></extra>"
            ),
            customdata=grouped["label"],
        )
    )

    if has_slack:
        fig.add_trace(
            go.Scatter(
                x=grouped["tasks_per_resource"],
                y=grouped["mean_slack"],
                mode="markers+lines",
                error_y={"type": "data", "array": grouped["ci95_slack"], "visible": True},
                marker={
                    "size": 9,
                    "color": OBJECTIVE_COLORS["slack"],
                    "symbol": OBJECTIVE_MARKERS["slack"],
                },
                line={"width": 2, "color": OBJECTIVE_COLORS["slack"]},
                name="Max Slack",
                hovertemplate=(
                    "pair=%{customdata}<br>"
                    "ratio=%{x:.2f}<br>"
                    "mean flexibility=%{y:.2f}<br>"
                    "95% CI +/- %{error_y.array:.2f}<extra></extra>"
                ),
                customdata=grouped["label"],
            )
        )

    fig.update_layout(
        title="Average Flexibility vs Task/Agent Ratio (All Available Schedules)",
        xaxis_title="Number of Tasks/Number of Agents",
        yaxis_title="Average Flexibility Value",
        template="plotly_white",
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


def build_grouped_avg_task_completion_margin_plot(
    df: pd.DataFrame,
) -> go.Figure:
    has_makespan_margin = (
        "makespan_schedule_earliest_completion_time" in df.columns
        and df["makespan_schedule_earliest_completion_time"].notna().any()
    )
    has_slack_margin = (
        "slack_schedule_earliest_completion_time" in df.columns
        and df["slack_schedule_earliest_completion_time"].notna().any()
    )

    grouped = (
        df.groupby(["n_tasks", "n_resources"], as_index=False)
        .agg(
            mean_flex_margin=("flexibility_earliest_completion_time", "mean"),
            std_flex_margin=("flexibility_earliest_completion_time", "std"),
            mean_ect_margin=("earliest_completion_time_schedule_value", "mean"),
            std_ect_margin=("earliest_completion_time_schedule_value", "std"),
            mean_makespan_margin=(
                "makespan_schedule_earliest_completion_time",
                "mean",
            )
            if has_makespan_margin
            else ("flexibility_earliest_completion_time", "mean"),
            std_makespan_margin=(
                "makespan_schedule_earliest_completion_time",
                "std",
            )
            if has_makespan_margin
            else ("flexibility_earliest_completion_time", "std"),
            mean_slack_margin=("slack_schedule_earliest_completion_time", "mean")
            if has_slack_margin
            else ("flexibility_earliest_completion_time", "mean"),
            std_slack_margin=("slack_schedule_earliest_completion_time", "std")
            if has_slack_margin
            else ("flexibility_earliest_completion_time", "std"),
            count=("flexibility_earliest_completion_time", "count"),
        )
        .reset_index(drop=True)
    )

    grouped["tasks_per_resource"] = grouped["n_tasks"] / grouped["n_resources"]
    grouped["label"] = grouped["n_tasks"].astype(str) + "/" + grouped["n_resources"].astype(str)

    grouped["sem_flex_margin"] = grouped["std_flex_margin"] / np.sqrt(grouped["count"])
    grouped["sem_ect_margin"] = grouped["std_ect_margin"] / np.sqrt(grouped["count"])
    grouped["sem_makespan_margin"] = grouped["std_makespan_margin"] / np.sqrt(grouped["count"])
    grouped["sem_slack_margin"] = grouped["std_slack_margin"] / np.sqrt(grouped["count"])
    grouped["ci95_flex_margin"] = 1.96 * grouped["sem_flex_margin"]
    grouped["ci95_ect_margin"] = 1.96 * grouped["sem_ect_margin"]
    grouped["ci95_makespan_margin"] = 1.96 * grouped["sem_makespan_margin"]
    grouped["ci95_slack_margin"] = 1.96 * grouped["sem_slack_margin"]
    grouped = grouped.sort_values(["tasks_per_resource", "n_tasks", "n_resources"]).reset_index(drop=True)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_flex_margin"],
            mode="markers+lines",
            error_y={"type": "data", "array": grouped["ci95_flex_margin"], "visible": True},
            marker={
                "size": 9,
                "color": OBJECTIVE_COLORS["flexibility"],
                "symbol": OBJECTIVE_MARKERS["flexibility"],
            },
            line={"width": 2, "color": OBJECTIVE_COLORS["flexibility"]},
            name="Max Flexibility",
            hovertemplate=(
                "pair=%{customdata}<br>"
                "ratio=%{x:.2f}<br>"
                "mean task completion margin=%{y:.2f}<br>"
                "95% CI +/- %{error_y.array:.2f}<extra></extra>"
            ),
            customdata=grouped["label"],
        )
    )

    fig.add_trace(
        go.Scatter(
            x=grouped["tasks_per_resource"],
            y=grouped["mean_ect_margin"],
            mode="markers+lines+text",
            text=grouped["label"],
            textposition="top center",
            textfont={"size": 14},
            error_y={"type": "data", "array": grouped["ci95_ect_margin"], "visible": True},
            marker={
                "size": 9,
                "color": OBJECTIVE_COLORS["earliest_completion_time"],
                "symbol": OBJECTIVE_MARKERS["earliest_completion_time"],
            },
            line={"width": 2, "color": OBJECTIVE_COLORS["earliest_completion_time"]},
            name="Max Task Completion Margin",
            hovertemplate=(
                "pair=%{customdata}<br>"
                "ratio=%{x:.2f}<br>"
                "mean task completion margin=%{y:.2f}<br>"
                "95% CI +/- %{error_y.array:.2f}<extra></extra>"
            ),
            customdata=grouped["label"],
        )
    )

    if has_makespan_margin:
        fig.add_trace(
            go.Scatter(
                x=grouped["tasks_per_resource"],
                y=grouped["mean_makespan_margin"],
                mode="markers+lines",
                error_y={
                    "type": "data",
                    "array": grouped["ci95_makespan_margin"],
                    "visible": True,
                },
                marker={
                    "size": 9,
                    "color": OBJECTIVE_COLORS["makespan"],
                    "symbol": OBJECTIVE_MARKERS["makespan"],
                },
                line={"width": 2, "color": OBJECTIVE_COLORS["makespan"]},
                name="Min Makespan",
                hovertemplate=(
                    "pair=%{customdata}<br>"
                    "ratio=%{x:.2f}<br>"
                    "mean task completion margin=%{y:.2f}<br>"
                    "95% CI +/- %{error_y.array:.2f}<extra></extra>"
                ),
                customdata=grouped["label"],
            )
        )

    if has_slack_margin:
        fig.add_trace(
            go.Scatter(
                x=grouped["tasks_per_resource"],
                y=grouped["mean_slack_margin"],
                mode="markers+lines",
                error_y={"type": "data", "array": grouped["ci95_slack_margin"], "visible": True},
                marker={
                    "size": 9,
                    "color": OBJECTIVE_COLORS["slack"],
                    "symbol": OBJECTIVE_MARKERS["slack"],
                },
                line={"width": 2, "color": OBJECTIVE_COLORS["slack"]},
                name="Max Slack",
                hovertemplate=(
                    "pair=%{customdata}<br>"
                    "ratio=%{x:.2f}<br>"
                    "mean task completion margin=%{y:.2f}<br>"
                    "95% CI +/- %{error_y.array:.2f}<extra></extra>"
                ),
                customdata=grouped["label"],
            )
        )

    fig.update_layout(
        title="Average Task Completion Margin vs Task/Agent Ratio (All Available Schedules)",
        xaxis_title="Number of Tasks/Number of Agents",
        yaxis_title="Task Completion Margin",
        template="plotly_white",
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


def main() -> None:
    args = parse_args()

    if not args.input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {args.input_csv}")

    df = pd.read_csv(args.input_csv)
    validate_columns(df)

    df = df.copy()
    df["tasks_per_resource"] = df["n_tasks"] / df["n_resources"]
    has_slack = (
        "slack_objective_flexibility_value" in df.columns
        and df["slack_objective_flexibility_value"].notna().any()
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    grouped_avg_flex_fig = build_grouped_avg_flexibility_comparison_plot(
        df,
        has_slack,
    )
    grouped_avg_flex_path = args.output_dir / f"average_flexibility_grouped_{timestamp}.html"
    grouped_avg_completion_margin_fig = build_grouped_avg_task_completion_margin_plot(df)
    grouped_avg_completion_margin_path = (
        args.output_dir / f"average_task_completion_margin_grouped_{timestamp}.html"
    )

    grouped_avg_flex_fig.write_html(grouped_avg_flex_path)
    grouped_avg_completion_margin_fig.write_html(grouped_avg_completion_margin_path)

    print("Generated plots:")
    print(f"- {grouped_avg_flex_path}")
    print(f"- {grouped_avg_completion_margin_path}")


if __name__ == "__main__":
    main()
