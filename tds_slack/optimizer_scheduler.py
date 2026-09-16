"""
CP-SAT-based initial schedule generation: an alternative to run_scheduler's
greedy loop (executer.py) that solves for a schedule maximizing the number
of tasks placed, subject to the same constraints the STN enforces (capability
match, one-task-at-a-time per resource, sequence-dependent travel time,
release/due date, exact duration).

Design: CP-SAT only decides the *combinatorial* part -- which resource each
task goes on and its relative order there. The exact numeric start/end times
CP-SAT picks are discarded; the winning order is replayed through the
existing Timeline.insert_task_to_timeline machinery so the STN recomputes the
true flexible [lb, ub] bounds for that order, exactly as the greedy scheduler
already does for its own placements. This means the write-back step reuses
code that already exists and is already correct, rather than needing a new
translator from a CP solution back into STN timepoints.

V1 SCOPE: assumes no resource has any *initial* (pre-existing) downtime on
its timeline when this runs -- i.e. every resource's timeline is just
[header, footer] at call time. This matches every scenario this codebase
currently generates (downtime_prob is always forced to 0; only *future*
downtime events exist, and those are injected during the live simulation,
not at initial generation time). If a resource does have a mid-schedule
downtime already present, this raises rather than silently producing a
schedule that ignores it -- see _assert_no_preexisting_downtimes.
"""
import numpy as np
from ortools.sat.python import cp_model


def _assert_no_preexisting_downtimes(tds):
    for resource in tds.resources.values():
        extra = [t for t in resource.timeline.tasks if 'downtime' in t.name]
        if extra:
            raise NotImplementedError(
                f"optimizer_scheduler v1 does not support pre-existing downtimes "
                f"(resource {resource.name} already has {[t.name for t in extra]} on its "
                f"timeline before optimization). Extend the model to include these as "
                f"fixed, non-optional circuit nodes before using it on scenarios with "
                f"downtime_prob > 0."
            )


def _real_tasks(tds):
    return [
        t for t in tds.tasks.values()
        if not t.name.endswith('_header') and not t.name.endswith('_footer') and 'downtime' not in t.name
    ]


def build_and_solve(tds, removed_tasks=None, time_limit_seconds=60, num_workers=8):
    """
    Build the CP-SAT model and solve it, maximizing the count of free tasks
    placed.

    removed_tasks=None (default): INITIAL schedule generation. Every real
    task in tds is a free/optional candidate, and no resource may have any
    pre-existing downtime on its timeline yet (see
    _assert_no_preexisting_downtimes).

    removed_tasks=<list>: RUNTIME REGENERATION. Only the tasks in
    removed_tasks (the not-yet-started 'scheduled' tasks wiped off the
    schedule -- see TDSManager.wipe_all_scheduled_tasks) are free/optional
    candidates. Everything else still sitting on a resource's timeline
    (executing tasks, downtime blocks, AND completed tasks -- completed
    tasks are never removed from the timeline, they just stay there as
    history) is instead a MANDATORY, FIXED candidate: its start/end are the
    real constants already committed in the STN (not variables), it has no
    presence_var (always included) and no self-loop arc (AddCircuit is
    forced to visit it). Free tasks are then placed into the real gaps
    around these fixed anchors -- see _add_resource_circuit. Free tasks
    also can't start before tds.now, since we're mid-simulation.

    Returns (status, resource_orders, presence) where:
        status: the raw CP-SAT solver status (cp_model.OPTIMAL / FEASIBLE / ...)
        resource_orders: dict {resource_name: [Task, ...]} -- the full
            solved sequence order on that resource, free tasks placed by
            the solver interleaved with any fixed tasks already there.
            Only present for resources with >=1 task (free or fixed).
        presence: dict {task_name: bool} for every free task -- True if the
            solver placed it anywhere, False if it was left unscheduled.
            (Fixed tasks aren't included; they're always present already.)
    """
    if removed_tasks is None:
        _assert_no_preexisting_downtimes(tds)
        free_tasks = _real_tasks(tds)
    else:
        free_tasks = removed_tasks

    # Whatever's left on a resource's timeline (besides header/footer) is,
    # by construction, immovable in this context: for the initial-generation
    # case that's nothing (timelines start empty); for the runtime-
    # regeneration case, wipe_all_scheduled_tasks already stripped every
    # movable 'scheduled' task off the live timelines before this is called,
    # so anything still there is completed, executing, or a downtime block.
    fixed_by_resource = {
        r.name: [t for t in r.timeline.tasks if not (t.name.endswith('_header') or t.name.endswith('_footer'))]
        for r in tds.resources.values()
    }

    now = int(round(np.abs(tds.now.lb))) if tds.now is not None else 0

    model = cp_model.CpModel()

    # (task, resource) -> (start_var, end_var, presence_var)
    vars_by_pair = {}
    # task -> [presence_var, ...] across every capable resource
    presence_vars_by_task = {t.name: [] for t in free_tasks}
    # resource -> [(task, start, end, presence_var), ...] -- presence_var is
    # None for fixed (mandatory, constant-timing) candidates.
    candidates_by_resource = {r.name: [] for r in tds.resources.values()}

    for resource in tds.resources.values():
        for task in fixed_by_resource[resource.name]:
            start = int(round(np.abs(task.start.lb)))
            end = int(round(np.abs(task.end.lb)))
            candidates_by_resource[resource.name].append((task, start, end, None))

    skipped_infeasible = []
    for task in free_tasks:
        release = max(int(round(task.get_release_time())), now)
        due = int(round(task.get_due_date()))
        duration = int(round(task.get_duration()))
        if due - duration < release:
            # Can't fit on ANY resource regardless of assignment -- release
            # to due-minus-duration would be an empty/invalid domain.
            skipped_infeasible.append(task.name)
            continue

        capable = [r for r in tds.resources.values() if r.has_capability(task.capability)]
        for resource in capable:
            start_var = model.new_int_var(release, due - duration, f'start_{task.name}_{resource.name}')
            end_var = model.new_int_var(release + duration, due, f'end_{task.name}_{resource.name}')
            presence_var = model.new_bool_var(f'presence_{task.name}_{resource.name}')
            model.new_optional_interval_var(start_var, duration, end_var, presence_var, f'interval_{task.name}_{resource.name}')

            vars_by_pair[(task.name, resource.name)] = (start_var, end_var, presence_var)
            presence_vars_by_task[task.name].append(presence_var)
            candidates_by_resource[resource.name].append((task, start_var, end_var, presence_var))

    for task in free_tasks:
        p = presence_vars_by_task[task.name]
        if p:
            model.add(sum(p) <= 1)

    for resource in tds.resources.values():
        _add_resource_circuit(model, tds, resource, candidates_by_resource[resource.name])

    model.maximize(sum(pv for plist in presence_vars_by_task.values() for pv in plist))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = num_workers
    status = solver.solve(model)

    presence = {t.name: False for t in free_tasks}
    for name in skipped_infeasible:
        presence[name] = False

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return status, {}, presence

    resource_orders = {}
    for resource in tds.resources.values():
        placed = []
        for task, start, end, presence_var in candidates_by_resource[resource.name]:
            if presence_var is None:
                # Fixed task: always included, at its real constant start.
                placed.append((start, task))
            elif solver.value(presence_var):
                placed.append((solver.value(start), task))
                presence[task.name] = True
        if placed:
            placed.sort(key=lambda pair: pair[0])
            resource_orders[resource.name] = [task for _, task in placed]

    return status, resource_orders, presence


