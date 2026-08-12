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


PROFILES = build_profiles()
PROFILES_BY_NAME = {p["name"]: p for p in PROFILES}


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
    (always FIXED_DOWNTIME_PROB) regardless of jitter or profile overrides.
    `horizon` is fixed and shared across every scenario in the set."""
    random.seed(rng_seed)

    n_resources = random.randint(*profile["n_resources"])
    ratio = random.uniform(*profile["ratio"])
    n_tasks = max(1, int(math.ceil(n_resources * ratio)))

    n_locations = random.randint(*profile["n_locations"])

    caps_range = _sample_pair(profile["caps_range"])
    caps_range = (max(1, caps_range[0]), max(caps_range[0] + 1, caps_range[1]))

    capability_overlap = round(random.uniform(*profile["capability_overlap"]), 2)
    capability_overlap = max(capability_overlap, MIN_OVERLAP)  # enforce hard constraint
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
        for name in PROFILES_BY_NAME:
            print(name)
        return

    if args.profile:
        unknown = [name for name in args.profile if name not in PROFILES_BY_NAME]
        if unknown:
            valid = ", ".join(PROFILES_BY_NAME)
            raise SystemExit(f"Unknown profile(s): {unknown}. Valid options: {valid}")
        selected_profiles = [PROFILES_BY_NAME[name] for name in args.profile]
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
            # emit a scenario that violates the spec).
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