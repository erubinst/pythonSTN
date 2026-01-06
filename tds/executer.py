from tds.resource import Resource
from tds.task import Task
from tds.tds_manager import TDSManager
from tds.config import *
from tds.parse import *
from tds.utils import *
from collections import deque


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
                locations = row['locations']
            )
        except ValueError as e:
            print(f"Error creating task '{name}': {e}")
            continue

        task.add_time_window_constraints(row.get('est'), row.get('lft'))
        task.add_duration_constraint(row.get('duration'))

# TODO: implement order constraints loading, order_constraints df return from function created in parse.py
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
        preceding_task.constrain_before(succeeding_task, ("sequence", "order"))
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
# ---------------------------------------------------------


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
        else:
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
            execute_undo_functions(undo_stacks[i])
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
            undo_stack = resource.timeline.try_slot(task, prior_task, capability)
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
                execute_undo_functions(popped_stack)
            
            # Move to next slot
            prior_task_idx += 1
            if prior_task_idx < len(resource.timeline.tasks):
                prior_task = resource.timeline.tasks[prior_task_idx]
            else:
                prior_task = None


def search_through_capability_assignments_with_transport(tds, task):
    """
    Find all possible assignments of task capabilities to resources,
    including transport assignments for dependent resources.
    Returns a list of valid assignment combinations.
    """
    all_assignments = []
    current_assignment = deque()
    undo_stacks = deque()
    transport_assignment = deque()  # Store pickup/dropoff assignments

    capabilities_list = list(task.capabilities)
    # sort capabilities backwards alphabetically
    capabilities_list.sort(reverse=True)
    backtrack_capability_assignments_with_transport(
        tds, task, capabilities_list, 0, current_assignment, undo_stacks, 
        transport_assignment, all_assignments
    )
    
    return all_assignments


