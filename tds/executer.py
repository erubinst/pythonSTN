from tds.resource import Resource
from tds.task import Task
from tds.tds_manager import TDSManager
from tds.config import *
from tds.parse import *
from tds.utils import *
from collections import deque
import inspect


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
    backtrack_capability_assignments_with_transport(
        tds, task, capabilities_list, 0, current_assignment, undo_stacks, 
        transport_assignment, all_assignments
    )
    
    return all_assignments


def backtrack_capability_assignments_with_transport(tds, task, capabilities, capability_idx, 
                                                    current_assignment, undo_stacks, 
                                                    transport_assignment, all_assignments):
    """
    Recursively try all combinations of resource and transport assignments.
    """
    # Base case: all capabilities have been assigned
    if capability_idx == len(capabilities):
        total_travel = tds.sum_total_travel()
        # Found a valid complete assignment - store with travel cost
        all_assignments.append({
            'capability_assignment': list(current_assignment),
            'transport_assignment': list(transport_assignment),
            'total_travel': total_travel
        })
        return
        
        # Undo transport assignments (pop from stack)
        temp_transport = deque()
        while transport_assignment:
            transport_info = transport_assignment.pop()
            temp_transport.append(transport_info)
            # Undo after-task transport (pickup2/dropoff2)
            if transport_info['after_dropoff_undo']:
                execute_undo_functions(transport_info['after_dropoff_undo'])
            if transport_info['after_pickup_undo']:
                execute_undo_functions(transport_info['after_pickup_undo'])
            # Undo before-task transport (pickup1/dropoff1)
            if transport_info['before_dropoff_undo']:
                execute_undo_functions(transport_info['before_dropoff_undo'])
            if transport_info['before_pickup_undo']:
                execute_undo_functions(transport_info['before_pickup_undo'])
            # DO NOT restore removed transport here
        
        # Restore transport_assignment for further exploration
        while temp_transport:
            transport_assignment.append(temp_transport.pop())
        
        # Undo capability assignments
        temp_assignment = deque()
        temp_undo = deque()
        while current_assignment:
            assignment = current_assignment.pop()
            undo_stack = undo_stacks.pop()
            temp_assignment.append(assignment)
            temp_undo.append(undo_stack)
            resource = assignment[0]
            execute_undo_functions(undo_stack)
        
        # Restore for further exploration
        while temp_assignment:
            current_assignment.append(temp_assignment.pop())
            undo_stacks.append(temp_undo.pop())
        
        return
    
    # Try assigning current capability to each compatible resource
    capability = capabilities[capability_idx]
    new_task_lst = task.start.ub
    
    for resource in tds.resources.values():
        if capability not in resource.capabilities:
            continue
        
        # For non-traveler resources, build list of non-transport tasks only
        if 'traveler' not in resource.capabilities:
            non_transport_tasks = [t for t in resource.timeline.tasks 
                                  if not t.name.startswith('pickup_from_') 
                                  and not t.name.startswith('dropoff_at_')]
            tasks_to_iterate = non_transport_tasks
        else:
            tasks_to_iterate = resource.timeline.tasks
        
        prior_task_list_idx = 0
        
        while prior_task_list_idx < len(tasks_to_iterate):
            prior_task = tasks_to_iterate[prior_task_list_idx]
            
            if prior_task.name.endswith('_footer'):
                break
            
            prior_eft = prior_task.end.lb
            
            if prior_eft > new_task_lst:
                break
            
            # CRITICAL: For non-traveler resources, remove existing transport BEFORE trying insertion
            removal_undo_stack = None
            if 'traveler' not in resource.capabilities:
                # Get the next non-transport task
                next_task = tasks_to_iterate[prior_task_list_idx + 1] if prior_task_list_idx + 1 < len(tasks_to_iterate) else None
                
                if next_task:
                    # Remove existing pickup/dropoffs between prior_task and next_task
                    # This clears space for trying to insert the new task
                    removal_undo_stack = remove_existing_transport_between(
                        resource.timeline, prior_task, next_task
                    )
            
            undo_stack = resource.timeline.try_slot(task, prior_task, capability)
            
            if undo_stack:
                current_assignment.append((resource, prior_task, capability))
                undo_stacks.append(undo_stack)
                
                # Check if this resource needs transport (doesn't have 'traveler' capability)
                if 'traveler' not in resource.capabilities:
                    # Get the next non-transport task after our newly inserted task
                    current_task_timeline_idx = resource.timeline.tasks.index(task)
                    next_task = None
                    for i in range(current_task_timeline_idx + 1, len(resource.timeline.tasks)):
                        t = resource.timeline.tasks[i]
                        if not t.name.startswith('pickup_from_') and not t.name.startswith('dropoff_at_'):
                            next_task = t
                            break
                    
                    if next_task is None:
                        # Can't proceed without a valid next task
                        current_assignment.pop()
                        popped_stack = undo_stacks.pop()
                        execute_undo_functions(popped_stack)
                        # Restore the removed transport AFTER undoing task insertion
                        if removal_undo_stack:
                            restore_removed_transport(removal_undo_stack)
                        prior_task_list_idx += 1
                        continue

                    prior_task_location = prior_task.locations[-1]
                    task_start_location = task.locations[0]
                    need_before_transport = (prior_task_location != task_start_location)

                    task_end_location = task.locations[-1]
                    next_task_location = next_task.locations[0]
                    need_after_transport = (task_end_location != next_task_location)
                    
                    if not need_after_transport and not need_before_transport:
                        # No transport needed, just recurse directly
                        backtrack_capability_assignments_with_transport(
                            tds, task, capabilities, capability_idx + 1, 
                            current_assignment, undo_stacks, 
                            transport_assignment, all_assignments
                        )
                        # Backtrack
                        current_assignment.pop()
                        popped_stack = undo_stacks.pop()
                        execute_undo_functions(popped_stack)
                        if removal_undo_stack:
                            restore_removed_transport(removal_undo_stack)
                        prior_task_list_idx += 1
                        continue
                    
                    # Generate NEW pickup/dropoff tasks for the new ordering
                    pickup1_task, dropoff1_task, before_cleanup_undo = resource.timeline.generate_possible_pickup_dropoff(prior_task, task)
                    pickup2_task, dropoff2_task, after_cleanup_undo = resource.timeline.generate_possible_pickup_dropoff(task, next_task)
                    
                    # Find all transport options (only for where needed)
                    before_transport_options = find_transport_options(tds, pickup1_task, dropoff1_task) if need_before_transport else [None]
                    after_transport_options = find_transport_options(tds, pickup2_task, dropoff2_task) if need_after_transport else [None]
                    
                    found_valid_transport = False

                    
                    if before_transport_options and after_transport_options:
                        # Try all combinations of before and after transport
                        for before_option in before_transport_options:
                            before_pickup_undo = None
                            before_dropoff_undo = None
                            
                            if need_before_transport and before_option:
                                before_resource = before_option['resource']
                                
                                if before_option['pickup_prior_task'] not in before_resource.timeline.tasks:
                                    continue

                                before_pickup_undo = before_resource.timeline.try_slot(
                                    pickup1_task, before_option['pickup_prior_task'], 'transport'
                                )
                                
                                if not before_pickup_undo:
                                    continue
                                
                                if before_option['pickup_prior_task'] not in before_resource.timeline.tasks:
                                    if before_pickup_undo:
                                        execute_undo_functions(before_pickup_undo)
                                    continue

                                before_dropoff_undo = before_resource.timeline.try_slot(
                                    dropoff1_task, before_option['dropoff_prior_task'], 'transport'
                                )
                                
                                if not before_dropoff_undo:
                                    execute_undo_functions(before_pickup_undo)
                                    continue
                            
                            for after_option in after_transport_options:
                                after_pickup_undo = None
                                after_dropoff_undo = None
                                
                                if need_after_transport and after_option:
                                    after_resource = after_option['resource']
                                    
                                    if after_option['pickup_prior_task'] not in after_resource.timeline.tasks:
                                        continue
                                    after_pickup_undo = after_resource.timeline.try_slot(
                                        pickup2_task, after_option['pickup_prior_task'], 'transport'
                                    )
                                    
                                    if not after_pickup_undo:
                                        continue
                                    
                                    if after_option['dropoff_prior_task'] not in after_resource.timeline.tasks:
                                        if after_pickup_undo:
                                            execute_undo_functions(after_pickup_undo)
                                        continue
                                    after_dropoff_undo = after_resource.timeline.try_slot(
                                        dropoff2_task, after_option['dropoff_prior_task'], 'transport'
                                    )
                                    
                                    if not after_dropoff_undo:
                                        execute_undo_functions(after_pickup_undo)
                                        continue
                                
                                found_valid_transport = True
                                # Store everything including removal undo stack
                                transport_assignment.append({
                                    'before_resource': before_option['resource'] if (need_before_transport and before_option) else None,
                                    'before_pickup_prior_task': before_option['pickup_prior_task'] if (need_before_transport and before_option) else None,
                                    'before_dropoff_prior_task': before_option['dropoff_prior_task'] if (need_before_transport and before_option) else None,
                                    'before_pickup_undo': before_pickup_undo,
                                    'before_dropoff_undo': before_dropoff_undo,
                                    'before_pickup_task': pickup1_task,
                                    'before_dropoff_task': dropoff1_task,
                                    'after_resource': after_option['resource'] if (need_after_transport and after_option) else None,
                                    'after_pickup_prior_task': after_option['pickup_prior_task'] if (need_after_transport and after_option) else None,
                                    'after_dropoff_prior_task': after_option['dropoff_prior_task'] if (need_after_transport and after_option) else None,
                                    'after_pickup_undo': after_pickup_undo,
                                    'after_dropoff_undo': after_dropoff_undo,
                                    'after_pickup_task': pickup2_task,
                                    'after_dropoff_task': dropoff2_task,
                                    'removal_undo_stack': removal_undo_stack  # Store for restoration
                                })
                                
                                # Recursively try next capability
                                backtrack_capability_assignments_with_transport(
                                    tds, task, capabilities, capability_idx + 1, 
                                    current_assignment, undo_stacks, 
                                    transport_assignment, all_assignments
                                )
                                
                                # Backtrack transport assignment
                                # Step 5: Undo new transport
                                info = transport_assignment.pop()
                                if info['after_dropoff_undo']:
                                    execute_undo_functions(info['after_dropoff_undo'])
                                if info['after_pickup_undo']:
                                    execute_undo_functions(info['after_pickup_undo'])
                                if info['before_dropoff_undo']:
                                    execute_undo_functions(info['before_dropoff_undo'])
                                if info['before_pickup_undo']:
                                    execute_undo_functions(info['before_pickup_undo'])
                                # Don't restore old transport yet - task is still in timeline
                            
                            # Clean up before transport if it was placed
                            if before_dropoff_undo:
                                execute_undo_functions(before_dropoff_undo)
                            if before_pickup_undo:
                                execute_undo_functions(before_pickup_undo)
                    
                    # Clean up the generated pickup/dropoff tasks
                    execute_undo_functions(before_cleanup_undo)
                    execute_undo_functions(after_cleanup_undo)
                    
                    # If no valid transport found, we need to undo and restore now
                    if not found_valid_transport:
                        # No valid transport - this slot doesn't work
                        current_assignment.pop()
                        popped_stack = undo_stacks.pop()
                        execute_undo_functions(popped_stack)
                        # NOW restore the removed transport (after undoing task)
                        if removal_undo_stack:
                            restore_removed_transport(removal_undo_stack)
                        prior_task_list_idx += 1
                        continue
                    
                else:
                    # Resource has 'traveler' capability, no transport needed
                    backtrack_capability_assignments_with_transport(
                        tds, task, capabilities, capability_idx + 1, 
                        current_assignment, undo_stacks, 
                        transport_assignment, all_assignments
                    )
                
                # Backtrack: remove this capability assignment
                # Step 6: Undo task
                current_assignment.pop()
                popped_stack = undo_stacks.pop()
                execute_undo_functions(popped_stack)
                
                # Step 7: Restore old transport (AFTER undoing task)
                if removal_undo_stack:
                    restore_removed_transport(removal_undo_stack)
            else:
                # Task didn't fit in this slot - restore the removed transport
                if removal_undo_stack:
                    restore_removed_transport(removal_undo_stack)
            
            # Move to next slot
            prior_task_list_idx += 1


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
                print(f'found a match for {resource.name}')
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
                print(f'temporarily removing {removed_task.name} from {resource.name}')
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


