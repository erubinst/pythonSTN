from tds_slack.resource import Resource
from tds_slack.task import Task
from tds_slack.task_swap import task_swap
from tds_slack.tds_manager import TDSManager
from tds_slack.parse import *
from tds_slack.utils import *
from tds_slack.slack_search import schedule_task, search_feasible_slots


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


def send_event(tds, row):
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
        result, affected_timepoint = resource.insert_task_to_timeline(
            dt_task,
            f'{resource.name}_presence',
            prev_task=prev_task,
            return_affected_timepoint=True,
        )

        if result:
            print(f"Inserted downtime {dt_task.name} on {resource.name}.")
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

        resource.timeline.remove_task(affected_task)
        removed_tasks.append(affected_task)
        print(f"Removed affected task {affected_task.name} from {resource.name} to insert {dt_task.name}.")

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

    return removed_tasks



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


