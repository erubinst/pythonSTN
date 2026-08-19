"""
plot_objective_comparison.py

Compares scheduling objectives (piggybacking-aware vs. baseline) on:
- Piggybacking opportunities
- Avg. time per caregiver (minutes)
- Summary table

Usage:
    python plot_objective_comparison.py
    python plot_objective_comparison.py --csv your_results.csv
"""

import argparse
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.gridspec import GridSpec
import pandas as pd
import numpy as np

DEFAULT_DATA = {
    "objective_label": ["min_travel", "min_makespan"],
    "piggybacking_count": [14, 12],
    "total_caregiver_time": [2940, 3017],
    "caregiver_time__emerald": [264, 261],
    "caregiver_time__coral": [747, 738],
    "caregiver_time__ruby": [624, 635],
    "caregiver_time__gray": [1305, 1383],
}

DISPLAY_NAMES = {
    "min_travel": "Piggybacking (Min Travel Time)",
    "min_makespan": "Baseline",
    "min_caregiver": "Piggybacking",
}

BASELINE_LABEL = "min_makespan"
# Set to None to include every objective in the CSV; otherwise, list the labels you want plotted.
PLOT_OBJECTIVE_LABELS = ("min_makespan", "min_caregiver")


def load_data(csv_path=None):
    if csv_path:
        df = pd.read_csv(csv_path)
    else:
        df = pd.DataFrame(DEFAULT_DATA)

    if PLOT_OBJECTIVE_LABELS is not None:
        df = df[df["objective_label"].isin(PLOT_OBJECTIVE_LABELS)].copy()

    if BASELINE_LABEL not in df["objective_label"].values:
        raise ValueError(f"Baseline label '{BASELINE_LABEL}' is not present after filtering.")

    # Baseline first
    df["_order"] = df["objective_label"].apply(lambda l: 0 if l == BASELINE_LABEL else 1)
    df = df.sort_values("_order").reset_index(drop=True)
    return df


def plot(df):
    caregiver_cols = [c for c in df.columns if c.startswith("caregiver_time__")]
    n_caregivers = len(caregiver_cols)

    x_labels = [DISPLAY_NAMES.get(lbl, lbl.replace("_", " ")) for lbl in df["objective_label"]]
    x = np.arange(len(df))

    baseline_idx = df.index[df["objective_label"] == BASELINE_LABEL][0]

    avg_per_icg_min = df["total_caregiver_time"] / n_caregivers
    baseline_per_icg_min = avg_per_icg_min.iloc[baseline_idx]
    per_icg_saved_min = (baseline_per_icg_min - avg_per_icg_min).round(3)
    pct_saved = (per_icg_saved_min / baseline_per_icg_min * 100).round(1)
    pig_diff = df["piggybacking_count"] - df["piggybacking_count"].iloc[baseline_idx]

    fig = plt.figure(figsize=(13, 9), facecolor="white")
    gs = GridSpec(2, 2, figure=fig, height_ratios=[1.6, 1], hspace=0.55, wspace=0.38)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax_table = fig.add_subplot(gs[1, :])

    fig.suptitle(
        "Piggybacking-Aware Scheduling vs. Baseline",
        fontsize=13, fontweight="bold", y=0.98,
    )

    xlabel = "Scheduling objective"

    # ── Panel 1: Piggybacking count ───────────────────────────────────────────
    bars1 = ax1.bar(x, df["piggybacking_count"], color="#4C8BBF", width=0.5, zorder=3)
    for bar, val in zip(bars1, df["piggybacking_count"]):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.2,
            str(val),
            ha="center", va="bottom", fontsize=11, fontweight="bold", color="#2A5A8A"
        )

    ax1.set_title("Piggybacking Opportunities", fontsize=11, pad=8)
    ax1.set_xlabel(xlabel, fontsize=9.5)
    ax1.set_ylabel("Piggybacking instances", fontsize=10)
    ax1.set_xticks(x)
    ax1.set_xticklabels(x_labels, fontsize=10)
    ax1.set_ylim(0, df["piggybacking_count"].max() * 1.3)
    ax1.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax1.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    ax1.spines[["top", "right"]].set_visible(False)

    # ── Panel 2: Avg. time per caregiver (minutes) ────────────────────────────
    bars2 = ax2.bar(x, avg_per_icg_min, color="#6BAE75", width=0.5, zorder=3)
    ax2.plot(x, avg_per_icg_min, "o--", color="#3A7A45", linewidth=1.5, zorder=4)

    for bar, val, saved in zip(bars2, avg_per_icg_min, per_icg_saved_min):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"{val:.2f}",
            ha="center", va="bottom", fontsize=10, fontweight="bold", color="#3A7A45"
        )

    ax2.set_title(f"Avg. Time per Caregiver ({n_caregivers} ICGs)", fontsize=11, pad=8)
    ax2.set_xlabel(xlabel, fontsize=9.5)
    ax2.set_ylabel("Avg. time per ICG (minutes)", fontsize=10)
    ax2.set_xticks(x)
    ax2.set_xticklabels(x_labels, fontsize=10)
    ax2.set_ylim(0, avg_per_icg_min.max() * 1.08)
    ax2.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.2f}"))
    ax2.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    ax2.spines[["top", "right"]].set_visible(False)

    # ── Summary table ─────────────────────────────────────────────────────────
    ax_table.axis("off")

    col_labels = [
        "Objective",
        "Piggybacking\ninstances",
        "+ instances vs.\nbaseline",
        f"Avg. time per ICG\n({n_caregivers} ICGs, mins)",
        "Avg. time saved\nper ICG (mins)",
        "% time saved\nper ICG",
    ]

    table_data = []
    for i, row in df.iterrows():
        label = DISPLAY_NAMES.get(row["objective_label"], row["objective_label"])
        table_data.append([
            label,
            str(row["piggybacking_count"]),
            f"+{pig_diff.iloc[i]}" if pig_diff.iloc[i] > 0 else ("—" if pig_diff.iloc[i] == 0 else str(pig_diff.iloc[i])),
            f"{avg_per_icg_min.iloc[i]:.2f}",
            f"−{per_icg_saved_min.iloc[i]:.2f}" if per_icg_saved_min.iloc[i] > 0 else "—",
            f"{pct_saved.iloc[i]:.1f}%" if pct_saved.iloc[i] > 0 else "baseline",
        ])

    tbl = ax_table.table(
        cellText=table_data,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9.5)
    tbl.scale(1, 2.0)

    for j in range(len(col_labels)):
        tbl[0, j].set_facecolor("#2A5A8A")
        tbl[0, j].set_text_props(color="white", fontweight="bold")

    savings_cols = {4, 5}
    for i in range(1, len(table_data) + 1):
        for j in range(len(col_labels)):
            cell = tbl[i, j]
            if j in savings_cols:
                cell.set_facecolor("#E8F5E9" if i % 2 == 0 else "#C8E6C9")
            else:
                cell.set_facecolor("#F5F5F5" if i % 2 == 0 else "white")

    ax_table.set_title("Summary", fontsize=11, fontweight="bold", pad=4, loc="left", x=0.01)

    plt.savefig("objective_comparison_results.png", dpi=150, bbox_inches="tight")
    print("Saved: objective_comparison_results.png")
    plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare scheduling objectives.")
    parser.add_argument("--csv", default=None, help="Path to results CSV file")
    args = parser.parse_args()

    df = load_data(args.csv)
    plot(df)