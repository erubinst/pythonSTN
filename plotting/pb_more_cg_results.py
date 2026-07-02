"""
plot_piggybacking_results.py

Visualizes care coordination scheduling results:
- Piggybacking opportunities vs. pairs with caregiver crossover
- Total caregiver time vs. pairs with caregiver crossover
- Per-caregiver time breakdown
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

# ── Default inline data (replace with --csv to load from file) ────────────────
DEFAULT_DATA = {
    "scenario_name": ["k00_of_02", "k01_of_02", "k02_of_02"],
    "k": [0, 1, 2],
    "piggybacking_count": [8, 11, 13],
    "total_caregiver_time": [2639, 2546, 2398],
    "caregiver_time__coral": [710, 841, 668],
    "caregiver_time__ruby": [624, 400, 321],
    "caregiver_time__gray": [1305, 1305, 1409],
}

CAREGIVER_COLORS = {
    "coral": "#E07060",
    "ruby": "#A03050",
    "gray": "#909090",
}


def load_data(csv_path=None):
    if csv_path:
        df = pd.read_csv(csv_path)
        df["k"] = df["scenario_name"].str.extract(r"k(\d+)").astype(int)
    else:
        df = pd.DataFrame(DEFAULT_DATA)
    df = df.sort_values("k").reset_index(drop=True)
    return df


def make_x_labels(df):
    n = len(df)
    labels = []
    for k in df["k"]:
        frac = Fraction(k + 1, n).limit_denominator(20)
        if frac.denominator == 1:
            labels.append(str(frac.numerator))
        else:
            labels.append(f"{frac.numerator}/{frac.denominator}")
    return labels


def plot(df):
    n_rows = len(df)
    x_labels = make_x_labels(df)
    x = np.arange(n_rows)

    caregiver_cols = [c for c in df.columns if c.startswith("caregiver_time__")]
    caregiver_names = [c.replace("caregiver_time__", "") for c in caregiver_cols]
    n_caregivers = len(caregiver_cols)

    baseline_time = df["total_caregiver_time"].iloc[0]
    time_saved = baseline_time - df["total_caregiver_time"]
    pct_saved = (time_saved / baseline_time * 100).round(1)
    pig_increase = df["piggybacking_count"] - df["piggybacking_count"].iloc[0]
    avg_time_saved_per_icg = (time_saved / n_caregivers).round(0).astype(int)

    fig = plt.figure(figsize=(16, 10), facecolor="white")
    gs = GridSpec(2, 3, figure=fig, height_ratios=[1.6, 1], hspace=0.55, wspace=0.38)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[0, 2])
    ax_table = fig.add_subplot(gs[1, :])

    fig.suptitle(
        "Effect of Increasing Caregiver Crossover on Piggybacking & Caregiver Time",
        fontsize=13, fontweight="bold", y=0.98,
    )

    xlabel = "Pairs with caregiver crossover\n(fraction of all pairs)"

    # ── Panel 1: Piggybacking count ───────────────────────────────────────────
    bars1 = ax1.bar(x, df["piggybacking_count"], color="#4C8BBF", width=0.5, zorder=3)
    ax1.plot(x, df["piggybacking_count"], "o--", color="#2A5A8A", linewidth=1.5, zorder=4)

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

    # ── Panel 2: Total caregiver time ─────────────────────────────────────────
    bars2 = ax2.bar(x, df["total_caregiver_time"], color="#6BAE75", width=0.5, zorder=3)
    ax2.plot(x, df["total_caregiver_time"], "o--", color="#3A7A45", linewidth=1.5, zorder=4)

    for bar, val, saved in zip(bars2, df["total_caregiver_time"], time_saved):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 20,
            f"{val:,}",
            ha="center", va="bottom", fontsize=10, fontweight="bold", color="#3A7A45"
        )
        if saved > 0:
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() / 2,
                f"−{saved:,} min\nsaved",
                ha="center", va="center", fontsize=8.5, color="white", fontweight="bold"
            )

    ax2.set_title("Total Caregiver Time", fontsize=11, pad=8)
    ax2.set_xlabel(xlabel, fontsize=9.5)
    ax2.set_ylabel("Total time (minutes)", fontsize=10)
    ax2.set_xticks(x)
    ax2.set_xticklabels(x_labels, fontsize=10)
    ymin = df["total_caregiver_time"].min() * 0.97
    ymax = df["total_caregiver_time"].max() * 1.06
    ax2.set_ylim(ymin, ymax)
    ax2.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax2.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    ax2.spines[["top", "right"]].set_visible(False)

    # ── Panel 3: Per-caregiver time ───────────────────────────────────────────
    width = 0.8 / n_caregivers
    for i, name in enumerate(caregiver_names):
        col = f"caregiver_time__{name}"
        color = CAREGIVER_COLORS.get(name, f"C{i}")
        offset = (i - (n_caregivers - 1) / 2) * width
        ax3.bar(x + offset, df[col], width, label=name.capitalize(),
                color=color, zorder=3)
        ax3.plot(x + offset, df[col], "o--", color=color,
                 linewidth=1, markersize=4, zorder=4, alpha=0.7)

    ax3.set_title("Time per Caregiver (ICG)", fontsize=11, pad=8)
    ax3.set_xlabel(xlabel, fontsize=9.5)
    ax3.set_ylabel("Caregiver time (minutes)", fontsize=10)
    ax3.set_xticks(x)
    ax3.set_xticklabels(x_labels, fontsize=10)
    ax3.legend(title="ICG", fontsize=9)
    ax3.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax3.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    ax3.spines[["top", "right"]].set_visible(False)

    # ── Summary table ─────────────────────────────────────────────────────────
    ax_table.axis("off")

    col_labels = [
        "Pairs with\ncrossover",
        "Piggybacking\ninstances",
        "+ instances vs.\nno crossover",
        "Total caregiver\ntime (min)",
        "Total time\nsaved (min)",
        "Avg. time saved\nper ICG (min)",
        "% time\nsaved",
    ]

    table_data = []
    for i, row in df.iterrows():
        table_data.append([
            x_labels[i],
            str(row["piggybacking_count"]),
            f"+{pig_increase.iloc[i]}" if pig_increase.iloc[i] >= 0 else str(pig_increase.iloc[i]),
            f"{row['total_caregiver_time']:,}",
            f"−{time_saved.iloc[i]:,}" if time_saved.iloc[i] > 0 else "—",
            f"−{avg_time_saved_per_icg.iloc[i]:,}" if avg_time_saved_per_icg.iloc[i] > 0 else "—",
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

    savings_cols = {4, 5, 6}
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