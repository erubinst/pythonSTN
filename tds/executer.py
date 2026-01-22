from tds.resource import Resource
from tds.task import Task
from tds.tds_manager import TDSManager
from tds.config import *
from tds.parse import *
from tds.utils import *
from search import schedule_independent_tasks, schedule_dependent_task


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
    # TODO: Ashna - after adding task type to the df in parse.py and to the task class as a param
    # add to the task instantiation here and set with row['task_type']
    for _, row in tasks_df.iterrows():
        name = row["task_name"]
        capabilities = [c.strip() for c in row["required_capabilities"].split(",")] if row["required_capabilities"] else []
        try:
            task = Task(
                name=name,
                capabilities=capabilities,
                tds_manager=tds_manager,
                order=None, #TODO set order
                template=None, #TODO set template
                locations = row['locations'],
                task_type = row['task_type']
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
def load_initial_timelines_to_tds(df, tds_manager):
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

            task = tds_manager.tasks.get(order_name)
            if not task:
                print(f"Warning: task/order '{order_name}' not found in TDS manager; skipping")
                continue

            try:
                resource.insert_task_to_timeline(task, capability, prev_task, generate_travel)
            except ValueError as e:
                print(f"Error appending task '{order_name}' to resource '{res_name}': {e}")

            prev_task = task

        for _, row in group.iterrows():
            order_name = row["order"].lower()
            task = tds_manager.tasks.get(order_name)
            resource.timeline.add_return_stops(task)
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


resources_df, downtimes_df, tasks_df, travel_matrix_dict, order_constraints = load_resources_and_tasks(REQUEST_PATH, TRAVEL_MATRIX_PATH, EPOCH_DATE)
tds = TDSManager(travel_matrix_dict)
add_resources_to_tds(resources_df, tds)
add_downtimes_to_tds(downtimes_df, tds)
add_tasks_to_tds(tasks_df, tds) # not yet assigned just in the system


# init_schedule = schedule_json_to_df(INITIAL_SCHEDULE_PATH)
# load_initial_timelines_to_tds(init_schedule, tds)
# pd_tasks = add_pickup_dropoff(tds)
# schedule_pd_tasks(tds, pd_tasks)
# add_return_home_tasks(tds)
# print(tds.sum_total_travel())
dependent_tasks = schedule_independent_tasks(tds)
for dep_task in dependent_tasks:
    schedule_dependent_task(tds, dep_task)
# display_current_schedule(tds, EPOCH_DATE)
export_schedule_to_csv(tds, EPOCH_DATE)

