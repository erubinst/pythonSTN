#!/usr/bin/env python3
"""
Generate representative scheduling scenarios, organized one folder per profile.

Hard constraints on every scenario:
    capability_overlap >= 0.4
    downtime_prob == 0.0   (initial resource downtimes are out of scope for
                            this study; disruption is driven entirely by
                            future_downtime_count instead, see the
                            "disruption" axis below)

(There is no floor on n_tasks / n_resources anymore -- see the workload_*
profiles below, which deliberately sweep that ratio from sparse to dense.)

Design pattern
---------------
Profiles are built from a single BASELINE parameter set plus a small number
of "axes". Each axis defines a few named tiers that override *one*
dimension of the baseline and leave everything else untouched. This means
two profiles on the same axis (e.g. workload_sparse vs workload_dense)
differ in exactly one respect, so if your resilience metric moves between
them you know what caused it. This is deliberately different from the old
profile list, where scale/overlap/downtime/deadlines often shifted
together and confounded comparisons.

To add a new axis (e.g. capability overlap, disruption pressure, time
pressure, scale), add an entry to AXES below with 2-3 tiers. Each tier is
just a dict of overrides applied on top of BASELINE.

Usage:
    # Generate every profile (20 scenarios each by default):
    python generate_representative_set.py --output-dir ./slack_scenarios --per-profile 20

    # Generate just one profile:
    python generate_representative_set.py --output-dir ./slack_scenarios --profile workload_dense

    # Generate a couple of specific profiles:
    python generate_representative_set.py --output-dir ./slack_scenarios \\
        --profile workload_sparse --profile workload_dense

    # See available profile names:
    python generate_representative_set.py --list-profiles
"""

import argparse
import json
import math
import os
import random

from scenario_generator import (
    generate_scenario,
    write_scenario_files,
)

MIN_OVERLAP = 0.4

# Initial resource downtimes (the ones baked into resourceTypes at generation
# time) are not part of this study -- only *future* downtimes (the disruption
# events injected after the fact) matter for the resilience axis. downtime_prob
# is therefore always forced to 0 for every profile/scenario; see the hard
# override in build_scenario_params below.
FIXED_DOWNTIME_PROB = 0.0


# ─── Baseline + axes ────────────────────────────────────────────────────
# BASELINE is a "typical" mid-range scenario. Every profile is BASELINE
# with one axis's tier merged on top, so exactly one dimension changes
# between tiers of the same axis.

BASELINE = dict(
    n_resources=(4, 7),
    ratio=(6, 10),                      # n_tasks = n_resources * ratio
    n_locations=(3, 6),
    caps_range=((1, 3), (2, 5)),
    capability_overlap=(0.6, 0.8),
    downtime_prob=(FIXED_DOWNTIME_PROB, FIXED_DOWNTIME_PROB),
    due_date_slack=((0, 90), (0, 500)),
    travel=((5, 30), (10, 60)),
    task_duration=((15, 60), (30, 180)),
    future_downtime_count=(3, 5),
)

