#!/usr/bin/env python3
"""Plot runtime trends from objective timing sweep results.

This script reads the detailed CSV produced by objective_timing_sweep.py and
writes one interactive Plotly chart:
1) Mean runtime by task/resource ratio with 95% CI, split by objective.
"""

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


OBJECTIVE_COLORS = {
    "flexibility": "#8e44ad",
    "makespan": "#1abc9c",
    "earliest_completion_time": "#e74c3c",
    "slack": "#7f8c8d",
}

OBJECTIVE_MARKERS = {
    "flexibility": "circle",
    "makespan": "square",
    "earliest_completion_time": "diamond",
    "slack": "triangle-up",
}

OBJECTIVE_LABELS = {
    "flexibility": "Max Flexibility",
    "makespan": "Min Makespan",
    "earliest_completion_time": "Max Task Completion Margin",
    "slack": "Max Slack",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot runtime trends for flexibility, makespan, earliest completion time, and slack objective sweeps."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help="Path to objective_timing_results_*.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("scenario_sweeps/plots"),
        help="Directory for generated plot files",
    )
    parser.add_argument(
        "--max-ratio",
        type=float,
        default=9.9,
        help="Upper bound for the task/resource ratio shown in the plots. Set to a larger value to include ratio 10.",
    )
    return parser.parse_args()


def validate_columns(df: pd.DataFrame) -> None:
    required = {
        "objective",
        "n_tasks",
        "n_resources",
        "ratio",
        "elapsed_seconds",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")


def objective_order(df: pd.DataFrame) -> list[str]:
    preferred = ["flexibility", "makespan", "earliest_completion_time", "slack"]
    present = [objective for objective in preferred if objective in set(df["objective"])]
    extra = sorted(set(df["objective"]) - set(present))
    return present + extra


def build_runtime_trend_figure(df: pd.DataFrame, max_ratio: float | None = None) -> go.Figure:
    if max_ratio is not None:
        df = df[df["ratio"] <= max_ratio].copy()

    objectives = objective_order(df)
    fig = make_subplots(
        rows=len(objectives),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=[f"{OBJECTIVE_LABELS.get(objective, objective.title())} Runtime" for objective in objectives],
    )

    for row_idx, objective in enumerate(objectives, start=1):
        objective_df = df[df["objective"] == objective].copy()
        objective_df = objective_df.sort_values(["ratio", "n_tasks", "n_resources", "elapsed_seconds"])

        x = objective_df["ratio"].to_numpy(dtype=float)
        y = objective_df["elapsed_seconds"].to_numpy(dtype=float)

        if len(objective_df) >= 2:
            slope, intercept = np.polyfit(x, y, deg=1)
            x_line = np.linspace(x.min(), x.max(), 100)
            y_line = slope * x_line + intercept
        else:
            slope = 0.0
            x_line = x
            y_line = y

        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="markers",
                marker={"size": 7, "opacity": 0.55, "color": OBJECTIVE_COLORS.get(objective)},
                name=f"{OBJECTIVE_LABELS.get(objective, objective.title())} scenarios",
                legendgroup=objective,
                showlegend=row_idx == 1,
                customdata=np.stack(
                    [objective_df["n_tasks"], objective_df["n_resources"], objective_df["scenario_idx"]],
                    axis=-1,
                ),
                hovertemplate=(
                    "ratio=%{x:.2f}<br>"
                    "tasks=%{customdata[0]}<br>"
                    "resources=%{customdata[1]}<br>"
                    "scenario=%{customdata[2]}<br>"
                    "elapsed=%{y:.6f}s<extra></extra>"
                ),
            ),
            row=row_idx,
            col=1,
        )

        fig.add_trace(
            go.Scatter(
                x=x_line,
                y=y_line,
                mode="lines",
                line={"width": 3, "color": OBJECTIVE_COLORS.get(objective)},
                name=f"{OBJECTIVE_LABELS.get(objective, objective.title())} trend",
                legendgroup=objective,
                showlegend=row_idx == 1,
                hovertemplate="trend=%{y:.6f}s<extra></extra>",
            ),
            row=row_idx,
            col=1,
        )

        fig.update_yaxes(title_text="Elapsed Seconds", row=row_idx, col=1)

    fig.update_xaxes(title_text="Task/Resource Ratio (n_tasks / n_resources)", row=len(objectives), col=1)
    fig.update_layout(
        title="Objective Timing Sweep: Scenario-Level Runtime vs Task/Resource Ratio",
        template="plotly_white",
        height=320 * max(1, len(objectives)),
        hovermode="closest",
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


def build_grouped_ci_figure(df: pd.DataFrame, max_ratio: float | None = None) -> go.Figure:
    if max_ratio is not None:
        df = df[df["ratio"] <= max_ratio].copy()

    grouped = (
        df.groupby(["n_tasks", "n_resources", "ratio", "objective"], as_index=False)
        .agg(
            mean_elapsed_seconds=("elapsed_seconds", "mean"),
            scenarios=("elapsed_seconds", "count"),
        )
        .reset_index(drop=True)
    )

    objectives = objective_order(df)
    fig = go.Figure()
    if len(objectives) > 1:
        x_offsets = np.linspace(-0.03, 0.03, len(objectives))
    else:
        x_offsets = np.array([0.0])

    for objective, x_offset in zip(objectives, x_offsets):
        objective_group = grouped[grouped["objective"] == objective].copy()
        objective_group = objective_group.sort_values(["ratio", "n_tasks", "n_resources"])
        x_values = objective_group["ratio"].to_numpy(dtype=float) + x_offset

        fig.add_trace(
            go.Scatter(
                x=x_values,
                y=objective_group["mean_elapsed_seconds"],
                mode="markers+lines",
                marker={
                    "size": 10,
                    "color": OBJECTIVE_COLORS.get(objective),
                    "symbol": OBJECTIVE_MARKERS.get(objective, "circle"),
                },
                line={"width": 2, "color": OBJECTIVE_COLORS.get(objective)},
                name=OBJECTIVE_LABELS.get(objective, objective.title()),
                hovertemplate=(
                    "pair=%{customdata[1]}<br>"
                    "ratio=%{customdata[0]:.2f}<br>"
                    "mean=%{y:.6f}s<br>"
                    "scenarios=%{customdata[2]}<extra></extra>"
                ),
                customdata=np.stack(
                    [
                        objective_group["ratio"].to_numpy(dtype=float),
                        (objective_group["n_tasks"].astype(str) + "/" + objective_group["n_resources"].astype(str)).to_numpy(),
                        objective_group["scenarios"].to_numpy(dtype=float),
                    ],
                    axis=-1,
                ),
            )
        )

    fig.update_layout(
        title="Objective Timing Sweep: Mean Runtime by Task/Resource Ratio",
        xaxis_title="Task/Resource Ratio (n_tasks / n_resources)",
        yaxis_title="Mean Elapsed Seconds",
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


def main() -> None:
    args = parse_args()

    if not args.input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {args.input_csv}")

    df = pd.read_csv(args.input_csv)
    validate_columns(df)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    grouped_ci_fig = build_grouped_ci_figure(df, max_ratio=args.max_ratio)
    grouped_ci_path = args.output_dir / f"objective_timing_grouped_ci_{timestamp}.html"
    grouped_ci_fig.write_html(grouped_ci_path)

    print(f"Generated plot: {grouped_ci_path}")


if __name__ == "__main__":
    main()