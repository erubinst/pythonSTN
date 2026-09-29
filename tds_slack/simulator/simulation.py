from collections import deque
from pathlib import Path
import time

from tds_slack.task_swap import task_swap
from tds_slack.executer import run_scheduler, send_event, regenerate_schedule_for_event, regenerate_schedule_for_event_cp, upload_request
from tds_slack.optimizer_scheduler import generate_initial_schedule_cp
from ortools.sat.python import cp_model
import json
import numpy as np
import pandas as pd

'''
This code will run a simulation of real time execution.  
To do this, there will be a now timepoint that will be updated as the simulation progresses.  
To start, all scheduled tasks will have their start timepoint constrained to be after the now timepoint.  
The now timepoint will be updated every increment time units.  
If a task is ready to start, it will be marked as executing and we remove the constraint on its start timepoint and add a constraint on its end timepoint to be after the now timepoint.  
We will mimic the notification of the start of execution for a task and mark the task as executing assuming earliest possible start time, but in the future we can add a random delay to simulate delays.  
Then, when the now timepoint reaches the end timepoint of a task, we will simulate a notification of the task being successfully completed and mark the task as completed, removing the constraint on its end timepoint.  
For now, all tasks will be assumed to complete at earliest possible time, but in the future we can add a random delay to simulate tasks taking longer than expected.

Could also jump to next event instead of increment for experiment
'''

'''
Steps: 
1. Given some parameters, initialize tds and a schedule
2. Call tds.create_now_tp() to create the now timepoint and constrain all scheduled tasks to be after the now timepoint
3. Update the now timepoint every increment time units
4. In the simulator, we will search for any tasks with earliest start time <= now and status == scheduled, and mark them as executing.  
5. Later we can add a random delay to simulate tasks taking longer than expected or starting late, but for now we will assume they start and complete at earliest possible time.
'''




def find_ready_tasks(tds):
    ready_tasks = []
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue
        # if the task is scheduled and the earliest start time is before the now timepoint, then it is ready to start
        if task.status == 'scheduled' and np.abs(task.start.lb) <= np.abs(tds.now.lb):
            ready_tasks.append(task)
    return ready_tasks



def find_starting_events(tds, event_df):
    # filter out events that have already started (start_time < now timepoint)
    if event_df.empty:
        return pd.DataFrame()
    incoming_events = pd.DataFrame()
    # Anything starting after current now time
    future_events = event_df[event_df['start_time'] >= np.abs(tds.now.lb)]
    for index, row in future_events.iterrows():
        # if it starts anywhere between now and the next jump
        if row['start_time'] <= np.abs(tds.now.lb):
            print(f"Incoming downtime event on {row['resource']} at time {row['start_time']}.")
            incoming_events = pd.concat([incoming_events, row.to_frame().T], ignore_index=True)
    return incoming_events


def get_next_event_time(tds, event_df):
    next_event_time = np.inf
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue
        if task.status == 'scheduled':
            next_event_time = min(next_event_time, np.abs(task.start.lb))
        elif task.status == 'executing':
            next_event_time = min(next_event_time, np.abs(task.end.lb))
    # also check for any future events in the event_df
    # assume event df is sorted by time, so we can just check the first event that is after the now timepoint
    if not event_df.empty:
        for event_time in event_df['start_time']:
            if event_time > np.abs(tds.now.lb):
                next_event_time = min(next_event_time, event_time)
                break
    return next_event_time


def get_next_event_type(tds, event_df):
    next_task_event_time = np.inf
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue
        if task.status == 'scheduled':
            next_task_event_time = min(next_task_event_time, np.abs(task.start.lb))
        elif task.status == 'executing':
            next_task_event_time = min(next_task_event_time, np.abs(task.end.lb))

    next_disruption_event_time = np.inf
    if not event_df.empty:
        for event_time in event_df['start_time']:
            if event_time > np.abs(tds.now.lb):
                next_disruption_event_time = event_time
                break

    if next_disruption_event_time <= next_task_event_time:
        return 'disruption event'

    return 'task event'