# Each axis is (axis_name, [(tier_name, overrides), ...]).
# Every tier only overrides its own dimension, so comparing tiers within an
# axis isolates that one variable. The "medium" tier of each axis is
# identical to BASELINE (kept explicit rather than omitted, so the profile
# list is self-documenting and every axis has the same 3 tiers).
AXES = [
    (
        "workload",  # tasks per resource -- no floor anymore, so this can
                      # go as low as ~1:1 or as high as you like
        [
            ("sparse", dict(ratio=(1, 3))),
            ("medium", dict(ratio=(6, 10))),   # == baseline
            ("dense",  dict(ratio=(15, 25))),
        ],
    ),
    (
        "overlap",  # capability_overlap -- how much redundant capacity
                     # resources share; low end still respects MIN_OVERLAP=0.4
        [
            ("sparse", dict(capability_overlap=(0.4, 0.5))),
            ("medium", dict(capability_overlap=(0.6, 0.8))),   # == baseline
            ("dense",  dict(capability_overlap=(0.9, 1.0))),
        ],
    ),
    (
        "disruption",  # future_downtime_count -- how many disruption events
                        # get injected after generation. This is the number of
                        # *attempts*, not guaranteed hits -- with the current
                        # arbitrary (non-targeted) future-downtime logic in
                        # scenario_generator.py, some fraction of attempts won't
                        # land during any task's window, so the effective "hit
                        # rate" needs to be measured empirically per profile.
        [
            ("low",    dict(future_downtime_count=(1, 2))),
            ("medium", dict(future_downtime_count=(3, 5))),    # == baseline
            ("high",   dict(future_downtime_count=(8, 12))),
            ("extreme", dict(future_downtime_count=(15, 20))),
        ],
    ),
    (
        "pressure",  # due_date_slack -- how much breathing room tasks have;
                      # tight slack means less room to reroute around trouble
        [
            ("loose", dict(due_date_slack=((0, 180), (0, 1000)))),
            ("medium", dict(due_date_slack=((0, 90), (0, 500)))),  # == baseline
            ("tight", dict(due_date_slack=((0, 15), (0, 100)))),
        ],
    ),
    (
        "scale",  # n_resources -- fleet size; tests whether findings from
                   # the other axes hold as the problem grows
        [
            ("small", dict(n_resources=(2, 3))),
            ("medium", dict(n_resources=(4, 7))),              # == baseline
            ("large", dict(n_resources=(15, 25))),
        ],
    ),
]


def build_profiles():
    profiles = []
    for axis_name, tiers in AXES:
        for tier_name, overrides in tiers:
            profile = dict(BASELINE)
            profile.update(overrides)
            profile["name"] = f"{axis_name}_{tier_name}"
            profiles.append(profile)
    return profiles


# ─── Grid profiles: workload x pressure ────────────────────────────────
# The AXES above only ever change one dimension at a time relative to
# BASELINE, so they can't reveal interaction effects between two axes.
# GRID_AXES below crosses the tiers of two named axes (e.g. every workload
# tier x every pressure tier) so both dimensions vary together within a
# single profile, holding everything else at BASELINE -- same isolation
# principle as AXES, just applied to a pair instead of a single dimension.
#
# grid_workload_medium_pressure_medium is BASELINE in both overridden
# dimensions (same tier values as workload_medium / pressure_medium /
# BASELINE itself), so it's a useful sanity-check cell, though note it is
# an independently-sampled set of scenarios (different profile index ->
# different seeds), not a byte-for-byte replay of the workload_medium or
# pressure_medium scenarios.
GRID_AXES = [("workload", "pressure")]


def _axis_profile_name(axis_name, tier_name):
    """Name of the pre-existing single-axis profile for a given axis/tier,
    e.g. ("workload", "dense") -> "workload_dense". Every single-axis
    profile from build_profiles() is named f'{axis_name}_{tier_name}'."""
    return f"{axis_name}_{tier_name}"


def build_grid_profiles():
    """
    Cross the tiers of GRID_AXES, but SKIP any cell that's equivalent to a
    profile we already have, so we never regenerate scenarios we already
    generated.

    A "medium" tier is defined (see AXES above) to equal BASELINE for that
    one dimension. So a grid cell with at least one axis at "medium" only
    differs from BASELINE in (at most) the *other* axis -- which is
    exactly what a single-axis profile already covers:
        - both axes "medium"           -> identical to BASELINE itself
                                           (workload_medium already IS a
                                           baseline-parameter profile)
        - axis A "medium", B not       -> identical to axis B's existing
                                           single-axis profile
                                           (e.g. workload_medium x
                                           pressure_tight == pressure_tight)
    Only cells where BOTH axes are off "medium" are genuinely new
    combinations, so only those get generated here.

    Returns (grid_profiles, aliases):
        grid_profiles: the new profiles to actually generate scenarios for
        aliases: {conceptual_grid_cell_name: existing_profile_name} for
            every cell that was skipped, so downstream analysis/combining
            scripts can pull that cell's data from the existing axis
            folder instead of expecting a grid_* folder that was never
            (and doesn't need to be) generated.
    """
    axes_by_name = dict(AXES)
    grid_profiles = []
    aliases = {}
    for axis_a_name, axis_b_name in GRID_AXES:
        tiers_a = axes_by_name[axis_a_name]
        tiers_b = axes_by_name[axis_b_name]
        for tier_a_name, overrides_a in tiers_a:
            for tier_b_name, overrides_b in tiers_b:
                cell_name = f"grid_{axis_a_name}_{tier_a_name}_{axis_b_name}_{tier_b_name}"

                if tier_a_name == "medium" and tier_b_name == "medium":
                    aliases[cell_name] = _axis_profile_name(axis_a_name, "medium")
                    continue
                if tier_a_name == "medium":
                    aliases[cell_name] = _axis_profile_name(axis_b_name, tier_b_name)
                    continue
                if tier_b_name == "medium":
                    aliases[cell_name] = _axis_profile_name(axis_a_name, tier_a_name)
                    continue

                profile = dict(BASELINE)
                profile.update(overrides_a)
                profile.update(overrides_b)
                profile["name"] = cell_name
                grid_profiles.append(profile)

    return grid_profiles, aliases