def _add_resource_circuit(model, tds, resource, candidates):
    """
    One CP-SAT AddCircuit per resource: node 0 is a virtual depot anchored at
    the resource's base_location (representing "start of day"), one node per
    candidate (free or fixed). A free task's self-loop arc means "not placed
    on this resource" (tied to that (task, resource) pair's presence_var); a
    fixed task has no self-loop at all, so AddCircuit is forced to visit it.
    The depot's self-loop ("nothing on this resource at all") only exists
    when every candidate is free -- if any fixed task is present the
    resource can never be empty, so that option is dropped and depot is
    forced to connect directly to whichever candidate ends up first.

    Real depot->task / task->task / task->depot arcs each carry the
    travel-time-aware precedence constraint gating on that arc's literal,
    only enforced when the arc is actually taken (OnlyEnforceIf) --
    implementing sequence-dependent transition times, since the required gap
    between two tasks depends on which one immediately precedes the other.
    Fixed tasks participate with their real constant start/end instead of a
    variable, so these same constraints simply confirm (or rule out, per
    arc) that a given adjacency is physically consistent with what's already
    committed in the STN.
    """
    if not candidates:
        return

    travel_matrix = tds.travel_matrix
    base_location = resource.base_location
    # The header task itself occupies [0, 1] (a real 1-unit duration, not a
    # zero-duration marker -- see Timeline.create_header_footer), so the
    # earliest any task can actually start is the header's end, not 0.
    # Omitting this offset understated every depot-departure time by exactly
    # that amount, which then propagates unchanged through an entire chain
    # and can silently blow a tight downstream due-date by that same amount.
    depot_departure = int(round(np.abs(resource.timeline.tasks[0].end.lb)))

    arcs = []

    free_candidates = [c for c in candidates if c[3] is not None]
    has_fixed = len(free_candidates) != len(candidates)

    if not has_fixed:
        depot_self_loop = model.new_bool_var(f'depot_empty_{resource.name}')
        presences = [presence_var for _, _, _, presence_var in candidates]
        model.add(sum(presences) == 0).only_enforce_if(depot_self_loop)
        model.add(sum(presences) >= 1).only_enforce_if(depot_self_loop.Not())
        arcs.append((0, 0, depot_self_loop))

    for i, (task, start, end, presence_var) in enumerate(candidates, start=1):
        if presence_var is not None:
            arcs.append((i, i, presence_var.Not()))

        depot_to_i = model.new_bool_var(f'arc_depot_{task.name}_{resource.name}')
        model.add(start >= depot_departure + travel_matrix[base_location][task.locations[0]]).only_enforce_if(depot_to_i)
        arcs.append((0, i, depot_to_i))

        i_to_depot = model.new_bool_var(f'arc_{task.name}_depot_{resource.name}')
        arcs.append((i, 0, i_to_depot))

    for i, (task_i, start_i, end_i, pres_i) in enumerate(candidates, start=1):
        for j, (task_j, start_j, end_j, pres_j) in enumerate(candidates, start=1):
            if i == j:
                continue
            lit = model.new_bool_var(f'arc_{task_i.name}_{task_j.name}_{resource.name}')
            gap = travel_matrix[task_i.locations[-1]][task_j.locations[0]]
            model.add(start_j >= end_i + gap).only_enforce_if(lit)
            arcs.append((i, j, lit))

    model.add_circuit(arcs)


