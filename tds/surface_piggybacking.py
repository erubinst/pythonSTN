"""
Piggybacking Detector for Caregiver Schedules
==============================================
Piggybacking: a non-routine task completed on the same trip as a CG routine
task, where the detour required to include the non-routine task is within an
acceptable threshold.

Definitions
-----------
- CG resources only (resource_type == 'cg')
- Trip: a contiguous sequence of tasks where the CG leaves their base location
  (from the header task's location), does some set of tasks, and returns to
  base. Bounded by header/footer and downtime tasks.
- Routine task (R): cg_routine == True
- Non-routine task (NR): cg_routine == False, not travel, not header/footer,
  not downtime
- Base location (B): location of the CG's header task

Detour calculation (uses travel matrix)
----------------------------------------
For each (NR, R) pair on the same trip, the detour is the minimum of:
  - Outbound detour: travel(B→NR) + travel(NR→R) − travel(B→R)
    (how much longer is it to visit NR on the way TO the routine task)
  - Inbound detour: travel(R→NR) + travel(NR→B) − travel(R→B)
    (how much longer is it to visit NR on the way HOME from the routine task)

If min(outbound_detour, inbound_detour) <= threshold → piggybacked.

We take the best (lowest detour) across all routine tasks on the trip.
"""

import json
import os
import warnings