def get_next_events(tds, event_df):
    next_event_time = np.inf

    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue

        if task.status == 'scheduled':
            task_event_time = np.abs(task.start.lb)
        elif task.status == 'executing':
            task_event_time = np.abs(task.end.lb)
        else:
            continue

        if task_event_time < next_event_time:
            next_event_time = task_event_time
    if not event_df.empty:
        for event_time in event_df['start_time']:
            if event_time > np.abs(tds.now.lb):
                next_event_time = min(next_event_time, event_time)

    if next_event_time == np.inf:
        return next_event_time, []

    next_events = []

    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue

        if task.status == 'scheduled' and np.abs(task.start.lb) == next_event_time:
            next_events.append({'time': next_event_time, 'type': 'task event'})
        elif task.status == 'executing' and np.abs(task.end.lb) == next_event_time:
            next_events.append({'time': next_event_time, 'type': 'task event'})

    if not event_df.empty:
        matching_disruption_events = event_df[event_df['start_time'] == next_event_time]
        for _index, _row in matching_disruption_events.iterrows():
            next_events.append({'time': next_event_time, 'type': 'disruption event'})

    return next_event_time, next_events


def find_completed_tasks(tds):
    completed_tasks = []
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue
        if task.status == 'executing' and np.abs(task.end.lb) <= np.abs(tds.now.lb):
            completed_tasks.append(task)
    return completed_tasks


def simulate_task_starts(tds, ready_tasks):
    for task in ready_tasks:
        print(f"Task {task.name} is ready to start at time {np.abs(tds.now.lb)}. Marking as executing.")
        task.begin_execution()



def simulate_task_completions(tds, completed_tasks):
    for task in completed_tasks:
        print(f"Task {task.name} is completed at time {np.abs(tds.now.lb)}. Marking as completed.")
        task.complete_execution()


def initialize_events(event_path):
    events_df = pd.read_json(event_path)
    # print events
    print(events_df)
    return events_df


def initialize_tds(request_path, travel_path, initial_heuristic, minimize, cp_time_limit=180):
    with open(request_path, 'r') as f:
        request_dict = json.load(f)
    with open(travel_path, 'r') as f:
        travel_matrix = json.load(f)
    if initial_heuristic == "cp_optimal":
        tds = upload_request(request_dict, travel_matrix, epoch_date=None)
        tds = generate_initial_schedule_cp(tds, time_limit_seconds=cp_time_limit)
    else:
        tds = run_scheduler(request_dict, travel_matrix, objective_metric=initial_heuristic, minimize=minimize)
    # print out the est schedule for debugging
    print("Initial schedule:")
    for resources in tds.resources.values():
        print(f"Resource {resources.name}:")
        for task in resources.timeline.tasks:
            if task.name.endswith('_header') or task.name.endswith('_footer'):
                continue
            print(f"  Task {task.name}: start={np.abs(task.start.lb)}, end={np.abs(task.end.lb)}, status={task.status}")
            print(f"    Flexibility for resource {resources.name}: {task.flexibility.get(resources.name, 0)}")
    return tds


def all_tasks_completed(tds):
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue
        # if status is not completed, unscheduled, or aborted (given up on for good)
        if task.status not in ['completed', 'unscheduled', 'aborted']:
            return False
    return True




def _check_flexibility(tds, label, enabled):
    """Compare the saved flexibility dicts against a full live recompute and print any drift."""
    if not enabled:
        return
    mismatches = tds.verify_saved_flexibility()
    if mismatches:
        print(f"[flexibility check] {label}: {len(mismatches)} MISMATCH(ES) between saved and live values:")
        for task_name, resource_name, saved_value, live_value in mismatches:
            print(f"    {task_name}'s entry for {resource_name}: saved={saved_value} live={live_value} (diff={live_value - saved_value})")
    else:
        print(f"[flexibility check] {label}: OK (saved == live)")


