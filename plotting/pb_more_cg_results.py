"""
plot_piggybacking_results.py

Visualizes care coordination scheduling results:
- Avg. piggybacking opportunities vs. pairs with caregiver crossover
- Avg. time per caregiver (in minutes) vs. pairs with caregiver crossover
- Summary table with time saved metrics

Usage:
    python plot_piggybacking_results.py
    python plot_piggybacking_results.py --csv your_results.csv
"""

import argparse
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.gridspec import GridSpec
import pandas as pd
import numpy as np
from fractions import Fraction

DEFAULT_DATA = {
    "combined_caregiver_count": [0, 2, 3, 4],
    "avg_total_caregiver_time": [2940.0, 2868.6666666666665, 2776.25, 2622.0],
    "avg_piggybacking_count": [14.0, 16.5, 20.0, 21.0],
}


def load_data(csv_path=None):
    if csv_path:
        df = pd.read_csv(csv_path)
    else:
        df = pd.DataFrame(DEFAULT_DATA)
    df = df.sort_values("combined_caregiver_count").reset_index(drop=True)
    return df


def make_x_labels(df):
    max_k = int(df["combined_caregiver_count"].max())
    labels = []
    for k in df["combined_caregiver_count"]:
        k = int(k)
        labels.append("0" if max_k == 0 else f"{k}/{max_k}")
    return labels


def plot(df):
    x_labels = make_x_labels(df)
    x = np.arange(len(df))

    n_caregivers = int(df["combined_caregiver_count"].max())

    pig_increase = df["avg_piggybacking_count"] - df["avg_piggybacking_count"].iloc[0]

    # Per-ICG time in minutes
    avg_per_icg_min = df["avg_total_caregiver_time"] / n_caregivers
    baseline_per_icg_min = avg_per_icg_min.iloc[0]
    per_icg_saved_min = (baseline_per_icg_min - avg_per_icg_min).round(3)
    pct_per_icg_saved = (per_icg_saved_min / baseline_per_icg_min * 100).round(1)

    fig = plt.figure(figsize=(13, 9), facecolor="white")
    gs = GridSpec(2, 2, figure=fig, height_ratios=[1.6, 1], hspace=0.55, wspace=0.38)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax_table = fig.add_subplot(gs[1, :])

    fig.suptitle(
        "Effect of Increasing Caregiver Crossover on Piggybacking & Caregiver Time",
        fontsize=13, fontweight="bold", y=0.98,
    )

    xlabel = "Pairs with caregiver crossover\n(fraction of all pairs)"

    # ── Panel 1: Avg. piggybacking count ─────────────────────────────────────
    bars1 = ax1.bar(x, df["avg_piggybacking_count"], color="#4C8BBF", width=0.5, zorder=3)
    ax1.plot(x, df["avg_piggybacking_count"], "o--", color="#2A5A8A", linewidth=1.5, zorder=4)

    for bar, val in zip(bars1, df["avg_piggybacking_count"]):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.2,
            f"{val:.1f}",
            ha="center", va="bottom", fontsize=11, fontweight="bold", color="#2A5A8A"
        )

    ax1.set_title("Avg. Piggybacking Opportunities", fontsize=11, pad=8)
    ax1.set_xlabel(xlabel, fontsize=9.5)
    ax1.set_ylabel("Avg. piggybacking instances", fontsize=10)
    ax1.set_xticks(x)
    ax1.set_xticklabels(x_labels, fontsize=10)
    ax1.set_ylim(0, df["avg_piggybacking_count"].max() * 1.25)
    ax1.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax1.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    ax1.spines[["top", "right"]].set_visible(False)

    # ── Panel 2: Avg. time per caregiver (minutes) ───────────────────────────
    bars2 = ax2.bar(x, avg_per_icg_min, color="#6BAE75", width=0.5, zorder=3)
    ax2.plot(x, avg_per_icg_min, "o--", color="#3A7A45", linewidth=1.5, zorder=4)

    for bar, val, saved in zip(bars2, avg_per_icg_min, per_icg_saved_min):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.05,
            f"{val:.2f}",
            ha="center", va="bottom", fontsize=10, fontweight="bold", color="#3A7A45"
        )

    ax2.set_title(f"Avg. Time per Caregiver (across {n_caregivers} ICGs)", fontsize=11, pad=8)
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
        "Pairs with\ncrossover",
        "Avg. piggybacking\ninstances",
        "+ instances vs.\nno crossover",
        f"Avg. time per ICG\n(across {n_caregivers} ICGs, mins)",
        "Avg. time saved\nper ICG (mins)",
        "% time saved\nper ICG",
    ]

    table_data = []
    for i, row in df.iterrows():
        table_data.append([
            x_labels[i],
            f"{row['avg_piggybacking_count']:.1f}",
            f"+{pig_increase.iloc[i]:.1f}" if pig_increase.iloc[i] >= 0 else f"{pig_increase.iloc[i]:.1f}",
            f"{avg_per_icg_min.iloc[i]:.2f}",
            f"−{per_icg_saved_min.iloc[i]:.2f}" if per_icg_saved_min.iloc[i] > 0 else "—",
            f"{pct_per_icg_saved.iloc[i]:.1f}%" if pct_per_icg_saved.iloc[i] > 0 else "baseline",
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

    plt.savefig("piggybacking_results.png", dpi=150, bbox_inches="tight")
    print("Saved: piggybacking_results.png")
    plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plot piggybacking scheduling results.")
    parser.add_argument("--csv", default=None, help="Path to results CSV file")
    args = parser.parse_args()

    df = load_data(args.csv)
    plot(df)