def backtrack_capability_assignments_with_transport(
    tds, task, capabilities, capability_idx,
    current_assignment, undo_stacks,
    transport_assignment, all_assignments
):
    if capability_idx == len(capabilities):
        all_assignments.append({
            'capability_assignment': list(current_assignment),
            'transport_assignment': list(transport_assignment),
            'total_travel': tds.sum_total_travel(),
            'total_ride_time': tds.sum_total_ride_time()
        })
        return True

    found_any = False
    capability = capabilities[capability_idx]
    new_task_lst = task.start.ub

    for resource in tds.resources.values():
        if capability not in resource.capabilities:
            continue

        if 'traveler' not in resource.capabilities:
            tasks_to_iterate = [
                t for t in resource.timeline.tasks
                if not t.name.startswith('pickup_from_')
                and not t.name.startswith('dropoff_at_')
            ]
        else:
            tasks_to_iterate = resource.timeline.tasks

        for i, prior_task in enumerate(tasks_to_iterate):
            if prior_task.name.endswith('_footer'):
                break
            if prior_task.end.lb > new_task_lst:
                break

            removal_undo_stack = None
            if 'traveler' not in resource.capabilities:
                next_task = tasks_to_iterate[i + 1] if i + 1 < len(tasks_to_iterate) else None
                if next_task:
                    removal_undo_stack = remove_existing_transport_between(
                        resource.timeline, prior_task, next_task
                    )

            undo_stack = resource.timeline.try_slot(task, prior_task, capability)
            if not undo_stack:
                if removal_undo_stack:
                    restore_removed_transport(removal_undo_stack)
                continue

            current_assignment.append((resource, prior_task, capability))
            undo_stacks.append(undo_stack)

            # ---- traveler: no transport ----
            if 'traveler' in resource.capabilities:
                found_any |= backtrack_capability_assignments_with_transport(
                    tds, task, capabilities, capability_idx + 1,
                    current_assignment, undo_stacks,
                    transport_assignment, all_assignments
                )
            else:
                # ---- transport required ----
                tl = resource.timeline.tasks
                idx = tl.index(task)

                next_task = None
                for t in tl[idx + 1:]:
                    if not t.name.startswith('pickup_from_') and not t.name.startswith('dropoff_at_'):
                        next_task = t
                        break
                if not next_task:
                    goto_cleanup = True
                else:
                    goto_cleanup = False

                if not goto_cleanup:
                    need_before = prior_task.locations[-1] != task.locations[0]
                    need_after = task.locations[-1] != next_task.locations[0]

                    pickup1, dropoff1, cleanup1 = resource.timeline.generate_possible_pickup_dropoff(prior_task, task)
                    pickup2, dropoff2, cleanup2 = resource.timeline.generate_possible_pickup_dropoff(task, next_task)

                    # First, try placing pickup/dropoff on resource's own timeline to check precedence feasibility
                    resource_undo_stack = deque()
                    if need_before:
                        ru1 = resource.timeline.try_slot(pickup1, prior_task, f'{resource.name}_presence')
                        if not ru1:
                            if cleanup1:
                                execute_undo_functions(cleanup1)
                            if cleanup2:
                                execute_undo_functions(cleanup2)
                            continue
                        resource_undo_stack.append(ru1)

                        rd1 = resource.timeline.try_slot(dropoff1, pickup1, f'{resource.name}_presence')
                        if not rd1:
                            execute_undo_functions(ru1)
                            if cleanup1:
                                execute_undo_functions(cleanup1)
                            if cleanup2:
                                execute_undo_functions(cleanup2)
                            continue
                        resource_undo_stack.append(rd1)

                    if need_after:
                        ru2 = resource.timeline.try_slot(pickup2, task, f'{resource.name}_presence')
                        if not ru2:
                            for undo in reversed(resource_undo_stack):
                                execute_undo_functions(undo)
                            if cleanup1:
                                execute_undo_functions(cleanup1)
                            if cleanup2:
                                execute_undo_functions(cleanup2)
                            continue
                        resource_undo_stack.append(ru2)

                        rd2 = resource.timeline.try_slot(dropoff2, pickup2, f'{resource.name}_presence')
                        if not rd2:
                            for undo in reversed(resource_undo_stack):
                                execute_undo_functions(undo)
                            if cleanup1:
                                execute_undo_functions(cleanup1)
                            if cleanup2:
                                execute_undo_functions(cleanup2)
                            continue
                        resource_undo_stack.append(rd2)

                    # Now explore transporter options
                    for before_resource in tds.resources.values():
                        if need_before and 'traveler' not in before_resource.capabilities:
                            continue
                        if not need_before:
                            before_resource = None

                        for after_resource in tds.resources.values():
                            if need_after and 'traveler' not in after_resource.capabilities:
                                continue
                            if not need_after:
                                after_resource = None

                            # try placing before transport
                            if need_before:
                                for p in before_resource.timeline.tasks:
                                    if p.end.lb > pickup1.start.ub:
                                        break
                                    pu1 = before_resource.timeline.try_slot(pickup1, p, 'transport')
                                    if not pu1:
                                        continue

                                    for q in before_resource.timeline.tasks[
                                        before_resource.timeline.tasks.index(pickup1):
                                    ]:
                                        if q.end.lb > dropoff1.start.ub:
                                            break
                                        du1 = before_resource.timeline.try_slot(dropoff1, q, 'transport')
                                        if not du1:
                                            continue

                                        # try after transport
                                        if need_after:
                                            for p2 in after_resource.timeline.tasks:
                                                if p2.end.lb > pickup2.start.ub:
                                                    break
                                                pu2 = after_resource.timeline.try_slot(pickup2, p2, 'transport')
                                                if not pu2:
                                                    continue

                                                for q2 in after_resource.timeline.tasks[
                                                    after_resource.timeline.tasks.index(pickup2):
                                                ]:
                                                    if q2.end.lb > dropoff2.start.ub:
                                                        break
                                                    du2 = after_resource.timeline.try_slot(dropoff2, q2, 'transport')
                                                    if not du2:
                                                        continue

                                                    transport_assignment.append({
                                                        'before_resource': before_resource,
                                                        'before_pickup_task': pickup1,
                                                        'before_dropoff_task': dropoff1,
                                                        'before_pickup_prior_task': p,
                                                        'before_dropoff_prior_task': q,
                                                        'before_pickup_undo': pu1,
                                                        'before_dropoff_undo': du1,
                                                        'after_resource': after_resource,
                                                        'after_pickup_task': pickup2,
                                                        'after_dropoff_task': dropoff2,
                                                        'after_pickup_prior_task': p2,
                                                        'after_dropoff_prior_task': q2,
                                                        'after_pickup_undo': pu2,
                                                        'after_dropoff_undo': du2,
                                                        'removal_undo_stack': removal_undo_stack,
                                                    })

                                                    found_any |= backtrack_capability_assignments_with_transport(
                                                        tds, task, capabilities, capability_idx + 1,
                                                        current_assignment, undo_stacks,
                                                        transport_assignment, all_assignments
                                                    )

                                                    transport_assignment.pop()
                                                    execute_undo_functions(du2)

                                                execute_undo_functions(pu2)

                                        else:
                                            transport_assignment.append({
                                                'before_resource': before_resource,
                                                'before_pickup_task': pickup1,
                                                'before_dropoff_task': dropoff1,
                                                'before_pickup_prior_task': p,
                                                'before_dropoff_prior_task': q,
                                                'before_pickup_undo': pu1,
                                                'before_dropoff_undo': du1,
                                                'after_resource': None,
                                                'after_pickup_task': None,
                                                'after_dropoff_task': None,
                                                'after_pickup_prior_task': None,
                                                'after_dropoff_prior_task': None,
                                                'after_pickup_undo': None,
                                                'after_dropoff_undo': None,
                                                'removal_undo_stack': removal_undo_stack,
                                            })

                                            found_any |= backtrack_capability_assignments_with_transport(
                                                tds, task, capabilities, capability_idx + 1,
                                                current_assignment, undo_stacks,
                                                transport_assignment, all_assignments
                                            )

                                            transport_assignment.pop()

                                        execute_undo_functions(du1)
                                    execute_undo_functions(pu1)

                    # Clean up resource's timeline pickup/dropoff tasks
                    for undo in reversed(resource_undo_stack):
                        execute_undo_functions(undo)

                    if cleanup1:
                        execute_undo_functions(cleanup1)
                    if cleanup2:
                        execute_undo_functions(cleanup2)

            execute_undo_functions(undo_stacks.pop())
            current_assignment.pop()
            if removal_undo_stack:
                restore_removed_transport(removal_undo_stack)

    return found_any





