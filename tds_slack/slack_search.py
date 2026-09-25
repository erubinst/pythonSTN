from tds_slack.utils import execute_undo_functions
from collections import deque


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


def _direct_slot_slack_on_resource(task, changed_tl_resource, prior_assignment):
    """Raw sols(changed_tl_resource, task): sum of slack over currently-open
    feasible slots, with nothing retracted. No risk adjustment applied."""
    metrics = ["slack"]
    alternate_slots = search_feasible_slots_on_resource(task, changed_tl_resource, metrics, prior_assignment=prior_assignment)
    slot_slack = 0
    for slot in alternate_slots:
        slot_slack += slot.get('slack', 0)
    return slot_slack


def determine_swap_slot_slack_on_resource(task, changed_tl_resource, prior_assignment):
    """
    swapsols(changed_tl_resource, task): the slack achievable for `task` on
    `changed_tl_resource` if that resource gave up some subset of its
    currently scheduled tasks overlapping `task`'s window. Only meaningful as
    a fallback when _direct_slot_slack_on_resource is 0 -- otherwise sols
    already covers it.

    Mirrors task_swap.py's compute_conflict_sets (Algorithm 2) -- enumerate
    progressively smaller suffixes of the overlapping-task list, retract each
    candidate set, and keep the ones that unblock task. Among those, pick the
    same candidate set task_swap itself would pick (via _score_conflict_set,
    the identical retraction heuristic used at repair time), not whichever
    set happens to maximize this task's own slack -- the credit given here
    should match what actually happens when task_swap later runs for real.
    """
    from tds_slack.task_swap import _score_conflict_set  # local import: task_swap.py imports this module

    overlapping = []
    for t in changed_tl_resource.timeline.find_overlapping_tasks(task):
        if t is task or t in overlapping:
            continue
        if t.name.endswith('_header') or t.name.endswith('_footer') or 'downtime' in t.name:
            continue
        if t.status != 'scheduled':
            continue
        overlapping.append(t)

    if not overlapping:
        return 0

    valid_sets = []  # (score, slack) for every candidate set that unblocks task
    for i in range(len(overlapping)):
        candidate_set = overlapping[i:]
        undo_stack = deque()
        for t in candidate_set:
            undo_stack.extend(changed_tl_resource.timeline.remove_task(t, generate_undo=True))
        slack = _direct_slot_slack_on_resource(task, changed_tl_resource, prior_assignment)
        if slack > 0:
            score = _score_conflict_set(candidate_set, 'full_flex_swap')
            valid_sets.append((score, slack))
        execute_undo_functions(undo_stack)

    if not valid_sets:
        return 0
    valid_sets.sort(key=lambda pair: pair[0], reverse=True)
    return valid_sets[0][1]


def determine_slot_slack_on_resource(task, changed_tl_resource, resource, return_detail=False):
    """
    Determine the slot slack for a given task on a specific resource.
    Falls back to swapsols (best slack achievable via a single retraction on
    changed_tl_resource) when there is no directly-open slot (sols == 0).

    Args:
        tds: TDS manager
        task: Task to evaluate
        changed_tl_resource: Resource whose timeline has changed
        resource: Currently assigned resource (if any)
        return_detail: if True, also return a dict of {'sols', 'swapsols', 'source'}

    Returns:
        Slot slack value (float), or (value, detail) if return_detail
    """
    # unassign the task from its current resource timeline to evaluate potential slack and save undo info
    if resource:
        task_idx = resource.timeline.tasks.index(task)
        prior_task = resource.timeline.tasks[task_idx - 1] if task_idx > 0 else None
        undo_stack = resource.timeline.remove_task(task, generate_undo=True)
        prior_assignment = (resource, prior_task)
    else:
        prior_assignment = None
        undo_stack = []

    sols_value = _direct_slot_slack_on_resource(task, changed_tl_resource, prior_assignment)
    if sols_value > 0:
        raw = sols_value
        swapsols_value = None
        source = 'sols'
    elif task.tds.include_swapsols:
        swapsols_value = determine_swap_slot_slack_on_resource(task, changed_tl_resource, prior_assignment)
        raw = swapsols_value
        source = 'swapsols' if swapsols_value > 0 else 'none'
    else:
        raw = 0
        swapsols_value = None
        source = 'none'

    # reassign the task back to its original resource timeline
    execute_undo_functions(undo_stack)
    current_risk = resource.risk_normalized if resource else 1.0
    value = raw / (changed_tl_resource.risk_normalized * current_risk)

    if return_detail:
        return value, {'sols': sols_value, 'swapsols': swapsols_value, 'source': source}
    return value




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
    resources_with_direct_slot = set()
    for slot in alternate_slots:
        # print(f"Slot slack for task {task.name} on resource {slot['resource'].name} with prior task {slot['task1_prior_task'].name if slot['task1_prior_task'] else 'None'}: {slot.get('task1_slack', 0)}")
        slot_slack += slot.get('slack', 0) / slot['resource'].risk_normalized
        resources_with_direct_slot.add(slot['resource'])

    # swapsols fallback: for any capable resource with no directly-open slot,
    # credit the best single-retraction rescue instead (mirrors
    # determine_slot_slack_on_resource's per-resource fallback).
    if task.tds.include_swapsols:
        for r in task.capable_resources():
            if r in resources_with_direct_slot:
                continue
            swap_value = determine_swap_slot_slack_on_resource(task, r, prior_assignment)
            if swap_value > 0:
                slot_slack += swap_value / r.risk_normalized

    # reassign the task back to its original resource timeline
    execute_undo_functions(undo_stack)
    current_risk = resource.risk_normalized if resource else 1.0
    return slot_slack / current_risk


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
    
    # Sort slots by the objective metric (e.g., slack, travel, flexibility).
    # full_flex additionally breaks ties by preferring the earlier start time:
    # it has no built-in preference for early vs. late placement, so absent
    # this it can drift a task later within its window at no cost to its own
    # score, which increases how long the task stays at risk of a disruption.
    if objective_metric in ('full_flex', 'full_flex_swap'):
        feasible_slots.sort(key=lambda x: (-x.get(objective_metric, float('-inf')), x.get('start', float('inf'))))
    else:
        feasible_slots.sort(key=lambda x: x.get(f'{objective_metric}', float('inf')), reverse=not minimize)
    best_slot = feasible_slots[0]
    best_resource = best_slot['resource']

    # Schedule the task on the best resource
    if objective_metric in ('save_flexibility', 'full_flex', 'full_flex_swap', 'slots'):
        best_resource.insert_task_to_timeline(task, task.capability, prev_task=best_slot['task1_prior_task'], generate_travel=True, save_flexibility=True)
    else:
        best_resource.insert_task_to_timeline(task, task.capability, prev_task=best_slot['task1_prior_task'], generate_travel=True)
    # return flexibility metric
    return best_slot.get(f'{objective_metric}', 0)