def _run_simulation(request_path, travel_path, event_path, initial_heuristic="flexibility", task_swap_heuristic="flexibility", minimize=False, initial_minimize=None, swap_minimize=None, max_moves=10, verify_flexibility=False, reschedule_mode="task_swap", regen_metric="makespan", cp_time_limit=180, on_initial_schedule=None):
    all_dropped_tasks = deque()

    initial_minimize = initial_minimize if initial_minimize is not None else minimize
    swap_minimize = swap_minimize if swap_minimize is not None else minimize
    verify_initial = verify_flexibility and initial_heuristic in ("save_flexibility", "full_flex", "full_flex_swap", "full_flex_concave")
    verify_swap = verify_flexibility and task_swap_heuristic in ("save_flexibility", "full_flex", "full_flex_swap", "full_flex_concave")

    # --- Timing: schedule generation ---
    print(f"Generating initial schedule from request {request_path} using heuristic '{initial_heuristic}' (minimize={initial_minimize}) with {max_moves} max moves...")
    schedule_gen_start = time.perf_counter()
    tds = initialize_tds(request_path, travel_path, initial_heuristic, initial_minimize, cp_time_limit=cp_time_limit)
    schedule_gen_time = time.perf_counter() - schedule_gen_start

    _check_flexibility(tds, "after initial schedule generation", verify_initial)

    # Tasks that never made it into the initial schedule at all. Tracked
    # separately from all_dropped_tasks (rather than merged in) so the two
    # sources: initial-generation shortfall vs. execution-rescheduling failure
    initial_unscheduled_tasks = []
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
            continue
        if task.status == 'unscheduled':
            print(f"Task {task.name} never made it into the initial schedule.")
            task.status = 'aborted'
            initial_unscheduled_tasks.append(task)

    if on_initial_schedule is not None:
        on_initial_schedule(tds)

    # Initial generation may have set this for initial_heuristic (see
    # run_scheduler); re-set it here for whichever metric actually drives
    # runtime rescheduling, since the two can differ.
    runtime_metric = regen_metric if reschedule_mode == "full_regen" else task_swap_heuristic
    tds.include_unscheduled_in_flexibility = (runtime_metric in ("full_flex", "full_flex_swap", "full_flex_concave", "full_flex_matching", "slots"))
    tds.include_slack_in_flexibility = (runtime_metric != "slots")
    tds.include_swapsols = (runtime_metric == "full_flex_swap")
    tds.include_slot_concavity = (runtime_metric == "full_flex_concave")
    tds.include_matching_redundancy = (runtime_metric == "full_flex_matching")

    tds.create_now_tp()
    event_df = initialize_events(event_path)
    print(f"Starting simulation with now timepoint at {np.abs(tds.now.lb)}.")

    # --- Timing: rescheduling (task_swap calls) ---
    reschedule_time_total = 0.0
    reschedule_call_count = 0
    reschedule_call_durations = []

    while True:
        # if all tasks are completed, break
        all_removed_tasks = deque()
        all_events = get_next_events(tds, event_df)
        new_time = all_events[0]

        # if new time is the same as old time we missed something, break the simulation and print the file path of the reqest file
        if new_time == np.abs(tds.now.lb):
            print(f"Error: next event time {new_time} is the same as current now time {np.abs(tds.now.lb)}. Breaking simulation.")
            print(f"Request file path: {request_path}")
            break

        print("-------------------------------")
        print(f"Updating now timepoint from {np.abs(tds.now.lb)} to {new_time}.")
        tds.update_now_tp(new_time)
        if all_tasks_completed(tds):
            print(f"All tasks completed at time {np.abs(tds.now.lb)}. Ending simulation.")
            break

        # if all_events contains a disruption type event, we first must process the disruption event
        if any(event['type'] == 'disruption event' for event in all_events[1]):
            tds.refresh_saved_flexibility_after_now_update()
            _check_flexibility(tds, f"after refreshing flexibility following now advance to {new_time}", verify_swap)
            starting_events = find_starting_events(tds, event_df)

            if reschedule_mode == "full_regen":
                # Once per individual event: wipe + rebuild the entire remaining
                # schedule from scratch, rather than task_swap's targeted local
                # repair. See executer.regenerate_schedule_for_event.
                for index, row in starting_events.iterrows():
                    regen_start = time.perf_counter()
                    dropped = regenerate_schedule_for_event(
                        tds, row.to_dict(), regen_metric=regen_metric, minimize=swap_minimize,
                        save_flexibility=(regen_metric in ("save_flexibility", "full_flex", "full_flex_swap", "full_flex_concave")),
                    )
                    regen_duration = time.perf_counter() - regen_start
                    reschedule_time_total += regen_duration
                    reschedule_call_count += 1
                    reschedule_call_durations.append(regen_duration)
                    _check_flexibility(tds, f"after full regeneration for event at {row['start_time']} on {row['resource']} at time {new_time}", verify_swap)
                    if dropped:
                        print(f"Event at {row['start_time']} on {row['resource']} left the following tasks unplaced after full regeneration: {[task.name for task in dropped]}")
                        all_dropped_tasks.extend(dropped)
            elif reschedule_mode == "cp_regen":
                # Same wipe-when-needed structure as full_regen, but the
                # rebuild is a single CP-SAT solve per event instead of a
                # greedy per-task loop -- see executer.regenerate_schedule_for_event_cp.
                for index, row in starting_events.iterrows():
                    regen_start = time.perf_counter()
                    dropped = regenerate_schedule_for_event_cp(
                        tds, row.to_dict(), time_limit_seconds=cp_time_limit,
                    )
                    regen_duration = time.perf_counter() - regen_start
                    reschedule_time_total += regen_duration
                    reschedule_call_count += 1
                    reschedule_call_durations.append(regen_duration)
                    _check_flexibility(tds, f"after CP regeneration for event at {row['start_time']} on {row['resource']} at time {new_time}", verify_swap)
                    if dropped:
                        print(f"Event at {row['start_time']} on {row['resource']} left the following tasks unplaced after CP regeneration: {[task.name for task in dropped]}")
                        all_dropped_tasks.extend(dropped)
            else:
                for index, row in starting_events.iterrows():
                    removed_tasks = send_event(tds, row.to_dict(), save_flexibility=(task_swap_heuristic in ("save_flexibility", "full_flex", "full_flex_swap", "full_flex_concave")))
                    if removed_tasks:
                        print(f"Event at {row['start_time']} on {row['resource']} caused the following tasks to be removed from the schedule: {[task.name for task in removed_tasks]}")
                        all_removed_tasks.extend(removed_tasks)
                        _check_flexibility(tds, f"after removing {[t.name for t in removed_tasks]} at time {new_time}", verify_swap)
                        #print current schedule
                if all_removed_tasks:
                    print(f"Attempting to reschedule removed tasks: {[task.name for task in all_removed_tasks]}")
                    for i in range(len(all_removed_tasks)):
                        task = all_removed_tasks.pop()
                        swap_start = time.perf_counter()
                        reschedule = task_swap(task, tds, metric=task_swap_heuristic, minimize=swap_minimize, retraction_metric=task_swap_heuristic, max_moves=max_moves)
                        swap_duration = time.perf_counter() - swap_start
                        reschedule_time_total += swap_duration
                        reschedule_call_count += 1
                        reschedule_call_durations.append(swap_duration)
                        _check_flexibility(tds, f"after task_swap({task.name}) at time {new_time}", verify_swap)
                        # if unsuccessful, add to all_dropped_tasks
                        if not reschedule[0]:
                            task.status = 'aborted'
                            all_dropped_tasks.append(task)

        ready_tasks = find_ready_tasks(tds)
        print(f"Ready tasks at time {np.abs(tds.now.lb)}: {[task.name for task in ready_tasks]}")
        completed_tasks = find_completed_tasks(tds)
        print(f"Completed tasks at time {np.abs(tds.now.lb)}: {[task.name for task in completed_tasks]}")
        if ready_tasks:
            print(f"Tasks ready to start at time {np.abs(tds.now.lb)}: {[task.name for task in ready_tasks]}")
            simulate_task_starts(tds, ready_tasks)
        if completed_tasks:
            print(f"Tasks completed at time {np.abs(tds.now.lb)}: {[task.name for task in completed_tasks]}")
            simulate_task_completions(tds, completed_tasks)
        if not ready_tasks and not completed_tasks:
            print(f"No tasks ready to start or complete at time {np.abs(tds.now.lb)}. Move to next time increment.")


        print("-------------------------------")

    print(f"Final list of dropped tasks: {[task.name for task in all_dropped_tasks]}")
    print(f"Tasks never scheduled initially: {[task.name for task in initial_unscheduled_tasks]}")

    cp_optimal_count = sum(1 for _, status in tds.cp_solve_log if status == cp_model.OPTIMAL)
    cp_feasible_count = sum(1 for _, status in tds.cp_solve_log if status == cp_model.FEASIBLE)

    timing_info = {
        "schedule_generation_time": schedule_gen_time,
        "total_reschedule_time": reschedule_time_total,
        "reschedule_call_count": reschedule_call_count,
        "avg_reschedule_time": (reschedule_time_total / reschedule_call_count) if reschedule_call_count else 0.0,
        "reschedule_call_durations": reschedule_call_durations,
        "initial_unscheduled_count": len(initial_unscheduled_tasks),
        "cp_optimal_count": cp_optimal_count,
        "cp_feasible_count": cp_feasible_count,
    }
    if tds.cp_solve_log:
        print(f"CP-SAT solves: {cp_optimal_count} OPTIMAL, {cp_feasible_count} FEASIBLE (time-limited) "
              f"of {len(tds.cp_solve_log)} total.")
    print(f"Schedule generation time: {schedule_gen_time:.4f}s")
    print(f"Total reschedule time: {reschedule_time_total:.4f}s over {reschedule_call_count} call(s) "
          f"(avg {timing_info['avg_reschedule_time']:.4f}s/call)")
    print(f"Initial-unscheduled tasks: {timing_info['initial_unscheduled_count']}")


    return tds, all_dropped_tasks, timing_info


