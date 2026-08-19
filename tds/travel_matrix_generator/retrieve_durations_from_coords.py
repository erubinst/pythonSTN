"""
Driving Distance Matrix Generator
Uses OpenRouteService (free tier: 2,000 req/day, no credit card required)
Sign up for a free API key at: https://openrouteservice.org/dev/#/signup

Usage:
    pip install requests
    python get_distance_matrix.py --api-key YOUR_KEY_HERE
    python get_distance_matrix.py --api-key YOUR_KEY_HERE --input addresses.csv --output distances.json
"""

import csv
import json
import re
import time
import argparse
import requests

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_INPUT  = "/Users/erubinst/Downloads/AddressList.csv"
DEFAULT_OUTPUT = "driving_distances.json"
ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"

# ORS free tier: max 50 locations per matrix request
ORS_MAX_LOCATIONS = 50
# ─────────────────────────────────────────────────────────────────────────────


def dms_to_decimal(dms_str: str) -> float:
    """
    Convert a DMS string like  40°28'19.0"N  or  79°57'33.9"W
    to a signed decimal degree float.
    """
    # Strip smart quotes and extra whitespace
    dms_str = dms_str.strip().replace("\u2019", "'").replace("\u201d", '"').replace("''", '"')

    pattern = r"""(\d+)[°\s]+(\d+)['\s]+([\d.]+)["\s]*([NSEW])"""
    m = re.search(pattern, dms_str, re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot parse DMS string: {dms_str!r}")

    degrees, minutes, seconds, direction = m.groups()
    decimal = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    if direction.upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def load_locations(csv_path: str) -> dict[str, tuple[float, float]]:
    """
    Read CSV with columns  Place, Coordinates
    where Coordinates is  LAT_DMS LONG_DMS  (space-separated, e.g. 40°28'19.0"N 79°57'33.9"W)
    Returns {name: (lon, lat)}  — ORS wants [longitude, latitude]
    """
    locations = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["Place"].strip()
            coords_raw = row["Coordinates"].strip()

            # Split on whitespace between the two DMS values
            # e.g.  '40°28\'19.0"N 79°57\'33.9"W'
            parts = re.split(r'\s+(?=\d)', coords_raw, maxsplit=1)
            if len(parts) != 2:
                print(f"  WARNING: Skipping {name!r} — unexpected coordinate format: {coords_raw!r}")
                continue

            lat = dms_to_decimal(parts[0])
            lon = dms_to_decimal(parts[1])
            locations[name] = (lon, lat)   # ORS order: [lon, lat]

    return locations


def get_matrix(api_key: str, coordinates: list[list[float]]) -> list[list[float]]:
    """
    Call ORS Matrix API for a list of [lon, lat] pairs.
    Returns a 2-D list of durations in seconds.
    """
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "locations": coordinates,
        "metrics": ["duration"],
        "units": "m",
    }
    resp = requests.post(ORS_MATRIX_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["durations"]   # seconds


def build_distance_matrix(
    api_key: str,
    locations: dict[str, tuple[float, float]],
) -> dict[str, dict[str, int]]:
    """
    Build a full N×N driving-time matrix (minutes, rounded).
    Handles chunking if there are more than ORS_MAX_LOCATIONS locations.
    """
    names = list(locations.keys())
    coords = [list(locations[n]) for n in names]
    n = len(names)

    print(f"  {n} locations → {n*n} matrix cells")

    if n > ORS_MAX_LOCATIONS:
        # Split into chunks and stitch results together
        print(f"  Splitting into chunks of {ORS_MAX_LOCATIONS} (ORS limit)…")
        # For simplicity we do row-chunks: query all destinations for each source chunk
        # A more efficient approach would batch NxN in sub-matrices, but this is
        # straightforward and stays within the free tier for typical use cases.
        duration_matrix = [[None] * n for _ in range(n)]

        chunk_size = ORS_MAX_LOCATIONS
        for start in range(0, n, chunk_size):
            chunk_names  = names[start : start + chunk_size]
            chunk_coords = coords[start : start + chunk_size]

            print(f"    Querying rows {start}–{start+len(chunk_names)-1}…")
            # sources = chunk, destinations = all locations
            headers = {
                "Authorization": api_key,
                "Content-Type": "application/json",
            }
            # ORS allows specifying sources/destinations indices within locations list
            all_coords = chunk_coords + coords   # chunk first, then all
            src_indices = list(range(len(chunk_coords)))
            dst_indices = list(range(len(chunk_coords), len(chunk_coords) + n))

            body = {
                "locations": all_coords,
                "sources": src_indices,
                "destinations": dst_indices,
                "metrics": ["duration"],
            }
            resp = requests.post(ORS_MATRIX_URL, headers=headers, json=body, timeout=30)
            resp.raise_for_status()
            rows = resp.json()["durations"]

            for i, row in enumerate(rows):
                for j, val in enumerate(row):
                    duration_matrix[start + i][j] = val

            time.sleep(1)   # be polite to the free tier
    else:
        raw = get_matrix(api_key, coords)
        duration_matrix = raw

    # Convert to named dict with minutes
    result = {}
    for i, src in enumerate(names):
        result[src] = {}
        for j, dst in enumerate(names):
            secs = duration_matrix[i][j]
            result[src][dst] = round(secs / 60) if secs is not None else None

    return result


def main():
    parser = argparse.ArgumentParser(description="Generate driving-time matrix JSON")
    # eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImU2Y2Q5YWJiMWM2ZTQ5MWU4ZTlhNjA1ZTQ4NDYxOTcyIiwiaCI6Im11cm11cjY0In0=
    parser.add_argument("--api-key", required=True, help="OpenRouteService API key")
    parser.add_argument("--input",   default=DEFAULT_INPUT,  help="Input CSV path")
    parser.add_argument("--output",  default=DEFAULT_OUTPUT, help="Output JSON path")
    args = parser.parse_args()

    print(f"Loading locations from {args.input!r}…")
    locations = load_locations(args.input)
    print(f"  Loaded {len(locations)} locations: {', '.join(locations)}")

    print("Calling OpenRouteService Matrix API…")
    matrix = build_distance_matrix(args.api_key, locations)

    print(f"Writing output to {args.output!r}…")
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(matrix, f, indent=4)

    print("Done! ✓")


if __name__ == "__main__":
    main()