def remove_existing_transport_between(timeline, prior_task, next_task, save_task=True):
    """
    Remove existing pickup/dropoff transport tasks between prior_task and next_task.
    Returns undo stack to restore them.
    """
    removal_undo_stack = deque()
    
    prior_idx = timeline.tasks.index(prior_task)
    next_idx = timeline.tasks.index(next_task)
    
    tasks_to_remove = []
    
    # Find all pickup/dropoff tasks between prior_task and next_task
    for i in range(prior_idx, next_idx):
        t = timeline.tasks[i]
        if t.name.startswith('pickup_from_') or t.name.startswith('dropoff_at_'):
            tasks_to_remove.append(t)
    
    # STEP 1: Collect all position information BEFORE removing anything
    tasks_with_positions = []
    for removed_task in tasks_to_remove:
        task_positions = []
        
        # Find all timelines this task appears on and store its prior task
        for resource in timeline.tds.resources.values():
            resource_task_lst = resource.timeline.list_task_names()
            if removed_task.name in resource_task_lst:
                task_idx = resource_task_lst.index(removed_task.name)
                resource_task = resource.timeline.tasks[task_idx] # must save this
                # Get the task that comes before this one
                prior_task_for_restore = resource.timeline.tasks[task_idx - 1] if task_idx > 0 else None
                # Store (resource, prior_task) for restoration
                task_positions.append((resource, prior_task_for_restore))
        
        tasks_with_positions.append((removed_task, task_positions))
    
    # STEP 2: Now remove all tasks and update names
    for removed_task, task_positions in tasks_with_positions:
        if save_task:
            # Remove from all timelines
            for resource, _ in task_positions:
                resource.remove_task_from_timeline(removed_task)
            
            original_name = removed_task.name
            removed_task.update_task_name(original_name + '_temp')
            # Store undo info: (task, original_name, list of (resource, prior_task) tuples)
            removal_undo_stack.append((removed_task, original_name, task_positions))
        else:
            for resource, _ in task_positions:
                print(f"Fully deleting {removed_task.name} from {resource.name}")
                resource.remove_task_from_timeline(removed_task)
            removed_task.delete_task()

    return removal_undo_stack