def run_simulation(request_path, travel_path, event_path, initial_heuristic="flexibility", task_swap_heuristic="flexibility", minimize=False, max_moves=10, verify_flexibility=False, reschedule_mode="task_swap", regen_metric="makespan", cp_time_limit=180):
    tds, _, timing_info = _run_simulation(
        request_path,
        travel_path,
        event_path,
        initial_heuristic=initial_heuristic,
        task_swap_heuristic=task_swap_heuristic,
        minimize=minimize,
        max_moves=max_moves,
        verify_flexibility=verify_flexibility,
        reschedule_mode=reschedule_mode,
        regen_metric=regen_metric,
        cp_time_limit=cp_time_limit,
    )
    return tds, timing_info


# run_simulation("/Users/erubinst/ICLL/pythonSTN/tds_slack/simulator/generated_scenarios/disruption_extreme/scenario_027/request.json",
#                "/Users/erubinst/ICLL/pythonSTN/tds_slack/simulator/generated_scenarios/disruption_extreme/scenario_027/travel_matrix.json",
#                "/Users/erubinst/ICLL/pythonSTN/tds_slack/simulator/generated_scenarios/disruption_extreme/scenario_027/future_downtimes.json",
#                initial_heuristic="save_flexibility",
#                task_swap_heuristic="save_flexibility",
#                minimize=False,
#                max_moves=10,
#                verify_flexibility=True)





