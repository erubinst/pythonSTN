from tds_slack.utils import execute_undo_functions

def search_feasible_slots(tds, task, metrics, assigned_resource=None):
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
        if assigned_resource and resource.name == assigned_resource.name:
            continue
        
        # Find all feasible slots for this resource
        feasible_slots = resource.timeline.map_feasible_slots(task, metrics)
        
        # Add resource info to each slot
        for slot in feasible_slots:
            slot['resource'] = resource
            all_slots.append(slot)
    
    return all_slots


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
    undo_stack = resource.timeline.remove_task(task, generate_undo=True)
    # find alternate slots using search feasible slots function
    alternate_slots = search_feasible_slots(tds, task, metrics, assigned_resource=resource)
    # on each alternate slot, we sum the task1_slack
    slot_slack = 0
    for slot in alternate_slots:
        # print(f"Slot slack for task {task.name} on resource {slot['resource'].name} with prior task {slot['task1_prior_task'].name if slot['task1_prior_task'] else 'None'}: {slot.get('task1_slack', 0)}")
        slot_slack += slot.get('task1_slack', 0)
    # reassign the task back to its original resource timeline
    execute_undo_functions(undo_stack)
    return slot_slack
    


def schedule_task(tds, task, objective_metric, minimize=True):
    """
    Attempt to schedule a task based on given metric across all compatible resources.
    
    Args:
        tds: TDS manager
        task: Task to schedule (assumes single capability)
    Returns:
        True if task was successfully scheduled, False otherwise.
    """
    feasible_slots = search_feasible_slots(tds, task, [objective_metric])
    if not feasible_slots:
        return False
    
    # Sort slots by the objective metric (e.g., slack, travel, flexibility)
    feasible_slots.sort(key=lambda x: x.get(f'task1_{objective_metric}', float('inf')), reverse=not minimize)
    best_slot = feasible_slots[0]
    best_resource = best_slot['resource']
    
    # Schedule the task on the best resource
    best_resource.timeline.insert_task(task, prev_task=best_slot['task1_prior_task'], generate_travel=True)
    # return flexibility metric
    return best_slot.get(f'task1_{objective_metric}', 0)