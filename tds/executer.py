from tds.resource import Resource
from tds.task import Task
from tds.tds_manager import TDSManager
from tds.config import *
from tds.parse import *
from tds.utils import *
from tds.search import (
    find_independent_task_assignment,
    schedule_independent_tasks,
    schedule_dependent_task,
    find_dependent_task_assignment,
    apply_independent_assignment,
    apply_assignment as apply_dependent_assignment,
    ObjectiveType,
    SortType
)

# Default objective for scheduling (change here to switch behavior)
DEFAULT_OBJECTIVE = ObjectiveType.MIN_TRAVEL_TIME
# Default sort for independent task scheduling (change here to switch sorting)
DEFAULT_SORT = SortType.FLEXIBILITY


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
        type = row['type']
        res = Resource(name, caps, base_location, type, tds_manager)


def add_downtimes_to_tds(downtimes_df, tds_manager):
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


def schedule_pd_task(tds, pickup, dropoff):
    options = []
    for resource in tds.resources.values():
        if 'transport' in resource.capabilities:
            slots = resource.timeline.map_feasible_slots_linked_tasks(pickup, dropoff)
            for slot in slots:
                slot['resource'] = resource
                options.append(slot)
        best = min(options, key=lambda x: x["total_travel"])
        # get resource in best
        resource = best['resource']
        resource.insert_task_to_timeline(pickup, 'transport', prev_task=best['task1_prior_task'], generate_travel=True)
        resource.insert_task_to_timeline(dropoff, 'transport', prev_task=best['task2_prior_task'], generate_travel=True)



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
        # if capabilities is a string
        if isinstance(row["required_capabilities"], str):
            capabilities = [c.strip() for c in row["required_capabilities"].split(",")] if row["required_capabilities"] else []
        else:
            capabilities = row["required_capabilities"] if row["required_capabilities"] else []
        try:
            task = Task(
                name=name,
                capabilities=capabilities,
                tds_manager=tds_manager,
                locations = row['locations'],
                task_type = row['task_type'],
                caregiver_routine = row['caregiver_routine'] if 'caregiver_routine' in row else False
            )
        except ValueError as e:
            print(f"Error creating task '{name}': {e}")
            continue

        task.add_time_window_constraints(row.get('est'), row.get('lft'))
        task.add_duration_constraint(row.get('duration'))


def add_order_constraints_to_tds(order_constraints_df, tds_manager):
    """
    Add order constraints from DataFrame to TDS manager.
    Parameters:
        order_constraints_df (pd.DataFrame): columns = ['preceding_task', 'succeeding_task']
        tds_manager: initialized TDS manager object
    """
    for _, row in order_constraints_df.iterrows():
        preceding_task_name = row['preceding_task']
        succeeding_task_name = row['succeeding_task']
        preceding_task = tds_manager.tasks.get(preceding_task_name)
        succeeding_task = tds_manager.tasks.get(succeeding_task_name)
        if not preceding_task or not succeeding_task:
            print(f"Warning: One of the tasks '{preceding_task_name}' or '{succeeding_task_name}' not found; skipping constraint")
            continue
        # Add constraint to TDS manager's STN
        preceding_task.constrain_before(succeeding_task, ("all", "sequence"))
#one of the parameters is constraint_type - ("all", "sequence")

# ---------------------------------------------------------
# Routine for starting from given schedule like CP model
def load_initial_timelines_to_tds(df, tds_manager, downtimes_df=None):
    """
    Append tasks to each resource's timeline using a schedule DataFrame.

    Parameters:
        df (DataFrame): DataFrame produced by schedule_json_to_df()
        tds_manager (TDSManager): TDS manager with tasks & resources loaded
    """
    # go through groups by resource name
    for res_name, group in df.groupby("resource_name"):
        res_name = res_name.lower()
        if res_name not in tds_manager.resources:
            print(f"Warning: resource '{res_name}' not found; skipping timeline")
            continue

        resource = tds_manager.resources[res_name]
        # First time through should have header since resource is initialized
        prev_task = resource.timeline.tasks[0]
        # If resource has traveler capability, add set generate_travel to true
        generate_travel = True # if 'traveler' in resource.capabilities else False

        for _, row in group.iterrows():
            order_name = row["order"].lower()
            capability = row["capability"].lower()

            # if starts with {resource_name}_downtime
            if order_name.startswith(f"{res_name}_downtime"):
                # find downtime in request_df
                if downtimes_df is not None:
                    # pull downtimes where resourceTypes
                    downtime_info = downtimes_df[downtimes_df['resource_name'] == res_name]
                    dt_start = int(order_name.split('_')[-1])
                    downtime_info = [d for d in downtime_info.to_dict(orient='records') if d['start_time'] == dt_start] 
                    downtime_info = downtime_info[0] if downtime_info else None
                    if downtime_info:
                        location = downtime_info['location']
                        start_time = downtime_info['start_time']
                        end_time = downtime_info['end_time']
                        duration = downtime_info['duration']

                        downtime_task = resource.timeline.generate_downtime(start_time, 
                                                                            end_time, 
                                                                            duration,
                                                                            location,
                                                                            prev_task)
                        prev_task = downtime_task
                        continue

            task = tds_manager.tasks.get(order_name)
            if not task:
                print(f"Warning: task/order '{order_name}' not found in TDS manager; skipping")
                continue

            try:
                resource.insert_task_to_timeline(task, capability, prev_task, generate_travel)
            except ValueError as e:
                print(f"Error appending task '{order_name}' to resource '{res_name}': {e}")

            prev_task = task

        # for _, row in group.iterrows():
        #     order_name = row["order"].lower()
        #     task = tds_manager.tasks.get(order_name)
        #     resource.timeline.add_return_stops(task)
            # initially schedule transport on nondriver timeline