def run_generated_scenarios_bulk(
    scenarios_dir=None,
    initial_heuristic="flexibility",
    task_swap_heuristic="flexibility",
    minimize=False,
    initial_minimize=None,
    swap_minimize=None,
    max_moves=10,
    reschedule_mode="task_swap",
    regen_metric="makespan",
    cp_time_limit=180,
):
    """
    Run every scenario subfolder found directly under `scenarios_dir` (each
    expected to contain request.json, travel_matrix.json, and
    future_downtimes.json) with a single fixed set of heuristic/max_moves
    settings.

    `minimize` applies to both the initial schedule generation and the
    task-swap rescheduling (the optimization direction is the same for
    both). Defaults to False.

    Returns:
        results_df: one row per scenario, with its dropped-task count and
            timing metrics (schedule generation time, total/avg reschedule
            time, and reschedule call count).
        total_dropped_tasks: sum of dropped tasks across all scenarios run.
        total_schedule_gen_time: sum of schedule generation time (seconds)
            across all scenarios run.
        total_reschedule_time: sum of task_swap (rescheduling) time (seconds)
            across all scenarios run.
    """

    if scenarios_dir is None:
        scenarios_dir = Path(__file__).resolve().parent / "generated_scenarios"
    else:
        scenarios_dir = Path(scenarios_dir)

    scenario_dirs = sorted(path for path in scenarios_dir.iterdir() if path.is_dir())
    results = []
    total_dropped_tasks = 0
    total_initial_unscheduled = 0
    total_schedule_gen_time = 0.0
    total_reschedule_time = 0.0
    total_cp_optimal = 0
    total_cp_feasible = 0

    for scenario_dir in scenario_dirs:
        request_path = scenario_dir / "request.json"
        travel_path = scenario_dir / "travel_matrix.json"
        event_path = scenario_dir / "future_downtimes.json"

        if not request_path.exists() or not travel_path.exists() or not event_path.exists():
            print(f"Skipping {scenario_dir.name}: missing request, travel matrix, or downtime file.")
            continue

        print(f"\nRunning scenario {scenario_dir.name}...")
        tds, dropped_tasks, timing_info = _run_simulation(
            str(request_path),
            str(travel_path),
            str(event_path),
            initial_heuristic=initial_heuristic,
            task_swap_heuristic=task_swap_heuristic,
            minimize=minimize,
            initial_minimize=initial_minimize,
            swap_minimize=swap_minimize,
            max_moves=max_moves,
            reschedule_mode=reschedule_mode,
            regen_metric=regen_metric,
            cp_time_limit=cp_time_limit,
        )

        dropped_count = len(dropped_tasks)
        total_dropped_tasks += dropped_count
        total_initial_unscheduled += timing_info["initial_unscheduled_count"]
        total_schedule_gen_time += timing_info["schedule_generation_time"]
        total_reschedule_time += timing_info["total_reschedule_time"]
        total_cp_optimal += timing_info["cp_optimal_count"]
        total_cp_feasible += timing_info["cp_feasible_count"]
        results.append(
            {
                "scenario": scenario_dir.name,
                "dropped_tasks": dropped_count,
                "initial_unscheduled": timing_info["initial_unscheduled_count"],
                "schedule_generation_time": timing_info["schedule_generation_time"],
                "total_reschedule_time": timing_info["total_reschedule_time"],
                "reschedule_call_count": timing_info["reschedule_call_count"],
                "avg_reschedule_time": timing_info["avg_reschedule_time"],
                "cp_optimal_count": timing_info["cp_optimal_count"],
                "cp_feasible_count": timing_info["cp_feasible_count"],
            }
        )

    results_df = pd.DataFrame(results)
    if not results_df.empty:
        print("\nScenario summary:")
        print(results_df.to_string(index=False))

    print(f"\nTotal tasks dropped across all scenarios: {total_dropped_tasks}")
    print(f"Total tasks never scheduled initially across all scenarios: {total_initial_unscheduled}")
    print(f"Total schedule generation time across all scenarios: {total_schedule_gen_time:.4f}s")
    print(f"Total reschedule time across all scenarios: {total_reschedule_time:.4f}s")
    if total_cp_optimal or total_cp_feasible:
        print(f"CP-SAT solves across all scenarios: {total_cp_optimal} OPTIMAL, {total_cp_feasible} FEASIBLE (time-limited).")

    return results_df, total_dropped_tasks, total_schedule_gen_time, total_reschedule_time


