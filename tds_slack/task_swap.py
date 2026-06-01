"""
task_swap.py
============
Core task swap algorithm adapted from GTS (Rubinstein, Smith & Barbulescu, AAAI 2012)
for resilient scheduling under resource downtime.

Slot format (from search_feasible_slots):
    {
        'resource':         Resource object,
        'task1_prior_task': Task that this task would be scheduled after,
        '<metric>':         float score for each requested metric,
        ...
    }
"""

from tds_slack.utils import execute_undo_functions
from slack_search import search_feasible_slots
from queue import deque



def _get_resource_for_task(task, tds):
    """Return the resource whose timeline currently holds `task`, or None."""
    for resource in tds.resources.values():
        if task in resource.timeline.tasks:
            return resource
    return None


def _best_slot(feasible_slots, metric, minimize):
    """Pick the best slot dict by metric, respecting minimize flag."""
    return min(feasible_slots, key=lambda s: s.get(metric, float('inf'))) \
        if minimize else \
        max(feasible_slots, key=lambda s: s.get(metric, float('-inf')))


def _retract_task(task, tds):
    """
    Remove `task` from its current resource timeline and return the undo
    deque so the removal can be reversed.
    """
    resource = _get_resource_for_task(task, tds)
    if resource is None:
        return deque()
    return resource.timeline.remove_task(task, generate_undo=True)


def _score_conflict_set(candidate_set, tds, retraction_metric):
    """
    Score a candidate conflict set for the retraction heuristic.
    Higher score = prefer to retract this set.
 
    retraction_metric controls what we sum across the set:
        'flexibility' — sum of slot and slack (tasks with more alternatives are
                        cheaper to displace; default resilience heuristic)
        <placeholder> — add further metric branches here as needed
    """
    if retraction_metric == 'flexibility':
        total = 0
        for task in candidate_set:
            resource = _get_resource_for_task(task, tds)
            total += 1 # determine_slack(tds, task, resource)
        return total
    # placeholder: add other retraction metrics here
    raise ValueError(f"Unknown retraction_metric: '{retraction_metric}'")


def compute_conflict_sets(task, tds, metric, protected, retraction_metric):
    """
    Algorithm 2 from the paper.
 
    Enumerate subsets of tasks that overlap with `task` (excluding protected
    ones). For each subset: retract all members, check whether `task` can now
    be inserted, then restore.  Collect subsets that do unblock `task`.
 
    Enumeration follows the paper: test all overlapping tasks together, then
    drop the first, then the first two, etc. (progressively smaller subsets).
    This is O(n^2) retract/restore pairs but n is small in practice.
 
    Returns a list of (score, conflict_set) sorted descending by score
    (best retraction choice first).  An empty list means no subset unblocks
    the task.
    """
    protected_tasks = {t.name for t in protected}
    overlapping = []
    for resource in tds.resources.values():
        if task.capability not in resource.capabilities:
            continue
        for t in resource.timeline.find_all_overlapping_tasks(task):
            if t.name not in protected_tasks:
                overlapping.append(t)
 
    if not overlapping:
        return []
 
    valid_sets = []
 
    for i in range(len(overlapping)):
        candidate_set = overlapping[i:]   # drop the first i tasks each iteration
        retraction_undo_stacks = deque()
 
        for t in candidate_set:
            # remove tasks in candidate set
            undo = _retract_task(t, tds)
            retraction_undo_stacks.extend(undo)
 
        # find new feasible slots for task with candidate set retracted
        feasible_slots = search_feasible_slots(tds, task, [metric])
 
        if feasible_slots:
            # choose best slot
            score = _score_conflict_set(candidate_set, tds, retraction_metric)
            valid_sets.append((score, candidate_set))
 
        execute_undo_functions(retraction_undo_stacks)
 
    valid_sets.sort(key=lambda x: x[0], reverse=True)
    return valid_sets



def task_swap(displaced_task, tds, metric, minimize=True, retraction_metric='slack', max_moves=10):
    """
    Attempt to insert `displaced_task` into the schedule, swapping other
    tasks around if no direct slot is available.
 
    Parameters
    ----------
    displaced_task : Task
        Task evicted by downtime.  Must already be removed from the TDS
        timeline before calling.
    tds : TDS
        TDS manager with displaced_task already removed.
    metric : str
        Objective metric for slot selection, e.g. 'slack', 'flexibility'.
    minimize : bool
        True  → lower metric value is better (e.g. makespan, travel).
        False → higher metric value is better (e.g. slack, flexibility).
    retraction_metric : str
        Metric used to score conflict sets for the retraction heuristic.
    max_moves : int
        Maximum swaps (not counting direct insertions) before giving up.
 
    Returns
    -------
    success : bool
    committed_moves : list of (task, resource, prior_task) — empty on failure
    unplaced : list of tasks that could not be reinserted — empty on success
    """

    protected       = []                      # tasks relocated once this call; not eligible for re-retraction
    retracted_queue = deque([displaced_task]) # tasks waiting to be (re)inserted
    main_undo       = deque()                 # undo stack for ALL insertions this call
    committed_moves = []                      # placements made this invocation; needed to undo on failure
    num_moves       = 0
 
    while retracted_queue and num_moves < max_moves:
        current_task = retracted_queue.popleft()   # FIFO matches paper ordering
 
        # Step 1 — try direct insertion
        feasible_slots = search_feasible_slots(tds, current_task, [metric])
 
        if feasible_slots:
            slot = _best_slot(feasible_slots, metric, minimize)
            resource  = slot['resource']
            prior_task = slot['task1_prior_task']

            insert_undo = resource.timeline.try_slot(current_task, prior_task)
            main_undo.extend(insert_undo)
            committed_moves.append((current_task, resource, prior_task))
            continue
 

        # Step 2 — find conflict sets
        conflict_set_candidates = compute_conflict_sets(
            current_task, tds, metric, protected, retraction_metric
        )
 
        if not conflict_set_candidates:
            # Nothing can free a slot — undo everything and report failure
            # execute undo stack
            execute_undo_functions(main_undo)
            return False, [], list(retracted_queue) + [current_task]


        # Step 3 — retract the best conflict set
        _, best_conflict_set = conflict_set_candidates[0]
 
        for t in best_conflict_set:
            retract_undo = _retract_task(t, tds)
            protected.append(t)
            retracted_queue.append(t)
 

        # Step 4 — insert current_task into the now-freed slot
        feasible_slots = search_feasible_slots(tds, current_task, [metric])
 
        if not feasible_slots:
            execute_undo_functions(main_undo)
            return False, [], [current_task]
 
        slot = _best_slot(feasible_slots, metric, minimize)
        resource   = slot['resource']
        prior_task = slot['task1_prior_task']

        insert_undo = resource.timeline.try_slot(current_task, prior_task)
        main_undo.extend(insert_undo)
        committed_moves.append((current_task, resource, prior_task))
        num_moves += 1
 

    # Final outcome
    if retracted_queue:
        # Hit max_moves with tasks still waiting — undo everything
        execute_undo_functions(main_undo)
        return False, [], list(retracted_queue)
 
    return True, committed_moves, []