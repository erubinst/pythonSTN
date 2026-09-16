"""
General-purpose driver for running a metric comparison across one or more
scenario profiles, one profile at a time. Replaces the one-off
rerun_sweep_type.py / run_baseline_comparison.py / run_baseline_300.py /
run_overlap_full_flex_vs_makespan.py scripts, which all did this same thing
with different hardcoded profile lists and combination lists.

Usage examples:

    # Rerun a whole sweep type's canonical results (overwrites the profile's
    # own <profile>_combination_summary.csv, same as rerun_sweep_type.py did):
    python -m tds_slack.simulator.run_comparison --sweep-type disruption \\
        --metrics save_flexibility full_flex makespan

    # Ad-hoc comparison on one profile, written to a separate suffixed file
    # so the profile's canonical results aren't touched:
    python -m tds_slack.simulator.run_comparison --profiles baseline \\
        --metrics save_flexibility full_flex makespan --output-suffix 300

    # Add full_regen combos alongside task_swap ones:
    python -m tds_slack.simulator.run_comparison --profiles baseline \\
        --metrics makespan --full-regen makespan save_flexibility

    # full_flex vs makespan across every overlap sweep profile, written
    # separately from that profile's canonical results:
    python -m tds_slack.simulator.run_comparison --sweep-type overlap \\
        --metrics full_flex makespan --output-suffix full_flex

Each metric passed to --metrics becomes two task_swap combinations
(max_moves=10 and max_moves=0). --minimize direction is inferred per metric
(makespan -> True, everything else -> False); override with
--minimize-true/--minimize-false if a new metric needs the other direction.
"""
import argparse
import time
from pathlib import Path

from tds_slack.simulator.simulation import run_profile_across_combinations

SCENARIOS_ROOT = Path(__file__).resolve().parent / "generated_scenarios"

DEFAULT_MINIMIZE_TRUE = {"makespan"}


def log(message, log_file):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    log_file.write(line + "\n")
    log_file.flush()


def resolve_profiles(args):
    if args.sweep_type:
        prefix = f"sweep_{args.sweep_type}_"
        dirs = sorted(p for p in SCENARIOS_ROOT.iterdir() if p.is_dir() and p.name.startswith(prefix))
        if not dirs:
            raise SystemExit(f"No folders found matching '{prefix}*' under {SCENARIOS_ROOT}")
        return dirs
    return [SCENARIOS_ROOT / name for name in args.profiles]


def minimize_for(metric, minimize_true, minimize_false):
    if metric in minimize_true:
        return True
    if metric in minimize_false:
        return False
    return metric in DEFAULT_MINIMIZE_TRUE


def build_combinations(args):
    minimize_true = set(args.minimize_true or [])
    minimize_false = set(args.minimize_false or [])

    combinations = []
    for metric in args.metrics:
        minimize = minimize_for(metric, minimize_true, minimize_false)
        combinations.append({"metric": metric, "max_moves": 10, "minimize": minimize})
        combinations.append({"metric": metric, "max_moves": 0, "minimize": minimize})
    for metric in args.full_regen:
        minimize = minimize_for(metric, minimize_true, minimize_false)
        combinations.append({
            "initial_metric": metric,
            "reschedule_mode": "full_regen",
            "regen_metric": metric,
            "minimize": minimize,
        })
    return combinations


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    profile_group = ap.add_mutually_exclusive_group(required=True)
    profile_group.add_argument("--profiles", nargs="+", help="Explicit profile folder names under generated_scenarios/")
    profile_group.add_argument("--sweep-type", help="Expand to every sweep_<type>_* folder, e.g. 'overlap'")

    ap.add_argument("--metrics", nargs="+", default=[], help="Metric names to run as task_swap combos (max_moves 10 and 0 each)")
    ap.add_argument("--full-regen", nargs="+", default=[], help="Metric names to run as full_regen combos")
    ap.add_argument("--minimize-true", nargs="+", default=None, help="Metric names that should use minimize=True (overrides default inference)")
    ap.add_argument("--minimize-false", nargs="+", default=None, help="Metric names that should use minimize=False (overrides default inference)")
    ap.add_argument("--output-suffix", default=None, help="If set, write <profile>_<suffix>_summary.csv/_detail.csv instead of overwriting the profile's canonical combination CSVs")
    ap.add_argument("--log-name", default=None, help="Log filename stem; defaults to sweep type or joined profile names")
    args = ap.parse_args()

    if not args.metrics and not args.full_regen:
        raise SystemExit("Specify at least one of --metrics or --full-regen")

    profile_dirs = resolve_profiles(args)
    combinations = build_combinations(args)

    log_name = args.log_name or (f"sweep_{args.sweep_type}" if args.sweep_type else "_".join(p.name for p in profile_dirs))
    if args.output_suffix:
        log_name += f"_{args.output_suffix}"
    log_path = SCENARIOS_ROOT / f"run_comparison_{log_name}.log"

    with open(log_path, "a") as log_file:
        log(
            f"Starting comparison on {len(profile_dirs)} profile(s): {[p.name for p in profile_dirs]}, "
            f"metrics={args.metrics}, full_regen={args.full_regen}, output_suffix={args.output_suffix}",
            log_file,
        )
        for i, profile_path in enumerate(profile_dirs, start=1):
            log(f"({i}/{len(profile_dirs)}) Running {profile_path.name}...", log_file)
            start = time.perf_counter()

            if args.output_suffix:
                output_csv = profile_path / f"{profile_path.name}_{args.output_suffix}_summary.csv"
                detail_csv = profile_path / f"{profile_path.name}_{args.output_suffix}_detail.csv"
            else:
                output_csv = None
                detail_csv = None

            try:
                run_profile_across_combinations(
                    profile_path=str(profile_path),
                    combinations=combinations,
                    output_csv_path=str(output_csv) if output_csv else None,
                    detail_csv_path=str(detail_csv) if detail_csv else None,
                )
            except Exception:
                import traceback
                log(f"({i}/{len(profile_dirs)}) FAILED {profile_path.name}:\n{traceback.format_exc()}", log_file)
                continue

            elapsed = time.perf_counter() - start
            log(f"({i}/{len(profile_dirs)}) Finished {profile_path.name} in {elapsed:.1f}s.", log_file)

        log("All profiles processed.", log_file)


if __name__ == "__main__":
    main()