# run_generated_scenarios_bulk(scenarios_dir="/Users/erubinst/ICLL/pythonSTN/tds_slack/simulator/generated_scenarios/small_dense_overlap", initial_heuristic="flexibility", task_swap_heuristic="flexibility", minimize=False, max_moves=10)



DEFAULT_COMBINATIONS = [
    {"metric": "makespan", "max_moves": 10, "minimize": True},
    {"metric": "makespan", "max_moves": 0, "minimize": True},
    # {"metric": "flexibility", "max_moves": 10, "minimize": False},
    # {"metric": "flexibility", "max_moves": 0, "minimize": False},
    {
        "initial_metric": "save_flexibility",
        "task_swap_metric": "flexibility",
        "minimize": False,
        "max_moves": 10,
    },
    {
        "initial_metric": "save_flexibility",
        "task_swap_metric": "flexibility",
        "minimize": False,
        "max_moves": 0,
    }
]


def _normalize_combination(combination):
    """
    Accept combinations that specify the initial-schedule-generation
    objective separately from the task-swap (rescheduling) objective:

        {"initial_metric": ..., "task_swap_metric": ..., "minimize": ...,
         "max_moves": ...}

    For backward compatibility, the older single-objective form is also
    accepted and applied to both initialization and task swap:
    {'metric': ..., 'max_moves': ..., 'minimize': ...},
    (metric, max_moves, minimize), or the shorter (metric, max_moves) /
    {'metric': ..., 'max_moves': ...} without 'minimize'.

    `minimize` is a single value shared by both the initial-generation and
    task-swap objectives, unless the dict form's "initial_minimize" and/or
    "swap_minimize" are given -- those override "minimize" per-role, for
    "mismatched" combinations where initial_metric and task_swap_metric need
    opposite optimization directions (e.g. initial_metric='full_flex'
    minimize=False paired with task_swap_metric='makespan' minimize=True).

    Also accepts an opt-in "reschedule_mode": "full_regen" (default is
    "task_swap") to compare against full-schedule-regeneration instead of
    task_swap's targeted local repair, with its own "regen_metric" (default
    "makespan") in place of task_swap_metric.

    Also accepts an opt-in "cp_time_limit" (default None, meaning "use the
    caller's own default") 

    Returns (initial_metric, task_swap_metric, max_moves, minimize,
    reschedule_mode, regen_metric, cp_time_limit, initial_minimize,
    swap_minimize) where minimize/cp_time_limit/initial_minimize/
    swap_minimize are None if the combination didn't specify one -- callers
    should fall back to their own defaults in that case.
    """
    if isinstance(combination, dict):
        reschedule_mode = combination.get("reschedule_mode", "task_swap")
        regen_metric = combination.get("regen_metric", "makespan")
        max_moves = combination.get("max_moves", 0)
        if "metric" in combination:
            initial_metric = task_swap_metric = combination["metric"]
        else:
            initial_metric = combination["initial_metric"]
            task_swap_metric = combination.get("task_swap_metric", regen_metric)
        minimize = combination.get("minimize")
        cp_time_limit = combination.get("cp_time_limit")
        initial_minimize = combination.get("initial_minimize")
        swap_minimize = combination.get("swap_minimize")
    elif len(combination) == 3:
        metric, max_moves, minimize = combination
        initial_metric = task_swap_metric = metric
        reschedule_mode = "task_swap"
        regen_metric = "makespan"
        cp_time_limit = None
        initial_minimize = None
        swap_minimize = None
    else:
        metric, max_moves = combination
        initial_metric = task_swap_metric = metric
        minimize = None
        reschedule_mode = "task_swap"
        regen_metric = "makespan"
        cp_time_limit = None
        initial_minimize = None
        swap_minimize = None
    return (initial_metric, task_swap_metric, max_moves, minimize, reschedule_mode,
            regen_metric, cp_time_limit, initial_minimize, swap_minimize)