# Grid profiles are appended AFTER the single-axis profiles, so every
# existing profile keeps the same index in PROFILES (and therefore the
# same seeds as before) regardless of this addition. Grid profiles get
# their own fresh indices/seed blocks, so they can never collide with an
# axis profile's seeds even if you run both sets side by side.
AXIS_PROFILES = build_profiles()
GRID_PROFILES, GRID_ALIASES = build_grid_profiles()


# ─── Sweep profiles: fine-grained, exact-value axes for reporting ──────
# AXES above uses 2-4 named tiers per dimension, sampled within a range --
# good for initial exploration, but coarse for a reporting-grade trend
# line, and the swept value itself is jittered (e.g. n_resources=(4,7)),
# so there's no single clean x-axis position per tier.
#
# SWEEP_AXES instead defines each level as a single EXACT value (not a
# range) for the one dimension being swept, so every scenario at a given
# level has that dimension pinned precisely -- e.g. every "sweep_scale_04"
# scenario has n_resources exactly 12, not "somewhere between 10 and 14".
# Every other dimension keeps its normal BASELINE jitter, so scenarios
# within a level are still diverse in everything *except* the swept
# variable. This isolates the trend from level-to-level.
#
# Each entry is (axis_name, [level values], override_fn) where override_fn
# maps one level value to a BASELINE-override dict.
SWEEP_AXES = [
    (
        "scale",  # n_resources, fleet size -- log-ish spacing since
                   # scheduling difficulty rarely scales linearly with size
        [3, 5, 8, 12, 18, 25],
        lambda v: dict(n_resources=(v, v)),
    ),
    (
        "disruption",  # future_downtime_count -- linear spacing, this is
                        # a count, not scale-sensitive
        [1, 3, 5, 8, 12, 16, 20],
        lambda v: dict(future_downtime_count=(v, v)),
    ),
    (
        "workload",  # ratio (tasks per resource) -- exact ratio, n_tasks
                      # is still derived as n_resources * ratio
        [1, 3, 5, 8, 12, 17, 25],
        lambda v: dict(ratio=(v, v)),
    ),
    (
        "pressure",  # due_date_slack, parameterized as a multiplier k on
                      # BASELINE's slack window. Both bounds scale by k
                      # together (mirrors how the tiered AXES pressure
                      # tiers move both bounds together, e.g. loose vs.
                      # tight), rather than always anchoring the low end
                      # at 0. k=1.0 pins due_date_slack_range to exactly
                      # (90, 500) -- the outer edge of BASELINE's own
                      # sampling range on both sides, so it's a clean
                      # deterministic reference point even though BASELINE
                      # itself samples within that range rather than
                      # landing on a fixed value.
        [2.0, 1.5, 1.0, 0.6, 0.35, 0.15, 0.05],
        lambda k: dict(due_date_slack=((round(90 * k), round(90 * k)), (round(500 * k), round(500 * k)))),
    ),
    (
        "overlap",  # capability_overlap -- true 0-1 range including both
                     # extremes. This is the one axis that deliberately
                     # relaxes the file's hard MIN_OVERLAP=0.4 floor (see
                     # _relax_overlap_floor in build_scenario_params) --
                     # every other profile in this file, sweep or
                     # otherwise, still gets that floor enforced.
        [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        lambda v: dict(capability_overlap=(v, v), _relax_overlap_floor=True),
    ),
]


def build_sweep_profiles():
    """
    One profile per (axis, level) pair, each an exact-value override of
    BASELINE on a single dimension. Profile names are index-based
    (sweep_{axis}_{01, 02, ...}) in level order, so they sort correctly
    and match the order levels are defined in SWEEP_AXES above. The exact
    swept value is stashed on the profile dict (as a leading-underscore
    key so build_scenario_params, which only reads specific named keys,
    ignores it) and surfaced in the manifest for easy plotting.
    """
    profiles = []
    for axis_name, levels, override_fn in SWEEP_AXES:
        for level_index, level_value in enumerate(levels, start=1):
            profile = dict(BASELINE)
            profile.update(override_fn(level_value))
            profile["name"] = f"sweep_{axis_name}_{level_index:02d}"
            profile["_sweep_axis"] = axis_name
            profile["_sweep_level_index"] = level_index
            profile["_sweep_level_value"] = level_value
            profiles.append(profile)
    return profiles


SWEEP_PROFILES = build_sweep_profiles()

PROFILES = AXIS_PROFILES + GRID_PROFILES + SWEEP_PROFILES
PROFILES_BY_NAME = {p["name"]: p for p in PROFILES}
GRID_PROFILE_NAMES = [p["name"] for p in GRID_PROFILES]
SWEEP_PROFILE_NAMES = [p["name"] for p in SWEEP_PROFILES]


def _sample_pair(bounds):
    """bounds is ((lo_a, hi_a), (lo_b, hi_b)) -> sample a range (a, b) with a<=b."""
    lo = random.randint(*bounds[0])
    hi = random.randint(*bounds[1])
    if hi < lo:
        lo, hi = hi, lo
    return (lo, hi)


def build_scenario_params(profile: dict, rng_seed: int, horizon: int) -> dict:
    """Draw one concrete parameter set from a profile, enforcing the hard
    constraints on capability overlap (>= MIN_OVERLAP) and downtime_prob
    (always FIXED_DOWNTIME_PROB) regardless of jitter or profile overrides
    -- UNLESS the profile explicitly opts out via _relax_overlap_floor
    (only sweep_overlap_* profiles do this; see SWEEP_AXES). That's a
    deliberate, narrowly-scoped exception for the one study that's
    specifically about testing overlap below MIN_OVERLAP -- every other
    profile (AXES, GRID_AXES, and the other SWEEP_AXES) is completely
    unaffected and still gets the >= 0.4 floor.
    `horizon` is fixed and shared across every scenario in the set."""
    random.seed(rng_seed)

    n_resources = random.randint(*profile["n_resources"])
    ratio = random.uniform(*profile["ratio"])
    n_tasks = max(1, int(math.ceil(n_resources * ratio)))

    n_locations = random.randint(*profile["n_locations"])

    caps_range = _sample_pair(profile["caps_range"])
    caps_range = (max(1, caps_range[0]), max(caps_range[0] + 1, caps_range[1]))

    capability_overlap = round(random.uniform(*profile["capability_overlap"]), 2)
    if not profile.get("_relax_overlap_floor", False):
        capability_overlap = max(capability_overlap, MIN_OVERLAP)  # enforce hard constraint
    capability_overlap = max(capability_overlap, 0.0)
    capability_overlap = min(capability_overlap, 1.0)

    # Hard override: initial resource downtimes are irrelevant to this study,
    # so downtime_prob is always forced to 0 regardless of what a profile
    # requests. Only future_downtime_count (below) should vary disruption.
    downtime_prob = FIXED_DOWNTIME_PROB

    due_date_slack = _sample_pair(profile["due_date_slack"])
    travel = _sample_pair(profile["travel"])
    task_duration = _sample_pair(profile["task_duration"])

    future_downtime_count = random.randint(*profile["future_downtime_count"])

    return dict(
        n_resources=n_resources,
        n_tasks=n_tasks,
        n_locations=n_locations,
        caps_range=caps_range,
        task_duration_range=task_duration,
        due_date_slack_range=due_date_slack,
        horizon=horizon,
        downtime_prob=downtime_prob,
        capability_overlap=capability_overlap,
        travel_time_range=travel,
        future_downtime_count=future_downtime_count,
        seed=rng_seed,
    )


def sanitize_name(name: str) -> str:
    """Filesystem-safe folder name (profiles already use underscores, but stay safe)."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def main():
    ap = argparse.ArgumentParser(description="Generate scenarios per profile, one folder per profile.")
    ap.add_argument("--per-profile", type=int, default=100,
                     help="Number of scenarios to generate for EACH selected profile")
    ap.add_argument("--profile", type=str, default=None, action="append",
                     help="Name of a profile to generate (e.g. workload_dense). "
                          "Repeatable to select several. Omit to generate ALL profiles. "
                          "Use --list-profiles to see valid names.")
    ap.add_argument("--profile-group", type=str, default=None,
                     choices=["axes", "grid", "sweep"],
                     help="Shortcut to select a whole group of profiles at once: "
                          "'axes' = all single-axis sweep profiles (the original "
                          "behavior), 'grid' = all workload x pressure grid cells, "
                          "'sweep' = all fine-grained exact-value reporting sweeps. "
                          "Combines with --profile if both are given.")
    ap.add_argument("--list-profiles", action="store_true",
                     help="Print available profile names and exit")
    ap.add_argument("--output-dir", type=str, default="./slack_scenarios",
                     help="Directory that will contain one subfolder per profile")
    ap.add_argument("--base-seed", type=int, default=0,
                     help="Base seed; each scenario gets a unique seed derived from this for reproducibility")
    ap.add_argument("--manifest", type=str, default="manifest.json",
                     help="Filename (inside output-dir and inside each profile folder) for the summary manifest")
    ap.add_argument("--horizon", type=int, default=1440,
                     help="Planning horizon (minutes) shared by every scenario in the set")
    args = ap.parse_args()

    if args.list_profiles:
        print("Axis sweep profiles:")
        for p in AXIS_PROFILES:
            print(f"  {p['name']}")
        print("Grid profiles (workload x pressure) -- newly generated:")
        for p in GRID_PROFILES:
            print(f"  {p['name']}")
        print("Grid profiles (workload x pressure) -- skipped, already covered by:")
        for cell_name, alias in GRID_ALIASES.items():
            print(f"  {cell_name}  ->  {alias}")
        print("Sweep profiles (fine-grained, exact-value):")
        for p in SWEEP_PROFILES:
            print(f"  {p['name']}  ({p['_sweep_axis']}={p['_sweep_level_value']})")
        return

    selected_names = list(args.profile) if args.profile else []
    if args.profile_group == "axes":
        selected_names += [p["name"] for p in AXIS_PROFILES]
    elif args.profile_group == "grid":
        selected_names += GRID_PROFILE_NAMES
    elif args.profile_group == "sweep":
        selected_names += SWEEP_PROFILE_NAMES

    if selected_names:
        # de-dupe while preserving order, in case --profile and
        # --profile-group overlap
        seen = set()
        selected_names = [n for n in selected_names if not (n in seen or seen.add(n))]
        unknown = [name for name in selected_names if name not in PROFILES_BY_NAME]
        if unknown:
            valid = ", ".join(PROFILES_BY_NAME)
            raise SystemExit(f"Unknown profile(s): {unknown}. Valid options: {valid}")
        selected_profiles = [PROFILES_BY_NAME[name] for name in selected_names]
    else:
        selected_profiles = PROFILES

    # Profile order/index is always taken from the master PROFILES list (not
    # the possibly-reordered selection) so seeds stay stable regardless of
    # which subset you run or the order you pass --profile in.
    profile_index_by_name = {p["name"]: i for i, p in enumerate(PROFILES)}

    os.makedirs(args.output_dir, exist_ok=True)

    new_entries_by_profile = {}

    for profile in selected_profiles:
        profile_index = profile_index_by_name[profile["name"]]
        profile_dir_name = sanitize_name(profile["name"])
        profile_dir = os.path.join(args.output_dir, profile_dir_name)
        os.makedirs(profile_dir, exist_ok=True)

        profile_manifest = []
        for k in range(args.per_profile):
            # Seeds are derived deterministically from (profile_index, k) so
            # re-running with the same --base-seed always reproduces the same
            # set, and different profiles never collide on seed even if
            # --per-profile changes or you run a single profile in isolation.
            # (Python's built-in hash() is randomized per-process, so it is
            # deliberately NOT used here.)
            seed = args.base_seed + profile_index * 100_000 + k
            params = build_scenario_params(profile, seed, args.horizon)

            # Hard-constraint sanity checks (fail loudly rather than silently
            # emit a scenario that violates the spec). Skipped for profiles
            # that explicitly opt out of the overlap floor (sweep_overlap_*).
            if not profile.get("_relax_overlap_floor", False):
                assert params["capability_overlap"] >= MIN_OVERLAP - 1e-9, \
                    f"overlap violated: {params}"
            assert params["downtime_prob"] == FIXED_DOWNTIME_PROB, \
                f"downtime_prob override violated: {params}"

            request, travel_matrix, future_downtimes = generate_scenario(**params)

            scenario_name = f"scenario_{k:03d}"
            scenario_dir = os.path.join(profile_dir, scenario_name)
            req_path, tm_path, fd_path = write_scenario_files(
                scenario_dir, request, travel_matrix, future_downtimes
            )

            entry = dict(
                scenario=scenario_name,
                profile=profile["name"],
                seed=seed,
                n_resources=params["n_resources"],
                n_tasks=params["n_tasks"],
                ratio=round(params["n_tasks"] / params["n_resources"], 2),
                capability_overlap=params["capability_overlap"],
                n_locations=params["n_locations"],
                horizon=params["horizon"],
                downtime_prob=params["downtime_prob"],
                future_downtime_count_requested=params["future_downtime_count"],
                future_downtime_count_actual=len(future_downtimes),
                due_date_slack_range=params["due_date_slack_range"],
                # Present only for sweep_* profiles -- the exact value this
                # scenario's swept dimension was pinned to, so downstream
                # analysis can plot against it directly without having to
                # re-derive it from the profile name.
                sweep_axis=profile.get("_sweep_axis"),
                sweep_level_index=profile.get("_sweep_level_index"),
                sweep_level_value=profile.get("_sweep_level_value"),
                paths=dict(request=req_path, travel_matrix=tm_path, future_downtimes=fd_path),
            )
            profile_manifest.append(entry)

        profile_manifest_path = os.path.join(profile_dir, args.manifest)
        with open(profile_manifest_path, "w") as f:
            json.dump(profile_manifest, f, indent=2)

        new_entries_by_profile[profile["name"]] = profile_manifest

    # Merge into the combined manifest rather than overwriting it, so running
    # a single profile doesn't erase results already generated for others.
    combined_manifest_path = os.path.join(args.output_dir, args.manifest)
    existing_entries = []
    if os.path.exists(combined_manifest_path):
        with open(combined_manifest_path) as f:
            existing_entries = json.load(f)

    kept_entries = [
        e for e in existing_entries if e.get("profile") not in new_entries_by_profile
    ]
    combined_manifest = kept_entries + [
        e for entries in new_entries_by_profile.values() for e in entries
    ]

    with open(combined_manifest_path, "w") as f:
        json.dump(combined_manifest, f, indent=2)

    total_new = sum(len(v) for v in new_entries_by_profile.values())
    print(f"✓ Generated {total_new} scenarios across {len(selected_profiles)} profile(s) in {args.output_dir}")
    print(f"✓ Combined manifest ({len(combined_manifest)} total scenarios): {combined_manifest_path}")
    print(f"✓ Shared horizon: {args.horizon} min")
    print()
    print(f"Profiles generated ({args.per_profile} scenarios each):")
    for profile in selected_profiles:
        print(f"  {args.output_dir}/{sanitize_name(profile['name'])}/")


if __name__ == "__main__":
    main()