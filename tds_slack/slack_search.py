from tds_slack.utils import execute_undo_functions


# function to just check for a feasible slot:
def has_feasible_slot(tds, task, prior_assignment=None):
    if not task.capability:
        return False
    
    capability = task.capability
    for resource in tds.resources.values():
        if capability not in resource.capabilities:
            continue
        
        # Skip already assigned resource if given (for rescheduling scenarios)
        if prior_assignment and resource.name == prior_assignment[0].name:
            has_feasible_slot = resource.timeline.has_feasible_slot(task, prior_slot=prior_assignment[1])
            if has_feasible_slot:
                return True
            
        else:
            if resource.timeline.has_feasible_slot(task):
                return True

    return False

def search_feasible_slots(tds, task, metrics, prior_assignment=None):
    """
    Find all feasible slots for a task across all compatible resources.
    
    Args:
        tds: TDS manager
        task: Task to search slots for (assumes single capability)
    
    Returns:
        List of dicts containing:
        - 'resource': Resource object
        - 'prior_task': Task that this task would be scheduled after
        - 'total_travel': Total travel time for this placement
    """
    if not task.capability:
        return []
    
    capability = task.capability
    all_slots = []
    
    # Search through all resources that have the required capability
    for resource in tds.resources.values():
        if capability not in resource.capabilities:
            continue
        
        # Skip already assigned resource if given (for rescheduling scenarios)
        if prior_assignment and resource.name == prior_assignment[0].name:
            feasible_slots = resource.timeline.map_feasible_slots(task, metrics, prior_slot=prior_assignment[1])
        else:
            # Find all feasible slots for this resource
            feasible_slots = resource.timeline.map_feasible_slots(task, metrics)
        
        # Add resource info to each slot
        for slot in feasible_slots:
            slot['resource'] = resource
            all_slots.append(slot)
    
    return all_slots


def search_feasible_slots_on_resource(task, resource, metrics, prior_assignment=None):
    """
    Find all feasible slots for a task on a specific resource.
    
    Args:
        task: Task to search slots for (assumes single capability)
        resource: Resource to search slots on
        metrics: List of metrics to evaluate for each slot
        prior_assignment: Tuple of (resource, prior_task) if the task is already assigned

    Returns:
        List of dicts containing:
        - 'resource': Resource object
        - 'prior_task': Task that this task would be scheduled after
        - 'metrics': Dictionary of evaluated metrics for this placement
    """
    if not task.capability:
        return []
    
    capability = task.capability
    if capability not in resource.capabilities:
        return []
    
    # Skip already assigned resource if given (for rescheduling scenarios)
    if prior_assignment and resource.name == prior_assignment[0].name:
        feasible_slots = resource.timeline.map_feasible_slots(task, metrics, prior_slot=prior_assignment[1])
    else:
        # Find all feasible slots for this resource
        feasible_slots = resource.timeline.map_feasible_slots(task, metrics)
    
    # Add resource info to each slot
    for slot in feasible_slots:
        slot['resource'] = resource
    
    return feasible_slots


def determine_slot_slack_on_resource(task, changed_tl_resource, resource):
    """
    Determine the slot slack for a given task on a specific resource.
    
    Args:
        tds: TDS manager
        task: Task to evaluate
        changed_tl_resource: Resource whose timeline has changed 
        resource: Currently assigned resource (if any)

    Returns:
        Slot slack value (int)
    """
    metrics = ["slack"] # NEVER call with flexibility as it will cause loop 
    # unassign the task from its current resource timeline to evaluate potential slack and save undo info
    if resource:
        task_idx = resource.timeline.tasks.index(task)
        prior_task = resource.timeline.tasks[task_idx - 1] if task_idx > 0 else None
        undo_stack = resource.timeline.remove_task(task, generate_undo=True)
        # find alternate slots using search feasible slots function
        prior_assignment = (resource, prior_task)
    else:
        prior_assignment = None
        undo_stack = []
    alternate_slots = search_feasible_slots_on_resource(task, changed_tl_resource, metrics, prior_assignment=prior_assignment)
    # on each alternate slot, we sum the task1_slack
    slot_slack = 0
    for slot in alternate_slots:
        slot_slack += slot.get('slack', 0)
    # reassign the task back to its original resource timeline
    execute_undo_functions(undo_stack)
    return slot_slack




