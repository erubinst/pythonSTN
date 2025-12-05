from resource import Resource
from task import Task
from tds_manager import TDSManager
from config import *
from parse import *


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
        capabilities = [c.strip() for c in row["required_capabilities"].split(",")] if row["required_capabilities"] else []
        try:
            task = Task(
                name=name,
                capabilities=capabilities,
                tds_manager=tds_manager,
                order=None, #TODO set order
                template=None, #TODO set template
                locations = row['locations'],
                assigned_resources=None  # no assignments yet
            )
        except ValueError as e:
            print(f"Error creating task '{name}': {e}")
            continue

        task.add_time_window_constraints(row.get('est'), row.get('lft'))
        task.add_duration_constraint(row.get('duration'))

# TODO: implement order constraints loading, order_constraints df return from function created in parse.py
# def add_order_constraints_to_tds(order_constraints_df, tds_manager):
#     """
#     Add order constraints from DataFrame to TDS manager.
#     Parameters:
#         order_constraints_df (pd.DataFrame): columns = ['preceding_task', 'succeeding_task']
#         tds_manager: initialized TDS manager object
#     """
#     for _, row in order_constraints_df.iterrows():
#         preceding_task_name = row['preceding_task']
#         succeeding_task_name = row['succeeding_task']
#         preceding_task = tds_manager.tasks.get(preceding_task_name)
#         succeeding_task = tds_manager.tasks.get(succeeding_task_name)
#         if not preceding_task or not succeeding_task:
#             print(f"Warning: One of the tasks '{preceding_task_name}' or '{succeeding_task_name}' not found; skipping constraint")
#             continue
#         # Add constraint to TDS manager's STN
#         task.add_constraint( -- take a look at this function 
# one of the parameters is constraint_type - ("all", "sequence")


def schedule_independent_tasks(tds):
    # scheduling driver tasks - go through all tasks and try to schedule onto driver.  If not able to skip
    driver_capabilities = set()
    for resource in tds.resources.values():
        if 'traveler' in resource.capabilities:
            driver_capabilities.update(resource.capabilities)
    dependent_tasks = []
    for task in tds.tasks.values():
        if task.name.endswith('_header') or task.name.endswith('_footer'):
            continue
        has_missing_capability = False
        for cap in task.capabilities:
            if cap not in driver_capabilities:
                dependent_tasks.append(task)
                has_missing_capability = True
                break  # Break out of capability loop
        if has_missing_capability:
            continue  # Skip to next task

        assignments = search_through_capability_assignments(tds, task)
        if assignments:
            best_assignment, min_travel = min(assignments, key=lambda x: x[1])
            for assignment in best_assignment:
                resource, prior_task, capability = assignment
                resource.insert_task_to_timeline(task, capability, prev_task=prior_task, generate_travel=True)
        dependent_tasks.append(task)
    return dependent_tasks





def search_through_capability_assignments(tds, task):
    """
    Find all possible assignments of task capabilities to resources.
    Returns a list of valid assignment combinations.
    """
    all_assignments = []
    current_assignment = []
    undo_stacks = []

    capabilities_list = list(task.capabilities)
    backtrack_capability_assignments(
        tds, task, capabilities_list, 0, current_assignment, undo_stacks, all_assignments
    )
    
    return all_assignments