# ---------------------------------------------------------


def add_return_home_tasks(tds):
    for resource in tds.resources.values():
        if 'traveler' in resource.capabilities:
            for task in resource.timeline.tasks:
                # if header or footer, skip
                if task.name.endswith('_header') or task.name.endswith('_footer'):
                    continue
                resource.timeline.add_return_stops(task)


def reduce_like_task_durations(tds):
    task_type_percents = {
        'food_shopping': 0.65,
        'shopping': 0.55
    }

    same_task_groups = tds.same_task_groups()
    for resource_name, task_groups in same_task_groups.items():
        for task_group in task_groups:
            if len(task_group) > 1:
                task_type = task_group[0].task_type
                if task_type in task_type_percents:
                    percent = task_type_percents[task_type]
                    # print statement reducing duration with task name and task type and percent
                    print(f"Reducing duration of {task_type} tasks to {percent*100}% for resource {resource_name} for tasks {[task.name for task in task_group]}")
                    for task in task_group:
                        current_duration = task.get_duration()
                        new_duration = int(current_duration * percent)
                        task.start.delete_constraint(task.end, ('all', 'duration'))
                        task.add_duration_constraint(new_duration)


def calculate_uncoordinated_time(tds):
    # sum up for all assigned tasks travel from home location to tasks and back
    total_travel_uncoordinated = 0
    for resource in tds.resources.values():
        if 'traveler' not in resource.capabilities:
            continue
        home_location = resource.base_location
        for task in resource.timeline.tasks:
            # skip header/footer/downtime tasks
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            # get travel time from home to task start location
            travel_to_task = tds.travel_matrix[home_location][task.locations[0]]
            # get travel time from task end location to home
            travel_from_task = tds.travel_matrix[task.locations[0]][home_location]
            total_travel_uncoordinated += (travel_to_task + travel_from_task)

    for resource in tds.resources.values():
        for task in resource.timeline.tasks:
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            task_duration = task.get_duration()
            total_travel_uncoordinated += task_duration
    
    return total_travel_uncoordinated


def upload_request(request, travel_matrix, epoch_date, add_downtimes=True):
    resources_df, downtimes_df, tasks_df, order_constraints = load_resources_and_tasks(
        request, epoch_date
    )
    tds = TDSManager(travel_matrix)
    add_resources_to_tds(resources_df, tds)
    if add_downtimes:
        add_downtimes_to_tds(downtimes_df, tds)
    add_tasks_to_tds(tasks_df, tds)
    # TODO fix order constraints
    # add_order_constraints_to_tds(order_constraints, tds)

    return tds


def run_scheduler(request, travel_matrix, epoch_date):
    tds = upload_request(request, travel_matrix, epoch_date)

    dependent_tasks = schedule_independent_tasks(tds, DEFAULT_OBJECTIVE, DEFAULT_SORT)
    for dep_task in dependent_tasks:
        schedule_dependent_task(tds, dep_task, DEFAULT_OBJECTIVE)
    
    # add_return_home_tasks(tds)
    
    df = export_schedule_to_df(tds, epoch_date)
    return df


def export_schedule(tds, epoch_date):
    return export_schedule_to_df(tds, epoch_date)


def reload_tds(scenario, current_schedule):
    resources_df, downtimes_df, tasks_df, order_constraints = load_resources_and_tasks(
        scenario[0], scenario[2]
    )
    tds = TDSManager(scenario[1])
    add_resources_to_tds(resources_df, tds)
    add_tasks_to_tds(tasks_df, tds)
    # load initial schedule form current_schedule, need to add in pickup/dropoff tasks since not in request
    pd_tasks = current_schedule[current_schedule['order'].str.startswith('pickup_from_') | current_schedule['order'].str.startswith('dropoff_at_')]
    # deduplicate pd_tasks by order name
    pd_tasks = pd_tasks.drop_duplicates(subset=['order'])
    for _, row in pd_tasks.iterrows():
        order_name = row["order"].lower()
        capability = row["capability"].lower()
        try:
            task = Task(
                name=order_name,
                capabilities=[capability],
                tds_manager=tds,
                locations = [row['location'], row['location']],
                task_type = 'transport'
            )
        except ValueError as e:
            print(f"Error creating task '{order_name}': {e}")
            continue
        task.add_duration_constraint(0)
    load_initial_timelines_to_tds(current_schedule, tds, downtimes_df)
    return tds



