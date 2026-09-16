"""
Run the CP-SAT optimizer (tds_slack/optimizer_scheduler.py) over every
scenario in a profile folder, generating ONLY the initial schedule (no
simulation/disruption loop), and report how many tasks it leaves unscheduled
-- directly comparable to the "total_initial_unscheduled" column already
reported by the greedy metrics (save_flexibility/full_flex/makespan) via
run_comparison.py.

Must run in the pythonSTN conda env (where ortools is installed):
    /Users/erubinst/anaconda3/envs/pythonSTN/bin/python3 -m tds_slack.simulator.run_optimizer_initial --profile baseline
"""
import argparse
import time
from pathlib import Path

from tds_slack.executer import upload_request
from tds_slack.optimizer_scheduler import build_and_solve, _real_tasks
from ortools.sat.python import cp_model

SCENARIOS_ROOT = Path(__file__).resolve().parent / "generated_scenarios"


def run_one(scenario_dir, time_limit_seconds):
    import json
    with open(scenario_dir / "request.json") as f:
        request = json.load(f)
    with open(scenario_dir / "travel_matrix.json") as f:
        travel_matrix = json.load(f)

    tds = upload_request(request, travel_matrix, epoch_date=None)
    total = len(_real_tasks(tds))

    start = time.perf_counter()
    status, resource_orders, presence = build_and_solve(tds, time_limit_seconds=time_limit_seconds)
    elapsed = time.perf_counter() - start

    scheduled = sum(presence.values())
    return {
        "total": total,
        "scheduled": scheduled,
        "unscheduled": total - scheduled,
        "status": cp_model.CpSolver().StatusName(status),
        "solve_time": elapsed,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="Profile folder name under generated_scenarios/")
    ap.add_argument("--time-limit", type=float, default=60.0, help="CP-SAT time limit per scenario (seconds)")
    args = ap.parse_args()

    profile_dir = SCENARIOS_ROOT / args.profile
    scenario_dirs = sorted(p for p in profile_dir.iterdir() if p.is_dir() and p.name.startswith("scenario_"))

    log_path = SCENARIOS_ROOT / f"run_optimizer_initial_{args.profile}.log"
    total_tasks = 0
    total_unscheduled = 0
    non_optimal = []

    with open(log_path, "a") as log_file:
        def log(msg):
            line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
            print(line, flush=True)
            log_file.write(line + "\n")
            log_file.flush()

        log(f"Starting optimizer initial-schedule run on {len(scenario_dirs)} scenarios in '{args.profile}' (time_limit={args.time_limit}s/scenario)")
        for i, scenario_dir in enumerate(scenario_dirs):
            result = run_one(scenario_dir, args.time_limit)
            total_tasks += result["total"]
            total_unscheduled += result["unscheduled"]
            if result["status"] != "OPTIMAL":
                non_optimal.append((scenario_dir.name, result["status"]))
            if (i + 1) % 25 == 0 or (i + 1) == len(scenario_dirs):
                log(f"({i + 1}/{len(scenario_dirs)}) running total unscheduled={total_unscheduled} "
                    f"of {total_tasks} tasks so far (last scenario: {scenario_dir.name}, "
                    f"{result['status']}, {result['solve_time']:.2f}s)")

        log(f"Done. Total tasks={total_tasks}, total unscheduled={total_unscheduled}")
        if non_optimal:
            log(f"{len(non_optimal)} scenario(s) did not reach OPTIMAL (hit time limit or other): {non_optimal}")
        else:
            log("Every scenario solved to proven OPTIMAL.")


if __name__ == "__main__":
    main()