import pandas as pd

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DETOUR_THRESHOLD_MINUTES = 20  # max additional travel time to count as piggybacking


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
def load_schedule(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["display_start"] = pd.to_datetime(df["display_start"], utc=True)
    df["display_end"] = pd.to_datetime(df["display_end"], utc=True)
    return df


def load_travel_matrix(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def travel(matrix: dict, origin: str, destination: str) -> float:
    """Look up travel time between two locations. Returns inf if unknown."""
    try:
        return float(matrix[origin][destination])
    except KeyError:
        return float("inf")


# ---------------------------------------------------------------------------
# Task classification helpers
# ---------------------------------------------------------------------------
def is_header(row) -> bool:
    return row["task_name"] == f"{row['resource']}_header"


def is_footer(row) -> bool:
    return row["task_name"] == f"{row['resource']}_footer"


def is_downtime(row) -> bool:
    # True downtime tasks have capability matching {resource}_presence AND
    # contain _downtime_ in the name. Pickup/dropoff tasks always have
    # capability 'transport' so they can never match here.
    # TODO update to work with new transport capability naming convention
    return (
        "_downtime_" in row["task_name"]
        and row["capability"] != "transport"
        and row["capability"] != "travel"
    )


def is_travel(row) -> bool:
    return row["capability"] == "travel"


def is_overhead(row) -> bool:
    # pickup/dropoff tasks are always transport candidates, never overhead
    if row["task_name"].startswith("pickup_from_") or row["task_name"].startswith("dropoff_at_"):
        return False
    return is_header(row) or is_footer(row) or is_downtime(row) or is_travel(row)


# ---------------------------------------------------------------------------
# Trip segmentation
# ---------------------------------------------------------------------------
def get_base_location(cg_df: pd.DataFrame, resource: str) -> str | None:
    header_rows = cg_df[cg_df["task_name"] == f"{resource}_header"]
    if header_rows.empty:
        return None
    return header_rows.iloc[0]["location"]


def segment_trips(cg_df: pd.DataFrame, base_location: str) -> list[list[int]]:
    """
    Split a CG's schedule into trips. A trip is the set of task indices
    between leaving base (after a header/downtime) and returning to base
    (before the next downtime/footer). Travel legs are included so we can
    inspect the trip structure, but they are excluded from the
    routine/non-routine classification later.

    Returns a list of trips; each trip is a list of positional indices
    into cg_df (sorted by display_start).
    """
    df = cg_df.sort_values("display_start").reset_index(drop=True)

    trips = []
    current_trip: list[int] = []
    in_trip = False

    for i, row in df.iterrows():
        if is_header(row) or is_footer(row):
            if in_trip and current_trip:
                trips.append(current_trip)
                current_trip = []
                in_trip = False
            continue

        if is_downtime(row):
            if in_trip and current_trip:
                trips.append(current_trip)
                current_trip = []
            in_trip = False
            continue

        if is_travel(row):
            prev_loc = _prev_location(df, i, base_location)
            if not in_trip and prev_loc == base_location:
                in_trip = True
                current_trip = []
            if in_trip:
                current_trip.append(i)
            next_loc = _next_location(df, i, base_location)
            if in_trip and next_loc == base_location:
                trips.append(current_trip)
                current_trip = []
                in_trip = False
            continue

        # Regular substantive task — only include if already in a trip.
        # Tasks appearing outside a travel-bounded trip (e.g. at base between
        # downtimes) are intentionally excluded.
        if in_trip:
            current_trip.append(i)

    if in_trip and current_trip:
        trips.append(current_trip)

    return trips


def _prev_location(df: pd.DataFrame, i: int, default: str) -> str:
    for j in range(i - 1, -1, -1):
        loc = df.at[j, "location"]
        if not pd.isna(loc) and loc != "":
            return loc
    return default


def _next_location(df: pd.DataFrame, i: int, default: str) -> str:
    for j in range(i + 1, len(df)):
        loc = df.at[j, "location"]
        if not pd.isna(loc) and loc != "":
            return loc
    return default


# ---------------------------------------------------------------------------
# Transport pair grouping
# ---------------------------------------------------------------------------

def get_person_suffix(task_name: str) -> str | None:
    """
    Extract the person suffix from a pickup or dropoff task name.
    pickup_from_lionvintagevisittue_lion  →  lion
    dropoff_at_jeanvintagevisitmon_jean   →  jean
    Returns the last underscore-delimited token, or None if not a transport task.
    """
    if not (task_name.startswith("pickup_from_") or task_name.startswith("dropoff_at_")):
        return None
    return task_name.rsplit("_", 1)[-1]


def group_transport_units(non_routine_rows: pd.DataFrame) -> list[dict]:
    """
    Group non-routine tasks into transport pairs or solo tasks.

    Pairing rule: for each pickup_from_X_person, scan forward and take the
    next dropoff_at_Y_person where the person suffix matches. Claimed dropoffs
    are skipped. Everything else is a solo unit.
    """
    rows = non_routine_rows.sort_values("display_start")
    row_list = list(rows.iterrows())  # list of (idx, row) in order

    claimed = set()
    units = []

    for i, (idx, row) in enumerate(row_list):
        task = row["task_name"]

        if task.startswith("pickup_from_"):
            person = get_person_suffix(task)
            # Scan forward for next unclaimed dropoff with same person suffix
            partner = None
            for j in range(i + 1, len(row_list)):
                didx, drow = row_list[j]
                if (
                    didx not in claimed
                    and drow["task_name"].startswith("dropoff_at_")
                    and get_person_suffix(drow["task_name"]) == person
                ):
                    partner = (didx, drow)
                    break

            if partner is not None:
                claimed.add(partner[0])
                units.append({
                    "type": "pair",
                    "rows": [row, partner[1]],
                    "pickup_loc": row["location"],
                    "dropoff_loc": partner[1]["location"],
                })
            else:
                units.append({"type": "solo", "rows": [row]})

        elif task.startswith("dropoff_at_"):
            if idx not in claimed:
                units.append({"type": "solo", "rows": [row]})

        else:
            units.append({"type": "solo", "rows": [row]})

    return units


# ---------------------------------------------------------------------------
# Detour calculation
# ---------------------------------------------------------------------------
def compute_detour_solo(
    base: str,
    nr_loc: str,
    r_loc: str,
    matrix: dict,
    task_before_routine: bool,
    prev_r_loc: str | None,
    next_r_loc: str | None,
) -> tuple[float, str, float]:
    """
    Returns (detour_minutes, direction, direct_leg_minutes).
    direct_leg_minutes is the baseline travel time without the detour,
    used to compute the effective threshold: min(global_threshold, 2 * direct_leg).
    """
    if nr_loc == r_loc:
        return 0.0, "same_location", 0.0

    if task_before_routine:
        from_loc = prev_r_loc or base
        direct = travel(matrix, from_loc, r_loc)
        detour = (
            travel(matrix, from_loc, nr_loc)
            + travel(matrix, nr_loc, r_loc)
            - direct
        )
        return detour, "outbound", direct
    else:
        to_loc = next_r_loc or base
        direct = travel(matrix, r_loc, to_loc)
        detour = (
            travel(matrix, r_loc, nr_loc)
            + travel(matrix, nr_loc, to_loc)
            - direct
        )
        return detour, "inbound", direct


def compute_detour_pair(
    base: str,
    pickup_loc: str,
    dropoff_loc: str,
    r_loc: str,
    matrix: dict,
    pair_before_routine: bool,
    prev_r_loc: str | None,
    next_r_loc: str | None,
) -> tuple[float, str, float]:
    """
    Returns (detour_minutes, direction, direct_leg_minutes).
    direct_leg_minutes is the baseline travel time without the detour,
    used to compute the effective threshold: min(global_threshold, 2 * direct_leg).
    """
    ab = travel(matrix, pickup_loc, dropoff_loc)

    if pair_before_routine:
        from_loc = prev_r_loc or base
        direct = travel(matrix, from_loc, r_loc)
        detour = (
            travel(matrix, from_loc, pickup_loc)
            + ab
            + travel(matrix, dropoff_loc, r_loc)
            - direct
        )
        return detour, "outbound", direct
    else:
        to_loc = next_r_loc or base
        direct = travel(matrix, r_loc, to_loc)
        detour = (
            travel(matrix, r_loc, pickup_loc)
            + ab
            + travel(matrix, dropoff_loc, to_loc)
            - direct
        )
        return detour, "inbound", direct


# ---------------------------------------------------------------------------
# Main piggybacking detection
# ---------------------------------------------------------------------------
def detect_piggybacking(
    df: pd.DataFrame,
    matrix: dict,
    threshold_minutes: float = DETOUR_THRESHOLD_MINUTES,
) -> pd.DataFrame:
    """
    Returns a DataFrame of piggybacked non-routine tasks/pairs.

    Transport pickup+dropoff pairs are treated as one atomic unit and produce
    a single output row. The detour for a pair is the extra travel time for
    driving person from pickup_loc to dropoff_loc, compared to the direct
    route to/from the nearest routine task.

    Output columns:
      resource, trip_id, trip_date,
      piggybacked_task, pickup_location, dropoff_location, task_capability,
      nearest_routine_task, nearest_routine_location,
      detour_minutes, detour_direction,
      all_routine_tasks_on_trip
    """
    results = []
    missing_locations: set[str] = set()

    cg_resources = df[df["resource_type"] == "cg"]["resource"].unique()

    for resource in sorted(cg_resources):
        cg_df = (
            df[df["resource"] == resource]
            .copy()
            .sort_values("display_start")
            .reset_index(drop=True)
        )

        base_location = get_base_location(cg_df, resource)
        if base_location is None:
            print(f"  Warning: no header found for {resource}, skipping.")
            continue

        trips = segment_trips(cg_df, base_location)

        for trip_num, trip_indices in enumerate(trips):
            if not trip_indices:
                continue

            trip_rows = cg_df.loc[trip_indices]

            routine_rows = trip_rows[
                (trip_rows["cg_routine"] == True)
                & (~trip_rows.apply(is_overhead, axis=1))
            ]
            non_routine_rows = trip_rows[
                (trip_rows["cg_routine"] == False)
                & (~trip_rows.apply(is_overhead, axis=1))
            ]

            if routine_rows.empty or non_routine_rows.empty:
                continue

            units = group_transport_units(non_routine_rows)

            for unit in units:
                is_pair = unit["type"] == "pair"

                # Build display label and locations for this unit
                if is_pair:
                    label = unit["rows"][0]["task_name"]  # pickup task name
                    pickup_loc = unit["pickup_loc"]
                    dropoff_loc = unit["dropoff_loc"]
                    capability = "transport_pair"
                    # Warn on missing locations
                    for loc in (pickup_loc, dropoff_loc):
                        if loc and loc not in matrix:
                            missing_locations.add(loc)
                else:
                    row = unit["rows"][0]
                    label = row["task_name"]
                    pickup_loc = row["location"]
                    dropoff_loc = None
                    capability = row["capability"]
                    if pickup_loc and pickup_loc not in matrix:
                        missing_locations.add(pickup_loc)

                if not pickup_loc or pd.isna(pickup_loc):
                    continue

                best_detour = float("inf")
                best_direction = None
                best_r_row = None
                best_effective_threshold = threshold_minutes

                unit_start = unit["rows"][0]["display_start"]
                sorted_routine = routine_rows.sort_values("display_start").reset_index()

                # For each non-routine unit, find the immediately preceding and
                # following routine tasks by schedule order, then evaluate only
                # against the adjacent one (outbound → next routine after unit,
                # inbound → last routine before unit). Take whichever gives a
                # lower detour if the unit falls between two routine tasks.
                before = sorted_routine[sorted_routine["display_start"] > unit_start]
                after_nr = sorted_routine[sorted_routine["display_start"] <= unit_start]

                candidates = []
                # Nearest routine task AFTER the unit (outbound)
                if not before.empty:
                    r_row = before.iloc[0]
                    r_pos = sorted_routine.index[sorted_routine["display_start"] == r_row["display_start"]][0]
                    prev_r_loc = sorted_routine.iloc[r_pos - 1]["location"] if r_pos > 0 else None
                    next_r_loc = sorted_routine.iloc[r_pos + 1]["location"] if r_pos < len(sorted_routine) - 1 else None
                    candidates.append((r_row, True, prev_r_loc, next_r_loc))

                # Nearest routine task BEFORE the unit (inbound)
                if not after_nr.empty:
                    r_row = after_nr.iloc[-1]
                    r_pos = sorted_routine.index[sorted_routine["display_start"] == r_row["display_start"]][0]
                    prev_r_loc = sorted_routine.iloc[r_pos - 1]["location"] if r_pos > 0 else None
                    next_r_loc = sorted_routine.iloc[r_pos + 1]["location"] if r_pos < len(sorted_routine) - 1 else None
                    candidates.append((r_row, False, prev_r_loc, next_r_loc))

                for r_row, before_routine, prev_r_loc, next_r_loc in candidates:
                    r_loc = r_row["location"]
                    if pd.isna(r_loc) or r_loc == "":
                        continue
                    if r_loc not in matrix:
                        missing_locations.add(r_loc)

                    if is_pair:
                        detour, direction, direct_leg = compute_detour_pair(
                            base_location, pickup_loc, dropoff_loc, r_loc, matrix,
                            pair_before_routine=before_routine,
                            prev_r_loc=prev_r_loc,
                            next_r_loc=next_r_loc,
                        )
                    else:
                        detour, direction, direct_leg = compute_detour_solo(
                            base_location, pickup_loc, r_loc, matrix,
                            task_before_routine=before_routine,
                            prev_r_loc=prev_r_loc,
                            next_r_loc=next_r_loc,
                        )

                    effective_threshold = min(threshold_minutes, 2 * direct_leg)

                    if detour < best_detour:
                        best_detour = detour
                        best_direction = direction
                        best_r_row = r_row
                        best_effective_threshold = effective_threshold

                if best_detour <= best_effective_threshold and best_r_row is not None:
                    results.append({
                        "resource": resource,
                        "trip_id": f"{resource}_trip_{trip_num + 1}",
                        "trip_date": trip_rows["display_start"].min().date(),
                        "schedule_order": unit["rows"][0]["display_start"],
                        "piggybacked_task": label,
                        "task_type": "transport_pair" if is_pair else "solo",
                        "pickup_location": pickup_loc,
                        "dropoff_location": dropoff_loc if is_pair else None,
                        "task_capability": capability,
                        "nearest_routine_task": best_r_row["task_name"],
                        "nearest_routine_location": best_r_row["location"],
                        "detour_minutes": round(best_detour, 1),
                        "effective_threshold": round(best_effective_threshold, 1),
                        "detour_direction": best_direction,
                        "all_routine_tasks_on_trip": ", ".join(
                            routine_rows["task_name"].tolist()
                        ),
                    })

    if missing_locations:
        print(
            f"\n  Warning: the following locations were not found in the travel "
            f"matrix and were skipped:\n    {sorted(missing_locations)}"
        )

    result_df = pd.DataFrame(results)
    if not result_df.empty:
        result_df = result_df.sort_values(
            ["resource", "trip_date", "trip_id", "schedule_order"]
        ).drop(columns=["schedule_order"]).reset_index(drop=True)
    return result_df


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def summarize(piggyback_df: pd.DataFrame, threshold: float) -> None:
    if piggyback_df.empty:
        print("No piggybacking found.")
        return

    print(f"\n{'='*70}")
    print(f"PIGGYBACKING SUMMARY  (detour threshold: {threshold} min)")
    print(f"{'='*70}")
    print(f"Total piggybacked instances : {len(piggyback_df)}")
    print(f"  Transport pairs           : {(piggyback_df['task_type'] == 'transport_pair').sum()}")
    print(f"  Solo tasks                : {(piggyback_df['task_type'] == 'solo').sum()}")
    print(f"Caregivers with piggybacking: {piggyback_df['resource'].nunique()}")
    print(f"Trips with piggybacking     : {piggyback_df['trip_id'].nunique()}\n")

    print("By caregiver:")
    print(
        piggyback_df.groupby("resource")
        .agg(
            piggybacked_instances=("piggybacked_task", "count"),
            trips=("trip_id", "nunique"),
        )
        .to_string()
    )

    print(f"\n{'='*70}")
    print("DETAIL")
    print(f"{'='*70}")
    for _, row in piggyback_df.iterrows():
        print(f"\n[{row['resource']}]  Trip: {row['trip_id']}  ({row['trip_date']})")
        if row["task_type"] == "transport_pair":
            print(f"  Piggybacked : {row['piggybacked_task']}  [transport pair]")
            print(f"    Pickup    : {row['pickup_location']}")
            print(f"    Dropoff   : {row['dropoff_location']}")
        else:
            print(f"  Piggybacked : {row['piggybacked_task']}  [solo]")
            print(f"    Location  : {row['pickup_location']}  |  Capability: {row['task_capability']}")
        print(f"  Routine task: {row['nearest_routine_task']}")
        print(f"    Location  : {row['nearest_routine_location']}")
        print(f"    Detour    : {row['detour_minutes']} min  ({row['detour_direction']})")
        print(f"  All routine tasks on trip: {row['all_routine_tasks_on_trip']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python piggyback_detector.py <schedule.csv> <travel_matrix.json> [threshold_minutes]")
        sys.exit(1)

    schedule_path = sys.argv[1]
    matrix_path = sys.argv[2]
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else DETOUR_THRESHOLD_MINUTES

    print(f"Loading schedule   : {schedule_path}")
    print(f"Loading matrix     : {matrix_path}")
    print(f"Detour threshold   : {threshold} min")

    df = load_schedule(schedule_path)
    matrix = load_travel_matrix(matrix_path)

    cg = sorted(df[df["resource_type"] == "cg"]["resource"].unique())
    print(f"CG resources       : {cg}")

    piggyback_df = detect_piggybacking(df, matrix, threshold_minutes=threshold)

    summarize(piggyback_df, threshold)

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.path.basename(schedule_path).replace(".csv", "_piggybacking.csv"),
    )
    piggyback_df.to_csv(out_path, index=False)
    print(f"\nResults saved to: {out_path}")