def add_task(tds, new_task_info):
    # add new task in 
    # assume task is df in format  ['task_name', 'required_capabilities', 'est', 'lft', 'duration']
    print(f"Adding new task {new_task_info['task_name'][0]} with info {new_task_info.to_dict(orient='records')[0]} to TDS")
    add_tasks_to_tds(new_task_info, tds)
    print(f"Added new task {new_task_info['task_name'][0]} to TDS")
    task_instance = tds.tasks[new_task_info['task_name'][0]]
    # try to schedule
    assignment = find_independent_task_assignment(tds, task_instance, DEFAULT_OBJECTIVE)
    if not assignment:
        print(f"Could not find independent assignment for new task {task_instance.name}, trying to find dependent assignment")
        assignment = find_dependent_task_assignment(tds, task_instance, DEFAULT_OBJECTIVE)
        # add task key to assignment dict for apply_assignment
        if assignment:
            assignment['task'] = task_instance
        else:
            raise ValueError(f"Could not find any assignment for new task {task_instance.name}")
    else:
        assignment = pd.DataFrame([{
            'capability_assignment': assignment,
            'total_ride_time': 0,
            'total_travel_time': 0,
            'transport_assignment': [],
            'task': task_instance
        }])
    return assignment


# apply assignment in format from add task
def apply_assignment(tds, assignment):
    """
    Apply assignment for a single task.
    Handles both independent and dependent task assignments.
    
    Parameters:
        tds: Task Dependent Scheduling manager
        task: Task to assign
        assignment: Either a list of (resource, prior_task, capability) tuples for independent tasks,
                   or a dict with 'capability_assignment' and 'transport_assignment' keys for dependent tasks
    """
        # Check if this is an independent or dependent task
    driver_capabilities = tds.get_driver_capabilities()
    print(f'Assignment {assignment}')
    task = assignment['task']
    is_independent = all(cap in driver_capabilities for cap in task.capabilities)
    
    if is_independent:
        # For independent tasks, extract just the capability_assignment if it's a dict
        if isinstance(assignment, dict):
            capability_assignment = assignment.get('capability_assignment', assignment)
        else:
            capability_assignment = assignment
        apply_independent_assignment(tds, task, capability_assignment)
    else:
        # Assignment is a dict with 'capability_assignment' and 'transport_assignment'
        apply_dependent_assignment(tds, task, assignment)

    return tds


# Only run this if executed directly (not imported)
if __name__ == '__main__':
    request_data = path_to_dict(REQUEST_PATH)
    travel_data = path_to_dict(TRAVEL_MATRIX_PATH)
    resources_df, downtimes_df, tasks_df, order_constraints = load_resources_and_tasks(
        request_data, EPOCH_DATE
    )
    tds = TDSManager(travel_data)
    add_resources_to_tds(resources_df, tds)
    add_downtimes_to_tds(downtimes_df, tds)
    add_tasks_to_tds(tasks_df, tds)
    # TODO fix order constraints
    # add_order_constraints_to_tds(order_constraints, tds)

    dependent_tasks = schedule_independent_tasks(tds, DEFAULT_OBJECTIVE, DEFAULT_SORT)
    for dep_task in dependent_tasks:
        schedule_dependent_task(tds, dep_task, DEFAULT_OBJECTIVE)

    reduce_like_task_durations(tds)
    print(f"Total task + travel time: {tds.calculate_total_travel_task_time()} minutes")
    # print(f"Total uncoordinated time: {calculate_uncoordinated_time(tds)} minutes")
    
    # add_return_home_tasks(tds)

    # export to csv
    df = export_schedule_to_df(tds, EPOCH_DATE)
    df.to_csv("schedule.csv", index=False)
    caregiver_time = tds.get_caregiver_total_time()
    print(f"Caregiver time: {caregiver_time}")
    display_current_schedule(tds, EPOCH_DATE, resource_type='cg')


# --- routine for taking input schedule: ---
# init_schedule = schedule_json_to_df(INITIAL_SCHEDULE_PATH)
# load_initial_timelines_to_tds(init_schedule, tds)
# pd_tasks = add_pickup_dropoff(tds)
# schedule_pd_tasks(tds, pd_tasks)
# add_return_home_tasks(tds)