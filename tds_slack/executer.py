import io
from contextlib import redirect_stdout

from tds_slack.resource import Resource
from tds_slack.task import Task
from tds_slack.task_swap import task_swap
from tds_slack.tds_manager import TDSManager
from tds_slack.parse import *
from tds_slack.utils import *
from tds_slack.slack_search import schedule_task, search_feasible_slots
from tds_slack.optimizer_scheduler import regenerate_schedule_cp
import numpy as np


def _run_with_expected_stn_output_block(callable_obj):
    """Capture stdout from expected STN diagnostics and reprint it as an indented block."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = callable_obj()

    captured = buffer.getvalue().rstrip()
    if captured:
        print("[Expected STN Diagnostics] The following inconsistency details are informational:")
        for line in captured.splitlines():
            print(f"    {line}")
        print("[End Expected STN Diagnostics]")

    return result


def add_resources_to_tds(resources_df, tds_manager):
    """
    Create Resource objects from resources_df and add them to the TDS manager.

    Parameters:
        resources_df (pd.DataFrame): DataFrame with columns ['resource_name', 'capabilities']
        tds_manager: initialized TDS manager object
    """
    for _, row in resources_df.iterrows():
        name = row["resource_name"]
        caps = [c.strip() for c in row["capabilities"].split(",")] if row["capabilities"] else []
        base_location = row['location']
        res = Resource(name, caps, base_location, tds_manager)


def add_downtimes_to_tds(downtimes_df, tds_manager):
    if downtimes_df.empty:
        return
    for res_name, group in downtimes_df.groupby("resource_name"):
        res_name = res_name.lower()
        resource = tds_manager.resources[res_name]
        # sort in order of start times
        group = group.sort_values('start_time')
        prev_task = resource.timeline.tasks[0]
        needs_pd = []
        for _, row in group.iterrows():
            downtime_task = resource.timeline.generate_downtime(row['start_time'], 
                                                                row['end_time'], 
                                                                row['duration'],
                                                                row['location'],
                                                                prev_task)
            
            if 'traveler' not in resource.capabilities:
                needs_pd.append(downtime_task)
            
            prev_task = downtime_task


def add_tasks_to_tds(tasks_df, tds_manager):
    """
    Create Task objects from tasks_df and add them to the TDS manager.
    Does NOT assign resources yet.

    Parameters:
        tasks_df (pd.DataFrame): columns = ['task_name', 'required_capabilities', 'est', 'lft', 'duration']
        tds_manager: initialized TDS manager object
    """
    for _, row in tasks_df.iterrows():
        name = row["task_name"]
        try:
            task = Task(
                name=name,
                capability=row['capability'],
                tds_manager=tds_manager,
                locations = row['locations'],
                task_type = row['task_type']
            )
        except ValueError as e:
            print(f"Error creating task '{name}': {e}")
            continue

        task.add_time_window_constraints(row.get('est'), row.get('lft'))
        task.add_duration_constraint(row.get('duration'))


def upload_request(request, travel_matrix, epoch_date, add_downtimes=True):
    resources_df, downtimes_df, tasks_df = load_resources_and_tasks(
        request, epoch_date
    )
    tds = TDSManager(travel_matrix)
    add_resources_to_tds(resources_df, tds)
    if add_downtimes:
        add_downtimes_to_tds(downtimes_df, tds)
    add_tasks_to_tds(tasks_df, tds)

    return tds


def send_event(tds, row, save_flexibility=False):
    removed_tasks = []

    resource = tds.resources[row['resource'].lower()]
    # Initializing downtime instance
    dt_task = resource.timeline.generate_downtime(row['start_time'], row['end_time'], row['duration'], row['location'], None, insert=False)
    overlapping_task = resource.timeline.find_first_overlapping_task(dt_task)
    prev_task = None
    if overlapping_task:
        print(f"Resource {resource.name} has an overlapping task {overlapping_task.name} with the downtime starting at {row['start_time']}")
        overlapping_task_idx = resource.timeline.tasks.index(overlapping_task)
        prev_task = resource.timeline.tasks[overlapping_task_idx - 1]
    else:
        prev_task = resource.timeline.find_preceding_task(dt_task)
        print(f"Resource {resource.name} has no overlapping task with the downtime starting at {row['start_time']}. Previous task in timeline is {prev_task.name if prev_task else 'None'}")

    if prev_task is None:
        print(f"No feasible previous task found for downtime {dt_task.name} on {resource.name}.")
        return removed_tasks

    while True:
        # need a statement to catch the print from the insert call in order to specify this is an expected STN inconsistency
        result, affected_timepoint = _run_with_expected_stn_output_block(
            lambda: resource.insert_task_to_timeline(
                dt_task,
                f'{resource.name}_presence',
                prev_task=prev_task,
                return_affected_timepoint=True,
                save_flexibility=save_flexibility,
            )
        )

        if result:
            break

        if affected_timepoint is None:
            print(f"Failed to insert downtime {dt_task.name} on {resource.name} with no affected timepoint.")
            break

        affected_task = tds.find_task_by_timepoint(affected_timepoint)
        if affected_task is None:
            print(f"Could not resolve affected task for timepoint {affected_timepoint}.")
            break

        # if affected task is current dt task
        if affected_task == dt_task:
            # find next task in timeline and remove it
            print(f"STN returned downtime task as the affected task, finding next task in timeline to remove")
            prev_task_idx = resource.timeline.tasks.index(prev_task)
            if prev_task_idx + 1 < len(resource.timeline.tasks):
                next_task = resource.timeline.tasks[prev_task_idx + 1]
                affected_task = next_task
            else:
                print(f"Downtime {dt_task.name} overlaps with no next task on {resource.name} after {prev_task.name}. Cannot insert downtime.")
                break

        # Never remove timeline boundary tasks or tasks that contain 'downtime'
        if affected_task.name.endswith('_header') or affected_task.name.endswith('_footer') or 'downtime' in affected_task.name:
            print(f"Cannot remove boundary/downtime task {affected_task.name}; stopping retries for {dt_task.name}.")
            break

        if affected_task not in resource.timeline.tasks:
            print(f"Affected task {affected_task.name} is not on resource {resource.name} timeline.")
            break

        if affected_task.status not in ("scheduled", "executing"):
            print(f"Affected task {affected_task.name} is not scheduled or executing (status: {affected_task.status}); cannot remove it.")
            break

        # A downtime takes priority over in-progress work: preempt an executing task
        # to make room too, not just scheduled ones. Either way the task is queued
        # in removed_tasks for a full task_swap reschedule attempt (same duration as
        # originally planned -- there's no partial-progress tracking), and counted
        # as dropped in simulation.py if that reschedule ultimately fails.
        prior_status = affected_task.status
        resource.timeline.remove_task(affected_task, save_flexibility=save_flexibility, allow_executing=True)
        removed_tasks.append(affected_task)
        print(f"Removed affected task {affected_task.name} (was {prior_status}) from {resource.name} to insert {dt_task.name}.")

        # Recompute insertion predecessor after timeline changed.
        overlapping_task = resource.timeline.find_first_overlapping_task(dt_task)
        if overlapping_task:
            print(f"Resource {resource.name} has an overlapping task {overlapping_task.name} with the downtime starting at {row['start_time']}")
            overlapping_task_idx = resource.timeline.tasks.index(overlapping_task)
            prev_task = resource.timeline.tasks[overlapping_task_idx - 1] if overlapping_task_idx > 0 else None
        else:
            prev_task = resource.timeline.find_preceding_task(dt_task)
            print(f"Resource {resource.name} has no overlapping task with the downtime starting at {row['start_time']}. Previous task in timeline is {prev_task.name if prev_task else 'None'}")

        if prev_task is None:
            print(f"No valid previous task found after removals for {dt_task.name} on {resource.name}.")
            break

    # print bounds of downtime task
    print(f"Downtime task {dt_task.name} inserted on {resource.name} with bounds [{np.abs(dt_task.start.lb)}, {np.abs(dt_task.end.lb)}].")
    return removed_tasks


def _find_overlapping_scheduled_task(tds, row):
    """
    Read-only equivalent of Timeline.find_first_overlapping_task for a
    not-yet-created downtime task, built from the raw event fields instead of a
    live Task object -- constructing one here (via generate_downtime) would
    register it with the tds/STN under the same name send_event's own
    generate_downtime call uses later, causing a duplicate-registration crash.

    Returns the first 'scheduled' task (of any kind, including existing
    downtime/boundary tasks) whose window overlaps this event's downtime, or
    None if inserting it would be free (nothing needs to move).
    """
    resource = tds.resources[row['resource'].lower()]
    location = row['location']
    start, end = row['start_time'], row['end_time']
    for task in resource.timeline.tasks:
        if task.status != 'scheduled':
            continue
        task_start_location = task.locations[0]
        task_end_location = task.locations[-1]
        travel_time_to_new = tds.travel_matrix[task_end_location][location]
        travel_time_from_new = tds.travel_matrix[location][task_start_location]
        if (np.abs(task.start.lb) - travel_time_to_new < end
                and np.abs(task.end.ub) + travel_time_from_new > start):
            return task
    return None


def regenerate_schedule_for_event(tds, row, regen_metric='makespan', minimize=True, save_flexibility=False):
    """
    Full-schedule-regeneration alternative to send_event + task_swap: instead of a
    targeted local repair, wipe every not-yet-started task tds-wide, insert this
    event's downtime (preempting an executing task if one is in the way, via the
    same send_event path task_swap mode uses), then greedily rebuild the whole
    remaining schedule in ascending-flexibility order. Meant as a lower-bound
    comparison for how many tasks task_swap's incremental approach manages to save.

    Only wipes/rebuilds when the downtime actually needs something to move --
    if it can be inserted for free, this just inserts it directly and does
    nothing else, matching task_swap's own behavior of being a no-op when
    nothing is displaced. Wiping unconditionally on every event (even ones
    needing zero changes) was giving the one-shot greedy rebuild far more
    chances to mis-pack an otherwise-fine schedule than task_swap ever gets,
    which is what was making full_regen look worse than task_swap despite
    having strictly more freedom to rearrange.

    If the downtime would collide with an existing downtime/boundary task, this
    event is skipped entirely -- no wipe, no rebuild, schedule left untouched --
    matching how send_event's own equivalent case is a no-op for task_swap mode.

    Returns the list of tasks that could not be placed back onto the schedule.
    """
    overlapping = _find_overlapping_scheduled_task(tds, row)

    if overlapping is not None and (
        overlapping.name.endswith('_header') or overlapping.name.endswith('_footer') or 'downtime' in overlapping.name
    ):
        print(f"Downtime on {row['resource']} at {row['start_time']} collides with an existing downtime/boundary task; skipping full regeneration for this event.")
        return []

    if overlapping is None:
        # Free insertion: nothing forces a rebuild. send_event may still come
        # back with a preempted task if an executing task is caught at the STN
        # level (this bounding check only considers 'scheduled' tasks).
        print(f"Downtime on {row['resource']} at {row['start_time']} is a free insertion; no regeneration needed for this event.")
        pool = send_event(tds, row, save_flexibility=save_flexibility)
    else:
        print(f"Downtime on {row['resource']} at {row['start_time']} conflicts with {overlapping.name}; regenerating the full remaining schedule for this event.")
        wiped_tasks = tds.wipe_all_scheduled_tasks(save_flexibility=save_flexibility)
        preempted_tasks = send_event(tds, row, save_flexibility=save_flexibility)
        pool = wiped_tasks + preempted_tasks

    dropped_tasks = []
    for task in tds.sort_tasks_by_flexibility(pool):
        result = schedule_task(tds, task, objective_metric=regen_metric, minimize=minimize)
        if result is False:
            task.status = 'aborted'
            dropped_tasks.append(task)

    return dropped_tasks


def regenerate_schedule_for_event_cp(tds, row, time_limit_seconds=180, save_flexibility=False):
    """
    CP-SAT-driven alternative to regenerate_schedule_for_event. Whenever this
    event actually displaces anything, every other currently-'scheduled'
    task is also wiped and CP-SAT re-solves them all jointly -- giving CP
    the same full freedom a from-scratch regeneration should have, a strict
    superset of what task_swap can do (which can only evict up to max_moves
    other tasks). CP-SAT's rebuild is provably optimal, so unlike a greedy
    rebuild (see regenerate_schedule_for_event's docstring for why
    unconditional wiping is wrong there), this has no downside: given the
    same task pool, it can only match or beat whatever already existed.

    Critically, whether anything is displaced at all is determined by
    calling send_event FIRST and checking its (authoritative, real STN-
    level) return value -- NOT by wiping unconditionally and NOT by the
    'scheduled'-only geometry pre-check (_find_overlapping_scheduled_task),
    which misses conflicts with 'executing' tasks. Wiping on every event
    regardless of whether it truly conflicts with anything was tried and is
    wrong: on scenarios where the existing schedule already absorbs a
    disruption for free, it forces a needless full rebuild anyway, and since
    CP's objective (maximize count placed) has no preference among tied
    optimal solutions, each needless rebuild is a free chance to land on a
    less resilient arrangement than what was already there for no benefit.

    'executing' and downtime tasks are left on their resource's timeline
    (never wiped) and enter the CP model as fixed anchors that the freed
    tasks get placed around -- see optimizer_scheduler.build_and_solve.

    Returns the list of tasks that could not be placed back onto the schedule.
    """
    overlapping = _find_overlapping_scheduled_task(tds, row)

    if overlapping is not None and (
        overlapping.name.endswith('_header') or overlapping.name.endswith('_footer') or 'downtime' in overlapping.name
    ):
        print(f"Downtime on {row['resource']} at {row['start_time']} collides with an existing downtime/boundary task; skipping regeneration for this event.")
        return []

    preempted_tasks = send_event(tds, row, save_flexibility=save_flexibility)

    if not preempted_tasks:
        print(f"Downtime on {row['resource']} at {row['start_time']} is a free insertion; no regeneration needed for this event.")
        return []

    wiped_tasks = tds.wipe_all_scheduled_tasks(save_flexibility=save_flexibility)
    pool = wiped_tasks + preempted_tasks

    print(f"Downtime on {row['resource']} at {row['start_time']} displaced {len(pool)} task(s); "
          f"regenerating the full remaining schedule via CP-SAT for this event.")
    return regenerate_schedule_cp(tds, pool, time_limit_seconds=time_limit_seconds)



def send_events(tds, events_df, objective_metric="slack", minimize=True):
    # loop through events and add downtime
    unscheduled_tasks = []
    for _, row in events_df.iterrows():
        removed_tasks = send_event(tds, row)
        print('---')
        print(removed_tasks)
        for task in removed_tasks:
            schedule_attempt = schedule_task(tds, task, objective_metric=objective_metric, minimize=minimize)
            if schedule_attempt is False:
                print(f"Could not reschedule task {task.name} after removal due to downtime event. Task remains unscheduled.")
                unscheduled_tasks.append(task)
            else:
                print(f"Successfully rescheduled task {task.name} after removal due to downtime event.")
        print('===')
    return unscheduled_tasks


def send_events_no_reschedule(tds, events_df, objective_metric="slack"):
    # loop through events and add downtime
    unscheduled_tasks = []
    alternative_options = 0 # count of alternate options found for removed tasks
    removed_task_count = 0
    for _, row in events_df.iterrows():
        removed_tasks = send_event(tds, row)
        removed_task_count = len(removed_tasks)
        for task in removed_tasks:
            feasible_slots = search_feasible_slots(tds, task, metrics=['slack'])
            if not feasible_slots:
                unscheduled_tasks.append(task)
            else:
                for slot in feasible_slots:
                    alternative_options += slot['slack']
            break # only process one event for testing
    return unscheduled_tasks, alternative_options, removed_task_count


def run_scheduler(request, travel_matrix, objective_metric="flexibility", epoch_date=None, minimize=True, metrics=None):
    tds = upload_request(request, travel_matrix, epoch_date)
    tds.include_unscheduled_in_flexibility = (objective_metric == "full_flex")

    # schedule tasks
    for task in tds.sort_tasks_by_flexibility():
        best_value = schedule_task(tds, task, objective_metric=objective_metric, minimize=minimize)
    
    # df = export_schedule_to_df(tds)
    return tds


scenarios_dir = 'scenario_sweeps/'
scenario = 'scenario_20260601_151102'
request_path = scenarios_dir + scenario + '/request.json'
travel_path = scenarios_dir + scenario + '/travel_matrix.json'
unexpected_downtimes_path = scenarios_dir + scenario + '/future_downtimes.json'


if __name__ == "__main__":
    with open(request_path, 'r') as f:
        request_dict = json.load(f)
    with open(travel_path, 'r') as f:
        travel_matrix = json.load(f)
    events_df = pd.read_json(unexpected_downtimes_path)

    tds = run_scheduler(request_dict, travel_matrix, objective_metric="flexibility", minimize=False)
    # export schedule to csv
    # export_schedule_to_csv(tds)
    for _, row in events_df.iterrows():
        removed_tasks = send_event(tds, row)
        print(f"Removed tasks for downtime event on resource {row['resource']}: {[t.name for t in removed_tasks]}")
        for task in removed_tasks:
            print('---')
            print(f"Attempting task swap on removed task {task.name}...")
            swap_results = task_swap(task, tds)

    # display_current_schedule(tds)
    # unscheduled_tasks = send_events(tds, events_df, objective_metric="makespan", minimize=True)
    # display_current_schedule(tds)
    # print(f"Unscheduled tasks after processing events: {[task.name for task in unscheduled_tasks]}")