def restore_removed_transport(removal_undo_stack):
    """
    Restore transport tasks that were removed.
    Must restore names first, then restore to timelines in correct order.
    """
    # Collect all items to restore
    items_to_restore = []
    while removal_undo_stack:
        items_to_restore.append(removal_undo_stack.pop())
    
    # STEP 1: Restore all names first (in reverse order)
    for task, original_name, task_positions in reversed(items_to_restore):
        task.update_task_name(original_name)
    
    # STEP 2: Restore to timelines in reverse order (last removed = first restored)
    for task, original_name, task_positions in reversed(items_to_restore):
        # Re-insert into timelines using prior_task
        for resource, prior_task_for_restore in task_positions:
            if 'traveler' not in resource.capabilities:
                capability = f'{resource.name}_presence'
            else:
                capability = 'transport'
            
            if prior_task_for_restore is not None and prior_task_for_restore in resource.timeline.tasks:
                resource.insert_task_to_timeline(task, capability, prev_task=prior_task_for_restore)
            else:
                print(f"Warning: Could not find prior task for {task.name}")




def schedule_dependent_task(tds, task):
    print(f'scheduling {task.name}')
    assignments = search_through_capability_assignments_with_transport(tds, task)
    # deduplicate assignments
    for i in range(len(assignments)-1, -1, -1):
        for j in range(i-1, -1, -1):
            if assignments[i]['capability_assignment'] == assignments[j]['capability_assignment'] and assignments[i]['transport_assignment'] == assignments[j]['transport_assignment']:
                print(f'Deduplicating assignments for {task.name} at indices {i} and {j}')
                assignments.pop(i)
                break
    # export assignments to file
    assignments_file = f'assignments_{task.name}.txt'
    with open(assignments_file, 'w') as f:
        for assignment in assignments:
            f.write(f'Assignment with total travel {assignment["total_travel"]}:\n')
            f.write(f'Assignment with total ride time {assignment["total_ride_time"]}:\n')
            f.write('Capability Assignments:\n')
            for cap_assign in assignment['capability_assignment']:
                resource = cap_assign[0]
                prior_task = cap_assign[1]
                capability = cap_assign[2]
                f.write(f'  Resource: {resource.name}, Prior Task: {prior_task.name}, Capability: {capability}\n')
            f.write('Transport Assignments:\n')
            for transport_assign in assignment['transport_assignment']:
                before_resource = transport_assign['before_resource']
                before_pickup_task = transport_assign['before_pickup_task']
                before_dropoff_task = transport_assign['before_dropoff_task']
                before_pickup_prior_task = transport_assign['before_pickup_prior_task']
                before_dropoff_prior_task = transport_assign['before_dropoff_prior_task']
                f.write(f'  Before Resource: {before_resource.name}, Pickup Task: {before_pickup_task.name}, Dropoff Task: {before_dropoff_task.name}, '
                        f'Pickup Prior Task: {before_pickup_prior_task.name}, Dropoff Prior Task: {before_dropoff_prior_task.name}\n')
                after_resource = transport_assign['after_resource']
                if after_resource is not None:
                    after_pickup_task = transport_assign['after_pickup_task']
                    after_dropoff_task = transport_assign['after_dropoff_task']
                    after_pickup_prior_task = transport_assign['after_pickup_prior_task']
                    after_dropoff_prior_task = transport_assign['after_dropoff_prior_task']
                    f.write(f'  After Resource: {after_resource.name}, Pickup Task: {after_pickup_task.name}, Dropoff Task: {after_dropoff_task.name}, '
                            f'Pickup Prior Task: {after_pickup_prior_task.name}, Dropoff Prior Task: {after_dropoff_prior_task.name}\n')
            f.write('---\n')
    # # check for duplicate assignments
    # for i in range(len(assignments)):
    #     for j in range(i + 1, len(assignments)):
    #         if assignments[i]['capability_assignment'] == assignments[j]['capability_assignment'] and assignments[i]['transport_assignment'] == assignments[j]['transport_assignment']:
    #             print(f'Warning: Duplicate assignments found for {task.name} at indices {i} and {j}')
    # print number of minimal travel assignments
    if not assignments:
        print(f'No possible assignments for {task.name}')
    else:
        min_travel = min(assignments, key=lambda x: x['total_travel'])
        num_min_travel = sum(1 for a in assignments if a['total_travel'] == min_travel['total_travel'])
        print(f'Found {num_min_travel} assignments with minimal travel {min_travel["total_travel"]} for {task.name}')

        # Filter to assignments with minimal travel
        min_travel_assignments = [a for a in assignments if a['total_travel'] == min_travel['total_travel']]

        # Break ties by selecting minimal ride time
        assignment = max(min_travel_assignments, key=lambda x: x['total_ride_time'])
        print(f'Selected assignment with ride time {assignment["total_ride_time"]}')
        for capability_assignment in assignment['capability_assignment']:
            resource = capability_assignment[0]
            prior_task = capability_assignment[1]
            capability = capability_assignment[2]

            if 'traveler' not in resource.capabilities:
                first_non_transport_task = resource.timeline.tasks[-1]
                prior_task_index = resource.timeline.tasks.index(prior_task)
                for t_task in resource.timeline.tasks[prior_task_index+1:]:
                    if 'pickup' not in t_task.name.lower() and 'dropoff' not in t_task.name.lower():
                        first_non_transport_task = t_task
                        break
                remove_existing_transport_between(resource.timeline, prior_task, first_non_transport_task, save_task=False)
            print(f'Assigning {task.name} to {resource.name} after {prior_task.name}')
            resource.insert_task_to_timeline(task, capability, prior_task)
            # If this resource needs transport, find and insert the associated transport tasks
            if 'traveler' not in resource.capabilities:
                # Find transport assignments for this capability
                # The transport tasks are named: pickup_from_<prior_location>_<resource>
                #                                dropoff_at_<task_name>_<resource>
                for transport_assign in assignment['transport_assignment']:
                    before_resource = transport_assign['before_resource']
                    before_pickup_task = transport_assign['before_pickup_task']
                    before_dropoff_task = transport_assign['before_dropoff_task']

                    # Match by checking if the dropoff task name contains this task's name and resource
                    # Example: dropoff_at_preparecakeingredients_annie
                    if task.name.lower() in before_dropoff_task.name.lower() and resource.name.lower() in before_dropoff_task.name.lower():
                        # Found the transport for this capability
                        before_pickup_prior_task = transport_assign['before_pickup_prior_task']
                        before_dropoff_prior_task = transport_assign['before_dropoff_prior_task']
                        resource.timeline.add_pickup_dropoffs(task)
                        next_task = resource.timeline.tasks[resource.timeline.tasks.index(task)+1]
                        resource.timeline.add_pickup_dropoffs(next_task)

                        print(f'Assigning transport task {before_pickup_task.name} to {before_resource.name} after {before_pickup_prior_task.name}')
                        before_resource.insert_task_to_timeline(before_pickup_task, 'transport', before_pickup_prior_task)

                        print(f'Assigning transport task {before_dropoff_task.name} to {before_resource.name} after {before_dropoff_prior_task.name}')
                        before_resource.insert_task_to_timeline(before_dropoff_task, 'transport', before_dropoff_prior_task)

                        # Handle "after" transport if it exists (transport after this task completes)
                        after_resource = transport_assign['after_resource']
                        if after_resource is not None:
                            after_pickup_task = transport_assign['after_pickup_task']
                            after_dropoff_task = transport_assign['after_dropoff_task']
                            after_pickup_prior_task = transport_assign['after_pickup_prior_task']
                            after_dropoff_prior_task = transport_assign['after_dropoff_prior_task']

                            print(f'Assigning transport task {after_pickup_task.name} to {after_resource.name} after {after_pickup_prior_task.name}')
                            after_resource.insert_task_to_timeline(after_pickup_task, 'transport', after_pickup_prior_task)
                            after_resource.insert_task_to_timeline(after_dropoff_task, 'transport', after_dropoff_prior_task)

                        break  # Found and inserted transport for this capability, move to next


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


resources_df, tasks_df, travel_matrix_dict, order_constraints = load_resources_and_tasks(REQUEST_PATH, TRAVEL_MATRIX_PATH)
tds = TDSManager(travel_matrix_dict)
add_resources_to_tds(resources_df, tds)
add_tasks_to_tds(tasks_df, tds) # not yet assigned just in the system


# init_schedule = schedule_json_to_df(INITIAL_SCHEDULE_PATH)
# load_initial_timelines_to_tds(init_schedule, tds)
# pd_tasks = add_pickup_dropoff(tds)
# schedule_pd_tasks(tds, pd_tasks)
# add_return_home_tasks(tds)
# print(tds.sum_total_travel())
# dependent_tasks = schedule_independent_tasks(tds)
# for dep_task in dependent_tasks:
    # schedule_dependent_task(tds, dep_task)
# display_current_schedule(tds, EPOCH_DATE)

