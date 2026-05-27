from tds.utils import *
from collections import deque
from enum import Enum
import random

class ObjectiveType(Enum):
    MIN_TRAVEL_TIME = "min_travel_time"
    MIN_COMPLETION_TIME = "min_completion_time"
    MIN_MAKESPAN = "min_makespan"


class SortType(Enum):
    CAREGIVER_ROUTINE = "caregiver_routine"
    FLEXIBILITY = "flexibility"


def get_task_location_options(task):
    """Return the possible start/end locations for an independent task."""
    if not task.locations:
        return []

    start_locations = task.locations[0]
    if isinstance(start_locations, (list, tuple, set)):
        return list(dict.fromkeys(start_locations))

    return [start_locations]


def set_task_location(task, location):
    """Force a task to use one concrete location for both start and end."""
    task.locations = [location, location]


def find_independent_task_assignment(tds, task, objective=ObjectiveType.MIN_TRAVEL_TIME):
    """
    Find the best location and resource assignment for an independent task.
    
    Args:
        tds: Task Dependent Scheduling manager
        task: Task to assign
        objective: ObjectiveType enum specifying the optimization objective
               (MIN_TRAVEL_TIME, MIN_COMPLETION_TIME, or MIN_MAKESPAN)
    
    Returns:
        Best assignment tuple or None if no valid assignment exists
    """
    driver_capabilities = tds.get_driver_capabilities()
    for cap in task.capabilities:
        if cap not in driver_capabilities:
            print(f"Task {task.name} is not independent (missing capability {cap})")
            return None
    print(f"Finding assignment for independent task {task.name}")

    original_locations = list(task.locations)
    location_options = get_task_location_options(task)
    if not location_options:
        print(f"No location options available for independent task {task.name}")
        return None

    best_assignment = None
    best_location = None
    best_metric = None

    for location in location_options:
        set_task_location(task, location)
        assignments = search_through_capability_assignments(tds, task)
        if not assignments:
            continue

        candidate_assignment, candidate_metric = min(assignments, key=lambda x: x[1])
        print(
            f"Candidate location {location} for {task.name} has minimum {objective.value} {candidate_metric}"
        )

        if best_metric is None or candidate_metric < best_metric:
            best_assignment = candidate_assignment
            best_location = location
            best_metric = candidate_metric

    if best_assignment:
        set_task_location(task, best_location)
        return best_assignment

    task.locations = original_locations
    print(f"No valid assignment found for independent task {task.name}")
    return None
    

def apply_independent_assignment(tds, task, assignment):
    task = tds.tasks[task.name]
    for resource, prior_task, capability in assignment:
        print(f'Assigning {task.name} to {resource.name} after {prior_task.name}')
        resource = tds.resources[resource.name]
        prior_task = tds.tasks[prior_task.name]
        resource.insert_task_to_timeline(task, capability, prev_task=prior_task)


def schedule_independent_task(tds, task, objective=ObjectiveType.MIN_TRAVEL_TIME):
    """
    Schedule an independent task using the specified objective.
    
    Args:
        tds: Task Dependent Scheduling manager
        task: Task to schedule
        objective: ObjectiveType enum for optimization objective
    
    Returns:
        True if task was successfully scheduled, False otherwise
    """
    assignment = find_independent_task_assignment(tds, task, objective)
    if assignment:
        apply_independent_assignment(tds, task, assignment)
        return True
    else:
        return False


def schedule_independent_tasks(tds, objective=ObjectiveType.MIN_TRAVEL_TIME, sort_by=SortType.CAREGIVER_ROUTINE):
    """
    Schedule all independent tasks using the specified objective.
    
    Args:
        tds: Task Dependent Scheduling manager
        objective: ObjectiveType enum for optimization objective
    
    Returns:
        List of tasks that could not be scheduled (dependent tasks)
    """
    # scheduling driver tasks - go through all tasks and try to schedule onto driver.  If not able to skip
    dependent_tasks = []
    # choose sorting method (caregiver routine or flexibility)
    if sort_by == SortType.FLEXIBILITY:
        sorted_tasks = tds.sort_tasks_by_flexibility()
    else:
        # default: caregiver routine (all true first), then flexibility
        sorted_tasks = tds.sort_tasks_by_caregiver_routine()
    for task in sorted_tasks:
        scheduled = schedule_independent_task(tds, task, objective)
        if not scheduled:
            dependent_tasks.append(task)
    return dependent_tasks