def find_transport_options(tds, pickup_task, dropoff_task):
    """
    Find all feasible transport options for pickup and dropoff tasks.
    Returns list of options with transport resource and prior tasks for each.
    """
    options = []
    
    for resource in tds.resources.values():
        if 'traveler' in resource.capabilities:
            # Use existing map_feasible_slots_linked_tasks to find options
            slots = resource.timeline.map_feasible_slots_linked_tasks(pickup_task, dropoff_task)
            for slot in slots:
                options.append({
                    'resource': resource,
                    'pickup_prior_task': slot['task1_prior_task'],
                    'dropoff_prior_task': slot['task2_prior_task'],
                    'total_travel': slot['total_travel']
                })
    
    return options


def schedule_dependent_task(tds, task):
    print(f'scheduling {task.name}')
    assignments = search_through_capability_assignments_with_transport(tds, task)
    if not assignments:
        print(f'No possible assignments for {task.name}')
    else:
        assignment = min(assignments, key=lambda x: x['total_travel'])
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
            resource.insert_task_to_timeline(task, capability, prior_task)
            if 'traveler' not in resource.capabilities:
                resource.timeline.add_pickup_dropoffs(task)
                next_task = resource.timeline.tasks[resource.timeline.tasks.index(task)+1]
                resource.timeline.add_pickup_dropoffs(next_task)
        for transport_assignment in assignment['transport_assignment']:
            before_resource = transport_assignment['before_resource']
            before_pickup_task = transport_assignment['before_pickup_task']
            before_dropoff_task = transport_assignment['before_dropoff_task']
            before_pickup_prior_task = transport_assignment['before_pickup_prior_task']
            before_dropoff_prior_task = transport_assignment['before_dropoff_prior_task']

            print(f'Assigning transport task {before_pickup_task.name} to {before_resource.name} after {before_pickup_prior_task.name}')
            before_resource.insert_task_to_timeline(before_pickup_task, 'transport', before_pickup_prior_task)
            before_resource.insert_task_to_timeline(before_dropoff_task, 'transport', before_dropoff_prior_task)

            after_resource = transport_assignment['after_resource']
            after_pickup_task = transport_assignment['after_pickup_task']
            after_dropoff_task = transport_assignment['after_dropoff_task']
            after_pickup_prior_task = transport_assignment['after_pickup_prior_task']
            after_dropoff_prior_task = transport_assignment['after_dropoff_prior_task']
            if after_resource is not None:
                print(f'Assigning transport task {after_pickup_task} to {after_resource} after {after_pickup_prior_task}')
                after_resource.insert_task_to_timeline(after_pickup_task, 'transport', after_pickup_prior_task)
                after_resource.insert_task_to_timeline(after_dropoff_task, 'transport', after_dropoff_prior_task)


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
dependent_tasks = schedule_independent_tasks(tds)
for dep_task in dependent_tasks:
    schedule_dependent_task(tds, dep_task)
display_current_schedule(tds, EPOCH_DATE)

