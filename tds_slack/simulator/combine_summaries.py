#!/usr/bin/env python3
"""
Combine per-profile `{profile_name}_combination_summary.csv` files (as
written by `run_profile_across_combinations` in simulation.py) into one
CSV, with a few extra metadata columns parsed from the folder name to make
plotting easier (e.g. filtering/faceting by axis, tier, or grid position).

Expected layout (one level below this script):

    simulator/
        combine_summaries.py          <- this script
        generated_scenarios/
            workload_sparse/workload_sparse_combination_summary.csv
            grid_workload_dense_pressure_tight/grid_workload_dense_pressure_tight_combination_summary.csv
            ...

Usage:
    python combine_summaries.py
    python combine_summaries.py --scenarios-dir ./generated_scenarios --output combined_combination_summary.csv
"""

import argparse
import json
from pathlib import Path

import pandas as pd

# Mirrors the alias rule in representative_set.py's build_grid_profiles():
# a "medium" tier is defined to equal BASELINE for that dimension, so a
# grid cell with either axis at "medium" was never generated as its own
# grid_* folder -- it's equivalent to an existing single-axis profile.
# Kept here as plain constants (rather than importing representative_set.py)
# so this script works standalone from just the CSVs on disk. If the tiers
# in representative_set.py's AXES ever change, update these to match.
WORKLOAD_TIERS = ["sparse", "medium", "dense"]
PRESSURE_TIERS = ["loose", "medium", "tight"]

DEFAULT_MANIFEST_FILENAME = "manifest.json"


def grid_cell_alias(workload_tier: str, pressure_tier: str):
    """
    Name of the existing single-axis (or baseline) profile that a
    workload x pressure grid cell is equivalent to, or None if the cell is
    a genuine combination that should have its own grid_* folder.
    """
    if workload_tier == "medium" and pressure_tier == "medium":
        return "workload_medium"  # baseline-equivalent
    if workload_tier == "medium":
        return f"pressure_{pressure_tier}"
    if pressure_tier == "medium":
        return f"workload_{workload_tier}"
    return None


def _read_sweep_level_value(profile_dir: Path, manifest_filename: str):
    """
    sweep_* profile names only encode the level *index* (e.g.
    sweep_scale_04), not the exact value that level was pinned to (e.g.
    n_resources=12) -- that only exists in the per-profile manifest.json
    that representative_set.py writes alongside the scenarios. Read it and
    pull sweep_level_value/sweep_level_index off the first entry (every
    scenario in a sweep profile shares the same level).

    Returns (level_value, level_index) or (None, None) if the manifest is
    missing, empty, or doesn't have sweep metadata (e.g. an older run).
    """
    manifest_path = profile_dir / manifest_filename
    if not manifest_path.exists():
        return None, None
    try:
        with open(manifest_path) as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None, None
    if not entries:
        return None, None
    first = entries[0]
    return first.get("sweep_level_value"), first.get("sweep_level_index")


def parse_profile_name(name: str) -> dict:
    """
    Best-effort parse of a profile folder name into metadata columns for
    plotting. Handles:
      - grid profiles: grid_workload_{tier}_pressure_{tier}
      - single-axis profiles: {axis}_{tier}  (axis is always one word:
        workload, overlap, disruption, pressure, scale)
      - sweep profiles: sweep_{axis}_{level_index}  (fine-grained,
        exact-value axes from representative_set.py's SWEEP_AXES; the
        exact swept value isn't in the name and gets filled in separately
        from the profile's manifest.json, see _read_sweep_level_value)
    Anything that doesn't match any of these shapes still gets a row -- it
    just won't have the parsed columns filled in (left as None), so it
    won't be silently dropped from the combined file.
    """
    info = dict(
        profile_type=None,
        axis=None,
        tier=None,
        workload_tier=None,
        pressure_tier=None,
        grid_cell=None,
        aliased_from=None,
        sweep_axis=None,
        sweep_level_index=None,
        sweep_level_value=None,
    )

    if name.startswith("grid_workload_") and "_pressure_" in name:
        info["profile_type"] = "grid"
        rest = name[len("grid_workload_"):]
        workload_tier, pressure_tier = rest.split("_pressure_", 1)
        info["workload_tier"] = workload_tier
        info["pressure_tier"] = pressure_tier
        info["grid_cell"] = name
    elif name.startswith("sweep_"):
        info["profile_type"] = "sweep"
        rest = name[len("sweep_"):]  # e.g. "scale_04"
        axis_name, level_index_str = rest.rsplit("_", 1)
        info["sweep_axis"] = axis_name
        try:
            info["sweep_level_index"] = int(level_index_str)
        except ValueError:
            pass  # unexpected name shape -- leave index as None rather than crash
    elif "_" in name:
        info["profile_type"] = "axis"
        axis, tier = name.split("_", 1)
        info["axis"] = axis
        info["tier"] = tier
        # so axis-sweep rows can be filtered/plotted alongside grid rows
        # using the same workload_tier / pressure_tier columns
        if axis == "workload":
            info["workload_tier"] = tier
        elif axis == "pressure":
            info["pressure_tier"] = tier

    return info