def find_dependent_task_assignment(tds, task, objective=ObjectiveType.MIN_TRAVEL_TIME):
    """
    Find the best assignment for a dependent task that requires transport.
    
    Args:
        tds: Task Dependent Scheduling manager
        task: Task to assign
        objective: ObjectiveType enum specifying the optimization objective
               (MIN_TRAVEL_TIME, MIN_COMPLETION_TIME, or MIN_MAKESPAN)
    
    Returns:
        Best assignment dictionary or None if no valid assignment exists
    """
    print(f'Finding assignment for dependent task {task.name}')
    assignments = search_through_capability_assignments_with_transport(tds, task)
    if not assignments:
        print(f'No valid assignment found for dependent task {task.name}')
        return None
    best_assignment = select_best_assignment(assignments, task.name, objective)
    return best_assignment


def schedule_dependent_task(tds, task, objective=ObjectiveType.MIN_TRAVEL_TIME):
    """
    Schedule a dependent task that requires transport using the specified objective.
    
    Args:
        tds: Task Dependent Scheduling manager
        task: Task to schedule
        objective: ObjectiveType enum for optimization objective
    
    Returns:
        True if task was successfully scheduled, False otherwise
    """
    assignment = find_dependent_task_assignment(tds, task, objective)
    if assignment:  
        apply_assignment(tds, task, assignment)
        return True
    else:
        return False


def deduplicate_assignments(assignments, task_name):
    unique = []

    for assignment in assignments:
        if not any(
            assignment['capability_assignment'] == u['capability_assignment'] and
            assignment['transport_assignment'] == u['transport_assignment']
            for u in unique
        ):
            unique.append(assignment)
        else:
            print(f'Deduplicating assignments for {task_name}')

    return unique


# need to edit this to have the objective function be a parameter
def select_best_assignment(assignments, task_name, objective=ObjectiveType.MIN_TRAVEL_TIME):
    """
    Select the best assignment from a list based on the objective function.
    
    Args:
        assignments: List of assignment dictionaries with metrics
        task_name: Name of the task for logging
        objective: ObjectiveType enum specifying how to select the best assignment
    
    Returns:
        Best assignment dictionary or None if no assignments available
    """
    if not assignments:
        print(f'No possible assignments for {task_name}')
        return None

    if objective == ObjectiveType.MIN_TRAVEL_TIME:
        # First minimize travel, then use ride time as tiebreaker
        min_travel = min(a['total_travel'] for a in assignments)
        min_travel_assignments = [
            a for a in assignments if a['total_travel'] == min_travel
        ]

        print(
            f'Found {len(min_travel_assignments)} assignments '
            f'with minimal travel {min_travel} for {task_name}'
        )

        best = min(min_travel_assignments, key=lambda a: a['total_ride_time'])
        print(f'Selected assignment with ride time {best["total_ride_time"]}')

    elif objective == ObjectiveType.MIN_COMPLETION_TIME:
        # Minimize total completion time, then use travel as tiebreaker
        min_completion = min(a['total_completion_time'] for a in assignments)
        min_completion_assignments = [
            a for a in assignments if a['total_completion_time'] == min_completion
        ]

        print(
            f'Found {len(min_completion_assignments)} assignments '
            f'with minimal completion time {min_completion} for {task_name}'
        )


        best = random.choice(min_completion_assignments)
        print(f'Selected assignment with ride time {best["total_ride_time"]}')

    elif objective == ObjectiveType.MIN_MAKESPAN:
        # Minimize makespan, then use travel as a tie-breaker
        min_makespan = min(a['makespan'] for a in assignments)
        min_makespan_assignments = [
            a for a in assignments if a['makespan'] == min_makespan
        ]

        print(
            f'Found {len(min_makespan_assignments)} assignments '
            f'with minimal makespan {min_makespan} for {task_name}'
        )

        best = random.choice(min_makespan_assignments)
        print(f'Selected assignment with travel {best["total_travel"]}')

    else:
        raise ValueError(f"Unknown objective type: {objective}")

    return best


def apply_assignment(tds, task, assignment):
    task = tds.tasks[task.name]
    for resource, prior_task, capability in assignment['capability_assignment']:
        # find resource and prior task in tds
        resource = tds.resources[resource.name]
        prior_task = tds.tasks[prior_task.name]
        prepare_resource_for_assignment(resource, prior_task)

        print(f'Assigning {task.name} to {resource.name} after {prior_task.name}')
        resource.insert_task_to_timeline(task, capability, prior_task)

        if 'traveler' not in resource.capabilities:
            assign_transport_tasks(resource, task, assignment)


def prepare_resource_for_assignment(resource, prior_task):
    if 'traveler' in resource.capabilities:
        return

    timeline = resource.timeline
    prior_index = timeline.tasks.index(prior_task)

    first_non_transport_task = timeline.tasks[-1]
    for t in timeline.tasks[prior_index + 1:]:
        if 'pickup' not in t.name.lower() and 'dropoff' not in t.name.lower():
            first_non_transport_task = t
            break

    remove_existing_transport_between(
        timeline,
        prior_task,
        first_non_transport_task,
        save_task=False
    )