def run_profile_across_combinations(
    profile_path,
    combinations=None,
    minimize=None,
    output_csv_path=None,
    detail_csv_path=None,
    cp_time_limit=180,
):
    """
    Run every scenario in a single profile folder (as produced by
    generate_representative_set.py, e.g. .../slack_scenarios/high_downtime_pressure)
    once per (metric, max_moves) combination, and write out two CSVs:

      1. A summary CSV with one row per combination (profile-level totals
      2. A detail CSV with one row per (scenario, combination).

    Args:
        profile_path: path to a single profile's scenario folder. Each
            immediate subdirectory is expected to contain request.json,
            travel_matrix.json, and future_downtimes.json (i.e. this is the
            same argument you'd pass as `scenarios_dir` to
            run_generated_scenarios_bulk).
        combinations: list of combinations to evaluate, where each combination
            may specify the initial-schedule-generation objective separately
            from the task-swap (rescheduling) objective via a dict
            {"initial_metric": ..., "task_swap_metric": ..., "minimize": ...,
             "max_moves": ...}. The older single-objective form (applied to
            both init and task swap) is also accepted:
            {"metric": ..., "max_moves": ..., "minimize": ...}, a 3-tuple
            (metric, max_moves, minimize), or (metric, max_moves).
            Defaults to DEFAULT_COMBINATIONS. `minimize` is a single value
            shared by both the initial-generation and task-swap objectives.
        minimize: fallback optimization direction used only for combinations
            that don't specify their own "minimize". Leave as None (the
            default) if every combination specifies its own "minimize"; a
            combination's own "minimize" always takes priority over this.
        output_csv_path: where to write the summary CSV. Defaults to
            "<profile_path>/<profile_path.name>_combination_summary.csv".
        detail_csv_path: where to write the per-scenario detail CSV.
            Defaults to "<profile_path>/<profile_path.name>_scenario_detail.csv".

    Returns:
        summary_df: DataFrame with one row per combination, columns:
            profile, initial_metric, task_swap_metric, max_moves,
            minimize, num_scenarios, total_tasks_dropped,
            total_schedule_gen_time, avg_schedule_gen_time,
            total_reschedule_time, avg_reschedule_time
        detail_df: DataFrame with one row per (scenario, combination),
            columns: profile, scenario, initial_metric, task_swap_metric,
            max_moves, minimize, dropped_tasks, schedule_generation_time,
            total_reschedule_time, reschedule_call_count,
            avg_reschedule_time
    """
    profile_path = Path(profile_path)
    if combinations is None:
        combinations = DEFAULT_COMBINATIONS

    if output_csv_path is None:
        output_csv_path = profile_path / f"{profile_path.name}_combination_summary.csv"
    else:
        output_csv_path = Path(output_csv_path)

    if detail_csv_path is None:
        detail_csv_path = profile_path / f"{profile_path.name}_scenario_detail.csv"
    else:
        detail_csv_path = Path(detail_csv_path)

    summary_rows = []
    detail_frames = []
    for combination in combinations:
        (initial_metric, task_swap_metric, max_moves, combo_minimize, reschedule_mode, regen_metric,
         combo_cp_time_limit, combo_initial_minimize, combo_swap_minimize) = _normalize_combination(combination)
        effective_minimize = combo_minimize if combo_minimize is not None else minimize
        effective_cp_time_limit = combo_cp_time_limit if combo_cp_time_limit is not None else cp_time_limit
        effective_initial_minimize = combo_initial_minimize if combo_initial_minimize is not None else effective_minimize
        effective_swap_minimize = combo_swap_minimize if combo_swap_minimize is not None else effective_minimize

        if reschedule_mode == "full_regen":
            print(
                f"\n=== Profile '{profile_path.name}': initial_metric='{initial_metric}', "
                f"reschedule_mode='full_regen', regen_metric='{regen_metric}', "
                f"minimize={effective_minimize} ==="
            )
        else:
            print(
                f"\n=== Profile '{profile_path.name}': initial_metric='{initial_metric}' (minimize={effective_initial_minimize}), "
                f"task_swap_metric='{task_swap_metric}' (minimize={effective_swap_minimize}), max_moves={max_moves} ==="
            )
        results_df, total_dropped_tasks, total_schedule_gen_time, total_reschedule_time = run_generated_scenarios_bulk(
            scenarios_dir=profile_path,
            initial_heuristic=initial_metric,
            task_swap_heuristic=task_swap_metric,
            minimize=effective_minimize,
            initial_minimize=effective_initial_minimize,
            swap_minimize=effective_swap_minimize,
            max_moves=max_moves,
            reschedule_mode=reschedule_mode,
            regen_metric=regen_metric,
            cp_time_limit=effective_cp_time_limit,
        )

        # Tag this combination's per-scenario rows and keep them (rather
        # than only the collapsed totals) so scenarios can be compared
        # across combinations afterward.
        if not results_df.empty:
            tagged_df = results_df.copy()
            tagged_df.insert(0, "profile", profile_path.name)
            tagged_df.insert(2, "initial_metric", initial_metric)
            tagged_df.insert(3, "task_swap_metric", task_swap_metric)
            tagged_df.insert(4, "max_moves", max_moves)
            tagged_df.insert(5, "minimize", effective_minimize)
            tagged_df.insert(6, "reschedule_mode", reschedule_mode)
            tagged_df.insert(7, "regen_metric", regen_metric)
            tagged_df.insert(8, "initial_minimize", effective_initial_minimize)
            tagged_df.insert(9, "swap_minimize", effective_swap_minimize)
            tagged_df = tagged_df.rename(columns={"total_reschedule_time": "reschedule_time"})
            detail_frames.append(tagged_df)

        num_scenarios = len(results_df)
        total_initial_unscheduled = int(results_df["initial_unscheduled"].sum()) if not results_df.empty else 0
        total_cp_optimal = int(results_df["cp_optimal_count"].sum()) if not results_df.empty else 0
        total_cp_feasible = int(results_df["cp_feasible_count"].sum()) if not results_df.empty else 0
        summary_rows.append(
            {
                "profile": profile_path.name,
                "initial_metric": initial_metric,
                "task_swap_metric": task_swap_metric,
                "max_moves": max_moves,
                "minimize": effective_minimize,
                "initial_minimize": effective_initial_minimize,
                "swap_minimize": effective_swap_minimize,
                "reschedule_mode": reschedule_mode,
                "regen_metric": regen_metric,
                "cp_time_limit": effective_cp_time_limit,
                "num_scenarios": num_scenarios,
                "total_tasks_dropped": total_dropped_tasks,
                "total_initial_unscheduled": total_initial_unscheduled,
                "total_schedule_gen_time": total_schedule_gen_time,
                "avg_schedule_gen_time": (total_schedule_gen_time / num_scenarios) if num_scenarios else 0.0,
                "total_reschedule_time": total_reschedule_time,
                "avg_reschedule_time": (total_reschedule_time / num_scenarios) if num_scenarios else 0.0,
                "total_cp_optimal": total_cp_optimal,
                "total_cp_feasible": total_cp_feasible,
            }
        )

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()

    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(output_csv_path, index=False)

    detail_csv_path.parent.mkdir(parents=True, exist_ok=True)
    detail_df.to_csv(detail_csv_path, index=False)

    print(f"\nSaved combination summary to {output_csv_path}")
    print(summary_df.to_string(index=False))
    print(f"\nSaved per-scenario detail ({len(detail_df)} rows) to {detail_csv_path}")

    return summary_df, detail_df

if __name__ == "__main__":
    run_profile_across_combinations(
        profile_path="/Users/erubinst/ICLL/pythonSTN/tds_slack/simulator/generated_scenarios/sweep_workload_04",
    )