def _add_metadata_columns(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    df = df.copy()
    insert_at = (df.columns.get_loc("profile") + 1) if "profile" in df.columns else 0
    for i, (col, val) in enumerate(meta.items()):
        df.insert(insert_at + i, col, val)
    return df


def _normalize_metric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Some combination_summary.csv files were written by an older version of
    simulation.py that recorded a single 'metric' column (e.g. 'makespan',
    'flexibility') instead of the current split 'initial_metric' /
    'task_swap_metric' columns. When frames with different schemas are
    concatenated, pandas outer-joins the columns and leaves the missing
    ones as NaN -- which silently breaks anything that filters on
    task_swap_metric. Backfill from 'metric' wherever the split columns
    are absent or null, so every row is usable regardless of which
    simulation.py version produced it.
    """
    df = df.copy()
    if "metric" not in df.columns:
        return df

    for col in ("initial_metric", "task_swap_metric"):
        if col not in df.columns:
            df[col] = df["metric"]
        else:
            df[col] = df[col].fillna(df["metric"])

    return df


def combine_summaries(scenarios_dir: Path, output_path: Path, manifest_filename: str = DEFAULT_MANIFEST_FILENAME) -> pd.DataFrame:
    scenarios_dir = Path(scenarios_dir)
    if not scenarios_dir.is_dir():
        raise SystemExit(f"Scenarios directory not found: {scenarios_dir}")

    profile_dirs = sorted(p for p in scenarios_dir.iterdir() if p.is_dir())

    frames = []
    raw_by_name = {}  # profile_name -> raw (unmodified) df, for alias lookups
    missing = []
    sweep_missing_value = []
    for profile_dir in profile_dirs:
        csv_path = profile_dir / f"{profile_dir.name}_combination_summary.csv"
        if not csv_path.exists():
            missing.append(profile_dir.name)
            continue

        df = pd.read_csv(csv_path)
        if df.empty:
            missing.append(f"{profile_dir.name} (empty csv)")
            continue

        df = _normalize_metric_columns(df)
        raw_by_name[profile_dir.name] = df

        meta = parse_profile_name(profile_dir.name)
        if meta["profile_type"] == "sweep":
            level_value, level_index_from_manifest = _read_sweep_level_value(profile_dir, manifest_filename)
            if level_value is None:
                sweep_missing_value.append(profile_dir.name)
            else:
                meta["sweep_level_value"] = level_value
                # Manifest is the source of truth if it disagrees with the
                # name-parsed index for some reason (e.g. manual rename).
                if level_index_from_manifest is not None:
                    meta["sweep_level_index"] = level_index_from_manifest

        frames.append(_add_metadata_columns(df, meta))

    if missing:
        print(f"Skipped {len(missing)} folder(s) with no/empty combination_summary.csv:")
        for name in missing:
            print(f"  - {name}")

    if sweep_missing_value:
        print(f"\n{len(sweep_missing_value)} sweep profile(s) found but couldn't read their exact "
              f"level value from '{manifest_filename}' (sweep_level_value left blank -- rows are still "
              f"included, just without an exact x-axis value):")
        for name in sweep_missing_value:
            print(f"  - {name}")

    # Synthesize rows for grid cells that were intentionally never generated
    # because they're equivalent to an existing single-axis profile (see
    # grid_cell_alias). This is what makes the full 3x3 workload x pressure
    # grid available for plotting even though only the 4 genuinely new
    # corner cells exist as their own grid_* folders.
    alias_rows_added = 0
    alias_missing = []
    for workload_tier in WORKLOAD_TIERS:
        for pressure_tier in PRESSURE_TIERS:
            cell_name = f"grid_workload_{workload_tier}_pressure_{pressure_tier}"
            if cell_name in raw_by_name:
                continue  # a real grid_* folder exists for this cell already

            alias = grid_cell_alias(workload_tier, pressure_tier)
            if alias is None:
                # Should have its own grid_* folder but doesn't -- that's a
                # genuine gap (e.g. not yet generated), not an alias case.
                alias_missing.append(f"{cell_name} (no grid_* folder, no alias)")
                continue
            if alias not in raw_by_name:
                alias_missing.append(f"{cell_name} (alias '{alias}' not found)")
                continue

            meta = dict(
                profile_type="grid_alias",
                axis=None,
                tier=None,
                workload_tier=workload_tier,
                pressure_tier=pressure_tier,
                grid_cell=cell_name,
                aliased_from=alias,
                sweep_axis=None,
                sweep_level_index=None,
                sweep_level_value=None,
            )
            frames.append(_add_metadata_columns(raw_by_name[alias], meta))
            alias_rows_added += 1

    if alias_missing:
        print(f"\n{len(alias_missing)} grid cell(s) have no data (not generated and not aliasable):")
        for name in alias_missing:
            print(f"  - {name}")

    if not frames:
        raise SystemExit("No combination_summary.csv files found -- nothing to combine.")

    combined = pd.concat(frames, ignore_index=True)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)

    print(f"\nCombined {len(frames)} source(s) ({alias_rows_added} synthesized from aliases), {len(combined)} row(s) total.")
    print(f"Saved to {output_path}")

    return combined


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--scenarios-dir",
        type=str,
        default=str(Path(__file__).resolve().parent / "generated_scenarios"),
        help="Directory containing one subfolder per profile (default: ./generated_scenarios next to this script)",
    )
    ap.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV path (default: <scenarios-dir>/combined_combination_summary.csv)",
    )
    ap.add_argument(
        "--manifest-filename",
        type=str,
        default=DEFAULT_MANIFEST_FILENAME,
        help=f"Per-profile manifest filename to read sweep_level_value from (default: {DEFAULT_MANIFEST_FILENAME})",
    )
    args = ap.parse_args()

    scenarios_dir = Path(args.scenarios_dir)
    output_path = Path(args.output) if args.output else scenarios_dir / "combined_combination_summary.csv"

    combine_summaries(scenarios_dir, output_path, manifest_filename=args.manifest_filename)


if __name__ == "__main__":
    main()