def generate_initial_schedule_cp(tds, time_limit_seconds=180, num_workers=8):
    """
    CP-SAT-driven alternative to run_scheduler's greedy per-task loop for
    initial schedule generation. Solves the combinatorial (resource, order)
    assignment via build_and_solve, then replays the winning per-resource
    order into the STN through Resource.insert_task_to_timeline -- the same
    call the greedy scheduler uses -- so the STN (not CP-SAT's own numeric
    start/end picks) computes the true flexible [lb, ub] bounds for that
    order.

    Any replay failure is treated as a modeling bug rather than a normal
    drop: CP-SAT already decided this task fits, respecting release/due/
    duration/travel-time constraints as IT modeled them. If the STN's own
    constraint propagation then rejects the insertion, the CP model's
    constraints don't actually match the STN's and need to be fixed --
    silently leaving the task unscheduled would hide that mismatch instead
    of surfacing it.
    """
    status, resource_orders, presence = build_and_solve(
        tds, time_limit_seconds=time_limit_seconds, num_workers=num_workers
    )
    tds.cp_solve_log.append(('initial', status))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT could not find any feasible initial schedule (status={status}).")

    for resource_name, ordered_tasks in resource_orders.items():
        resource = tds.resources[resource_name]
        prev_task = resource.timeline.tasks[0]
        for task in ordered_tasks:
            ok = resource.insert_task_to_timeline(task, task.capability, prev_task=prev_task, generate_travel=True)
            if not ok:
                raise RuntimeError(
                    f"CP-SAT selected {task.name} on {resource.name} but replaying it into the STN "
                    f"produced an inconsistency. This means the CP model's constraints (release/due/"
                    f"duration/travel-time) don't actually match what the STN enforces on insertion -- "
                    f"fix the model rather than silently dropping the task."
                )
            prev_task = task

    return tds


def regenerate_schedule_cp(tds, removed_tasks, time_limit_seconds=180, num_workers=8):
    """
    CP-SAT-driven alternative to executer.regenerate_schedule_for_event's
    greedy rebuild loop, for the "wipe every not-yet-started task and
    rebuild" case. removed_tasks are the tasks TDSManager.wipe_all_scheduled_
    tasks just took off the schedule -- executing tasks, downtime blocks,
    and completed tasks are deliberately NOT included (see TDSManager.
    wipe_all_scheduled_tasks) and are left exactly where they are on each
    resource's timeline; the CP model treats them as fixed anchors (see
    build_and_solve) and this function only ever inserts the free
    removed_tasks around them.

    Any task the solver placed but that fails to replay into the STN is
    treated as a modeling bug (same reasoning as generate_initial_schedule_
    cp): the CP model already decided it fits, so a real STN insertion
    failure means the model's constraints don't actually match the STN's.

    Returns the list of removed_tasks the solver could not place anywhere
    (marked 'aborted', same convention as the greedy full_regen path).
    """
    status, resource_orders, presence = build_and_solve(
        tds, removed_tasks=removed_tasks, time_limit_seconds=time_limit_seconds, num_workers=num_workers
    )
    tds.cp_solve_log.append(('regen', status))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT could not find any feasible regenerated schedule (status={status}).")

    removed_task_names = {t.name for t in removed_tasks}

    for resource_name, ordered_tasks in resource_orders.items():
        resource = tds.resources[resource_name]
        prev_task = resource.timeline.tasks[0]
        for task in ordered_tasks:
            if task.name not in removed_task_names:
                # Already on the timeline (fixed anchor: executing, downtime,
                # or completed) -- nothing to insert, just advance the
                # insertion point past it.
                prev_task = task
                continue
            ok = resource.insert_task_to_timeline(task, task.capability, prev_task=prev_task, generate_travel=True)
            if not ok:
                raise RuntimeError(
                    f"CP-SAT selected {task.name} on {resource.name} but replaying it into the STN "
                    f"produced an inconsistency. This means the CP model's constraints (release/due/"
                    f"duration/travel-time, or the fixed-task anchoring) don't actually match what the "
                    f"STN enforces on insertion -- fix the model rather than silently dropping the task."
                )
            prev_task = task

    dropped_tasks = []
    for task in removed_tasks:
        if not presence[task.name]:
            task.status = 'aborted'
            dropped_tasks.append(task)

    return dropped_tasks