def determine_slot_slack(tds, task, resource):
    """
    Determine the slot slack for a given task on a specific resource.
    
    Args:
        tds: TDS manager
        task: Task to evaluate
        resource: Currently assigned resource

    Returns:
        Slot slack value (int)
    """
    metrics = ["slack"] # NEVER call with flexibility as it will cause loop 
    # unassign the task from its current resource timeline to evaluate potential slack and save undo info
    if resource:
        task_idx = resource.timeline.tasks.index(task)
        prior_task = resource.timeline.tasks[task_idx - 1] if task_idx > 0 else None
        undo_stack = resource.timeline.remove_task(task, generate_undo=True)
        # find alternate slots using search feasible slots function
        prior_assignment = (resource, prior_task)
    else:
        prior_assignment = None
        undo_stack = []
    alternate_slots = search_feasible_slots(tds, task, metrics, prior_assignment=prior_assignment)
    # on each alternate slot, we sum the task1_slack
    slot_slack = 0
    for slot in alternate_slots:
        # print(f"Slot slack for task {task.name} on resource {slot['resource'].name} with prior task {slot['task1_prior_task'].name if slot['task1_prior_task'] else 'None'}: {slot.get('task1_slack', 0)}")
        slot_slack += slot.get('slack', 0)
    # reassign the task back to its original resource timeline
    execute_undo_functions(undo_stack)
    return slot_slack


def determine_max_slot_slack(tds, task, resource):
    """
    Determine the alternate slot with the maximum slack
    Args:
        tds: TDS manager
        task: Task to evaluate
        resource: Currently assigned resource
    Returns:
        Max slot slack value (int)
    """
    metrics = ["slack"] # NEVER call with flexibility as it will cause loop 
    # unassign the task from its current resource timeline to evaluate potential slack and save undo info
    if resource:
        task_idx = resource.timeline.tasks.index(task)
        prior_task = resource.timeline.tasks[task_idx - 1] if task_idx > 0 else None
        undo_stack = resource.timeline.remove_task(task, generate_undo=True)
        # find alternate slots using search feasible slots function
        prior_assignment = (resource, prior_task)
    else:
        prior_assignment = None
        undo_stack = []
    alternate_slots = search_feasible_slots(tds, task, metrics, prior_assignment=prior_assignment)
    # on each alternate slot, we sum the task1_slack
    max_slot_slack = 0
    for slot in alternate_slots:
        # print(f"Slot slack for task {task.name} on resource {slot['resource'].name} with prior task {slot['task1_prior_task'].name if slot['task1_prior_task'] else 'None'}: {slot.get('task1_slack', 0)}")
        max_slot_slack = max(max_slot_slack, slot.get('slack', 0))
    # reassign the task back to its original resource timeline
    execute_undo_functions(undo_stack)
    return max_slot_slack

    


def schedule_task(tds, task, objective_metric, minimize=True, other_metrics = None):
    """
    Attempt to schedule a task based on given metric across all compatible resources.
    
    Args:
        tds: TDS manager
        task: Task to schedule (assumes single capability)
        objective_metric: Metric to optimize (e.g., 'slack', 'travel', 'flexibility')
        minimize: Whether to minimize the objective metric
        other_metrics: List of additional metrics to consider
    Returns:
        True if task was successfully scheduled, False otherwise.
    """
    metrics = [objective_metric]
    if other_metrics:
        metrics.extend(other_metrics)
    
    feasible_slots = search_feasible_slots(tds, task, metrics)
    if not feasible_slots:
        return False
    
    # Sort slots by the objective metric (e.g., slack, travel, flexibility)
    feasible_slots.sort(key=lambda x: x.get(f'{objective_metric}', float('inf')), reverse=not minimize)
    best_slot = feasible_slots[0]
    best_resource = best_slot['resource']
    
    # Schedule the task on the best resource
    if objective_metric == 'save_flexibility':
        best_resource.insert_task_to_timeline(task, task.capability, prev_task=best_slot['task1_prior_task'], generate_travel=True, save_flexibility=True)
    else:
        best_resource.insert_task_to_timeline(task, task.capability, prev_task=best_slot['task1_prior_task'], generate_travel=True)
    # return flexibility metric
    return best_slot.get(f'{objective_metric}', 0)