def assign_transport_tasks(resource, task, assignment):
    for t in assignment['transport_assignment']:
        print(f'Checking transport assignment for {task.name} on resource {resource.name}')
        before_dropoff_task = t['before_dropoff_task']

        # Match this transport to the task/resource
        if (task.name.lower() not in before_dropoff_task.name.lower() or
            resource.name.lower() not in before_dropoff_task.name.lower()):
            continue

        # Add pickup/dropoff placeholders around task
        resource.timeline.add_pickup_dropoffs(task)
        next_task = resource.timeline.tasks[
            resource.timeline.tasks.index(task) + 1
        ]
        resource.timeline.add_pickup_dropoffs(next_task)

        # BEFORE transport
        before_resource = t['before_resource']

        print(
            f'Assigning transport task {t["before_pickup_task"].name} '
            f'to {before_resource.name} after {t["before_pickup_prior_task"].name}'
        )

        before_resource.insert_task_to_timeline(
            t['before_pickup_task'],
            'transport',
            t['before_pickup_prior_task']
        )

        before_resource.insert_task_to_timeline(
            t['before_dropoff_task'],
            'transport',
            t['before_dropoff_prior_task']
        )

        # AFTER transport (optional)
        if t['after_resource'] is not None:
            after_resource = t['after_resource']

            print(
                f'Assigning transport task {t["after_pickup_task"].name} '
                f'to {after_resource.name} after {t["after_pickup_prior_task"].name}'
            )

            after_resource.insert_task_to_timeline(
                t['after_pickup_task'],
                'transport',
                t['after_pickup_prior_task']
            )

            after_resource.insert_task_to_timeline(
                t['after_dropoff_task'],
                'transport',
                t['after_dropoff_prior_task']
            )

        break  # transport found & applied


def write_assignments_to_file(assignments, task):
    filename = f'assignments_{task.name}.txt'

    with open(filename, 'w') as f:
        for assignment in assignments:
            f.write(f'Assignment with total travel {assignment["total_travel"]}:\n')
            f.write(f'Assignment with total ride time {assignment["total_ride_time"]}:\n')

            f.write('Capability Assignments:\n')
            for resource, prior_task, capability in assignment['capability_assignment']:
                f.write(
                    f'  Resource: {resource.name}, '
                    f'Prior Task: {prior_task.name}, '
                    f'Capability: {capability}\n'
                )

            f.write('Transport Assignments:\n')
            for t in assignment['transport_assignment']:
                f.write(
                    f'  Before Resource: {t["before_resource"].name}, '
                    f'Pickup Task: {t["before_pickup_task"].name}, '
                    f'Dropoff Task: {t["before_dropoff_task"].name}, '
                    f'Pickup Prior Task: {t["before_pickup_prior_task"].name}, '
                    f'Dropoff Prior Task: {t["before_dropoff_prior_task"].name}\n'
                )

                if t['after_resource'] is not None:
                    f.write(
                        f'  After Resource: {t["after_resource"].name}, '
                        f'Pickup Task: {t["after_pickup_task"].name}, '
                        f'Dropoff Task: {t["after_dropoff_task"].name}, '
                        f'Pickup Prior Task: {t["after_pickup_prior_task"].name}, '
                        f'Dropoff Prior Task: {t["after_dropoff_prior_task"].name}\n'
                    )

            f.write('---\n')


# --- Case where we don't need to worry about transportation assignments ---
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
        return
    
    # Try assigning current capability to each compatible resource
    capability = capabilities[capability_idx]
    new_task_lst = task.start.ub
    
    for resource in tds.resources.values():
        if 'traveler' not in resource.capabilities:
            continue
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
# ----


# --- Scheduling WITH transport ---
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
            'total_ride_time': tds.sum_total_ride_time(),
            'total_completion_time': tds.sum_total_completion_time(),
            'makespan': tds.min_makespan()
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
                        if need_before and 'transport' not in before_resource.capabilities:
                            continue
                        if not need_before:
                            before_resource = None

                        for after_resource in tds.resources.values():
                            if need_after and 'transport' not in after_resource.capabilities:
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
                                                        'driven_resource': resource.name
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
                                                'driven_resource': resource.name
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


def remove_task_and_transport(tds, task):
    """
    Remove task and any associated transport tasks, don't save undo stack
    """
    for resource in tds.resources.values():
        if task in resource.timeline.tasks:
            task_idx = resource.timeline.tasks.index(task)
            prior_task = resource.timeline.tasks[task_idx-1] if task_idx > 0 else None
            next_task = resource.timeline.tasks[task_idx+1] if task_idx < len(resource.timeline.tasks)-1 else None
            resource.remove_task_from_timeline(task)
            remove_existing_transport_between(resource.timeline, prior_task, next_task, save_task=False)


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