def backtrack_capability_assignments(tds, task, capabilities, capability_idx, current_assignment, undo_stacks, all_assignments):
    """
    Recursively try all combinations of resource assignments.
    Args:
        tds: manager
        task: Task to assign
        capability_idx: Index of current capability being assigned
        current_assignment: List of (resource, prior_task) tuples for capabilities assigned so far
        undo_stacks: List of undo_stacks corresponding to current_assignment
        all_assignments: List to accumulate all valid assignments
    """
    # Base case: all capabilities have been assigned
    if capability_idx == len(capabilities):
        total_travel = tds.sum_total_travel()
        # Found a valid complete assignment - store with travel cost
        all_assignments.append((current_assignment.copy(), total_travel))
        # Undo this assignment to try other combinations
        # TODO: make undo_stacks a stack of stacks
        for i in range(len(undo_stacks) - 1, -1, -1):
            resource = current_assignment[i][0]
            resource.timeline.execute_undo_functions(undo_stacks[i])
        return
    
    # Try assigning current capability to each compatible resource
    capability = capabilities[capability_idx]
    new_task_lst = task.start.ub
    
    for resource in tds.resources.values():
        if capability not in resource.capabilities:
            continue
        
        starting_task = resource.timeline.tasks[0]
        prior_task = starting_task
        prior_task_idx = resource.timeline.tasks.index(prior_task)
        
        while prior_task is not None and not prior_task.name.endswith('_footer'):
            prior_eft = prior_task.end.lb
            
            if prior_eft > new_task_lst:
                break
            
            # Try placing task after prior_task
            undo_stack = resource.timeline.try_slot(task, prior_task)
            
            if undo_stack:
                current_assignment.append((resource, prior_task, capability))
                undo_stacks.append(undo_stack)
                
                # Recursively try next capability
                backtrack_capability_assignments(
                    tds, task, capabilities, capability_idx + 1, current_assignment, undo_stacks, all_assignments
                )
                
                # Backtrack: remove this assignment for next iteration
                current_assignment.pop()
                popped_stack = undo_stacks.pop()
                resource.timeline.execute_undo_functions(popped_stack)
            
            # Move to next slot
            prior_task_idx += 1
            if prior_task_idx < len(resource.timeline.tasks):
                prior_task = resource.timeline.tasks[prior_task_idx]
            else:
                prior_task = None


# Routine for starting from given schedule like CP model
def load_initial_timelines_to_tds(df, tds_manager):
    """
    Append tasks to each resource's timeline using a schedule DataFrame.

    Parameters:
        df (DataFrame): DataFrame produced by schedule_json_to_df()
        tds_manager (TDSManager): TDS manager with tasks & resources loaded
    """
    # go through groups by resource name
    for res_name, group in df.groupby("resourceName"):
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


def add_pickup_dropoff(tds):
    pd_tasks = []
    for resource in tds.resources.values():
        original_task_list = list(resource.timeline.tasks)
        for task in original_task_list:
            # skip header
            if task.name == f'header_{resource.name}':
                continue
            if 'traveler' not in resource.capabilities:
                pickup, dropoff = resource.timeline.add_pickup_dropoffs(task)
                if pickup is not None and dropoff is not None:
                    pd_tasks.append([pickup, dropoff])

    return pd_tasks

def schedule_pd_tasks(tds, pd_tasks):
    first_time = True
    for transports in pd_tasks:
        options = []
        for resource in tds.resources.values():
            if 'traveler' in resource.capabilities:
                slots = resource.timeline.map_feasible_slots_linked_tasks(transports[0], transports[1])
                for slot in slots:
                    slot['resource'] = resource
                    options.append(slot)
        current_travel = tds.sum_total_travel()
        if first_time == True:
            print(f'Options for scheduling pickup/dropoff {transports[0].name}, {transports[1].name}:')
            for option in options:
                print(f"Resource: {option['resource'].name}\nTotal Additional Travel: {option['total_travel'] - current_travel},\n"
                      f"Pickup after: {option['task1_prior_task'].name},\nDropoff after: {option['task2_prior_task'].name}")
                print('---')
        best = min(options, key=lambda x: x["total_travel"])
        # get resource in best
        resource = best['resource']
        resource.insert_task_to_timeline(transports[0], 'transport', prev_task=best['task1_prior_task'], generate_travel=True)
        resource.insert_task_to_timeline(transports[1], 'transport', prev_task=best['task2_prior_task'], generate_travel=True)
        first_time = False

def add_return_home_tasks(tds):
    for resource in tds.resources.values():
        if 'traveler' in resource.capabilities:
            for task in resource.timeline.tasks:
                # if header or footer, skip
                if task.name.endswith('_header') or task.name.endswith('_footer'):
                    continue
                resource.timeline.add_return_stops(task)


resources_df, tasks_df, travel_matrix_dict = load_resources_and_tasks(REQUEST_PATH, TRAVEL_MATRIX_PATH)
tds = TDSManager(travel_matrix_dict)
add_resources_to_tds(resources_df, tds)
add_tasks_to_tds(tasks_df, tds) # not yet assigned just in the system
# init_schedule = schedule_json_to_df(INITIAL_SCHEDULE_PATH)
# load_initial_timelines_to_tds(init_schedule, tds)
# pd_tasks = add_pickup_dropoff(tds)
# schedule_pd_tasks(tds, pd_tasks)
# add_return_home_tasks(tds)
# print(tds.sum_total_travel())
dependent_tasks = schedule_independent_tasks(tds)
display_current_schedule(tds, EPOCH_DATE)

