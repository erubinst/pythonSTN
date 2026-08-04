import numpy as np
import pandas as pd
from tds_slack.task import Task
from queue import deque
from tds_slack.utils import execute_undo_functions

class Timeline:
    def __init__(self, resource, tds_manager):
        self.resource = resource
        self.tds = tds_manager
        self.tasks = [] 
        self.last_executed_task = None
    
    def create_header_footer(self, global_start=0, global_end=10000):
        """Create header and footer tasks for the timeline."""
        header_task = Task(
            name=f"{self.resource.name}_header",
            capability=f'{self.resource.name}_presence',
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds,
        )
        header_task.add_time_window_constraints(global_start, global_start+1)
        header_task.add_duration_constraint(1)
        self.tasks.append(header_task)
        footer_task = Task(
            name=f"{self.resource.name}_footer",
            capability=f'{self.resource.name}_presence',
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds
        )
        footer_task.add_time_window_constraints(global_end, global_end+1)
        footer_task.add_duration_constraint(1)
        self.resource.insert_task_to_timeline(footer_task, f'{self.resource.name}_presence', prev_task=header_task)


    def generate_downtime(self, start, end, duration, location, prev_task, insert=True):
        downtime_task = Task(
            name=f'{self.resource.name}_downtime_{start}',
            capability=f'{self.resource.name}_presence',
            locations = [location, location],
            tds_manager = self.tds
        )
        downtime_task.add_time_window_constraints(start, end)
        downtime_task.add_duration_constraint(duration)
        if insert:
            self.resource.insert_task_to_timeline(downtime_task, f'{self.resource.name}_presence', prev_task=prev_task)
        return downtime_task
    

    def find_first_overlapping_task(self, new_task):
        # we need to include travel time when considering overlap as well
        # we can access travel time from task to new task and from new task to task but how do we know which one to use? we can check both and if either one causes overlap, we consider it overlapping
        
        new_task_start_location = new_task.locations[0]
        new_task_end_location = new_task.locations[-1]
        for task in self.tasks:
            task_start_location = task.locations[0]
            task_end_location = task.locations[-1]
            travel_time_to_new_task = self.tds.travel_matrix[task_end_location][new_task_start_location]
            travel_time_from_new_task = self.tds.travel_matrix[new_task_end_location][task_start_location]
            if (np.abs(task.start.lb) - travel_time_to_new_task < new_task.end.ub and np.abs(task.end.ub) + travel_time_from_new_task >np.abs(new_task.start.lb)):
                return task
        return None
    

    # not considering travel
    def find_overlapping_tasks(self, new_task):
        overlapping_tasks = []
        for task in self.tasks:
            if (np.abs(task.start.lb) < new_task.end.ub and task.end.ub > np.abs(new_task.start.lb)):
                # if not downtime or header/footer task, add to overlapping tasks
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                overlapping_tasks.append(task)
        return overlapping_tasks
    

    def find_preceding_task(self, new_task):
        preceding_tasks = [task for task in self.tasks if np.abs(task.end.lb) <= new_task.start.ub]
        if not preceding_tasks:
            return None
        return max(preceding_tasks, key=lambda t: np.abs(t.end.lb))

    
    def remove_task(self, task, generate_undo=False):
        task_idx = self.tasks.index(task)
        prev_task = self.tasks[task_idx - 1] if task_idx - 1 >= 0 else None
        next_task = self.tasks[task_idx + 1] if task_idx + 1 < len(self.tasks) else None
        undo_stack = deque()

        # Remove sequence constraints
        if prev_task:
            prev_task.remove_constraint_btwn(task, (self.resource.name, "sequence"))
            if generate_undo:
                undo_stack.append((f'restoring sequence btwn {prev_task.name} and {task.name}', lambda: prev_task.constrain_before(task, (self.resource.name, "sequence"))))
        if next_task:
            task.remove_constraint_btwn(next_task, (self.resource.name, "sequence"))
            if generate_undo:
                undo_stack.append((f'restoring sequence btwn {task.name} and {next_task.name}', lambda: task.constrain_before(next_task, (self.resource.name, "sequence"))))

        # Remove travel constraints if applicable
        if prev_task and next_task:
            prev_task_task_dur = self.tds.travel_matrix[prev_task.locations[-1]][task.locations[0]]
            prev_task.remove_constraint_btwn(task, (self.resource.name, "travel"))
            task_next_task_dur = self.tds.travel_matrix[task.locations[-1]][next_task.locations[0]]
            task.remove_constraint_btwn(next_task, (self.resource.name, "travel"))
            travel_duration = self.tds.travel_matrix[prev_task.locations[-1]][next_task.locations[0]]
            prev_task.constrain_before(next_task, (self.resource.name, "travel"), travel_duration)
            if generate_undo:
                undo_stack.append((f'restoring travel of {prev_task_task_dur} btwn {prev_task.name} and {task.name}', lambda: prev_task.constrain_before(task, (self.resource.name, "travel"), prev_task_task_dur)))
                undo_stack.append((f'restoring travel of {task_next_task_dur} btwn {task.name} and {next_task.name}', lambda: task.constrain_before(next_task, (self.resource.name, "travel"), task_next_task_dur)))
                undo_stack.append((f'removing travel btwn {prev_task.name} and {next_task.name}', lambda: prev_task.remove_constraint_btwn(next_task, (self.resource.name, "travel"))))
        # Add sequence constraint between prev_task and next_task if both exist
        if prev_task and next_task:
            prev_task.constrain_before(next_task, (self.resource.name, "sequence"))
            if generate_undo:
                undo_stack.append((f'removing sequence btwn {prev_task.name} and {next_task.name}', lambda: prev_task.remove_constraint_btwn(next_task, (self.resource.name, "sequence"))))

        # Finally remove the task from the timeline
        self.tasks.remove(task)
        if generate_undo:
            undo_stack.append((f'restoring {task.name} to {self.resource.name} timeline list', lambda: self.tasks.insert(task_idx, task)))
        # set task to unscheduled
        task.status = "unscheduled"
        if generate_undo:
            undo_stack.append((f'setting {task.name} status back to scheduled', lambda: setattr(task, 'status', 'scheduled')))

        # remove constraint to now point if now point exists
        if self.tds.now is not None:
            self.tds.now.delete_constraint(task.start, ('all', 'start_after_now'))
            if generate_undo:
                undo_stack.append((f'restoring start_after_now constraint for {task.name}', lambda: self.tds.now.add_constraint(task.start, ('all', 'start_after_now'), min_gap=0, max_gap=np.inf)))

        return undo_stack if generate_undo else None


    def insert_task(self, task, prev_task=None, generate_travel=True, return_affected_timepoint=False):
        # TODO: If any of these operations don't work, we have to undo all changes made to add the task, including generating travel
        if prev_task is None:
            #TODO search for slot
            pass
        else:
            undo_stack = deque()
            prev_task_idx = self.tasks.index(prev_task)
            next_task = self.tasks[prev_task_idx + 1] if prev_task_idx + 1 < len(self.tasks) else None
            self.tasks.insert(prev_task_idx + 1, task)
            undo_stack.append((f'removing {task.name} from {self.resource.name} timeline list', lambda: self.tasks.remove(task)))

            affected_tp = None
            constraint1_result = task.constrain_after(
                prev_task,
                (self.resource.name, "sequence"),
                print_inconsistencies=True,
                return_affected_timepoint=return_affected_timepoint,
            )
            if return_affected_timepoint:
                constraint1, affected_tp = constraint1_result
            else:
                constraint1 = constraint1_result

            if not constraint1:
                execute_undo_functions(undo_stack)
                if return_affected_timepoint:
                    return False, affected_tp
                return False
            undo_stack.append((f'removing sequence btwn {prev_task.name} and {task.name}', lambda: prev_task.remove_constraint_btwn(task, (self.resource.name, "sequence"))))
            if next_task is not None:
                constraint2_result = task.constrain_before(
                    next_task,
                    (self.resource.name, "sequence"),
                    print_inconsistencies=True,
                    return_affected_timepoint=return_affected_timepoint,
                )
                if return_affected_timepoint:
                    constraint2, affected_tp = constraint2_result
                else:
                    constraint2 = constraint2_result

                if not constraint2:
                    execute_undo_functions(undo_stack)
                    if return_affected_timepoint:
                        return False, affected_tp
                    return False
                undo_stack.append((f'removing sequence btwn {task.name} and {next_task.name}', lambda: task.remove_constraint_btwn(next_task, (self.resource.name, "sequence"))))
                constraint3 = prev_task.remove_constraint_btwn(next_task, (self.resource.name,"sequence"))
                if not constraint3:
                    execute_undo_functions(undo_stack)
                    if return_affected_timepoint:
                        return False, None
                    return False
                undo_stack.append((f'restoring sequence btwn {prev_task.name} and {next_task.name}', lambda: prev_task.constrain_before(next_task, (self.resource.name, "sequence"))))
                travel_duration = self.tds.travel_matrix[prev_task.locations[-1]][next_task.locations[0]]
                constraint4 = prev_task.remove_constraint_btwn(next_task, (self.resource.name,"travel"))
                if not constraint4:
                    execute_undo_functions(undo_stack)
                    if return_affected_timepoint:
                        return False, None
                    return False
                undo_stack.append((f'restoring travel btwn {prev_task.name} and {next_task.name}', lambda: prev_task.constrain_before(next_task, (self.resource.name, "travel"), travel_duration)))
            if generate_travel:
                return self.generate_travel(
                    task,
                    undo_stack,
                    return_affected_timepoint=return_affected_timepoint,
                )

            if return_affected_timepoint:
                return True, None
            return True


    def generate_travel(self, task, undo_stack=None, return_affected_timepoint=False):
        task_idx = self.tasks.index(task)
        prev_task = self.tasks[task_idx - 1]
        next_task = self.tasks[task_idx + 1] if task_idx + 1 < len(self.tasks) else None

        prev_task_location = prev_task.locations[-1]
        curr_task_start_location = task.locations[0]

        prev_travel_time = self.tds.travel_matrix[prev_task_location][curr_task_start_location]
        # Routine for creating travel constraint
        # if travel edge already exists, overrwrite native to multidigraph based on keys
        affected_tp = None
        constraint1_result = task.constrain_after(
            prev_task,
            (self.resource.name, "travel"),
            prev_travel_time,
            print_inconsistencies=True,
            return_affected_timepoint=return_affected_timepoint,
        )
        if return_affected_timepoint:
            constraint1, affected_tp = constraint1_result
        else:
            constraint1 = constraint1_result

        if not constraint1:
            if undo_stack is not None:
                execute_undo_functions(undo_stack)
            if return_affected_timepoint:
                return False, affected_tp
            return False

        if undo_stack is not None:
            undo_stack.append((f'removing travel btwn {prev_task.name} and {task.name}', lambda: prev_task.remove_constraint_btwn(task, (self.resource.name, "travel"))))

        if next_task is not None:
            curr_task_end_location = task.locations[-1]
            next_task_location = next_task.locations[0]

            after_travel_time = self.tds.travel_matrix[curr_task_end_location][next_task_location]
            constraint2_result = next_task.constrain_after(
                task,
                (self.resource.name, "travel"),
                after_travel_time,
                print_inconsistencies=True,
                return_affected_timepoint=return_affected_timepoint,
            )
            if return_affected_timepoint:
                constraint2, affected_tp = constraint2_result
            else:
                constraint2 = constraint2_result

            if not constraint2:
                if undo_stack is not None:
                    execute_undo_functions(undo_stack)
                if return_affected_timepoint:
                    return False, affected_tp
                return False

        if return_affected_timepoint:
            return True, None
        return True
        # Routine for creating travel task
        # travel_task = Task(
        #     name=f"travel_{prev_task.name}_to_{task.name}",
        #     capability=f'{self.resource.name}_travel',
        #     locations=[prev_task_location, curr_task_location],
        #     tds_manager=self.tds,
        # )
        # travel_task.add_duration_constraint(travel_time)
        # self.insert_task(travel_task, prev_task=prev_task, generate_travel=False)


    def try_slot(self, new_task, prior_task):
        new_task_duration = new_task.get_duration()
        new_task_eft = new_task.end.lb
        prior_task_idx = self.tasks.index(prior_task)
        next_task_idx = prior_task_idx + 1
        post_task = self.tasks[next_task_idx] if next_task_idx < len(self.tasks) else None
        prior_eft = prior_task.end.lb
        to_travel = self.tds.travel_matrix[prior_task.locations[-1]][new_task.locations[0]]
        from_travel = 0
        if post_task is not None:
            from_travel = self.tds.travel_matrix[new_task.locations[-1]][post_task.locations[0]]
            post_task_lst = post_task.start.ub
            if post_task_lst < new_task_eft:
                return False
            
        if new_task_duration is not None and post_task is not None:
            post_task_lst = post_task.start.ub
            available_time = post_task_lst - prior_eft
            required_time = to_travel + new_task_duration + from_travel
            if available_time < required_time:
                return False
            
        return self.try_task_on_timeline(prior_task, new_task, post_task, to_travel, from_travel)
    

    # function to see if there is at least one feasible slot (for quick checks)
    def has_feasible_slot(self, new_task, starting_task=None, prior_slot=None):
        # write a new version of map_feasible_slots that breaks when a slot is found and returns True/False

        if starting_task is not None:
            prior_task = starting_task
        elif self.last_executed_task is not None:
            prior_task = self.last_executed_task
        else:
            prior_task = self.tasks[0]
        
        new_task_lst = new_task.start.ub
        prior_task_idx = self.tasks.index(prior_task)

        while prior_task is not None and not prior_task.name.endswith('_footer'):
            if prior_slot and prior_slot == prior_task:
                # skip this slot and move to the next one
                prior_task_idx += 1
                if prior_task_idx < len(self.tasks):
                    prior_task = self.tasks[prior_task_idx]
                else:
                    prior_task = None
                continue

            prior_eft = prior_task.end.lb
            if prior_eft > new_task_lst:
                break
            undo_stack = self.try_slot(new_task, prior_task)
            if undo_stack:
                execute_undo_functions(undo_stack)
                return True
            prior_task_idx += 1
            if prior_task_idx < len(self.tasks):
                prior_task = self.tasks[prior_task_idx]
            else:
                prior_task = None

        return False


    def map_feasible_slots(self, new_task, metrics, starting_task=None, prior_slot=None):
        if starting_task is not None:
            prior_task = starting_task
        elif self.last_executed_task is not None:
            prior_task = self.last_executed_task
        else:
            prior_task = self.tasks[0]

        new_task_lst = new_task.start.ub 
        results = []
        
        # Start scanning from starting_task
        prior_task_idx = self.tasks.index(prior_task)

        while prior_task is not None and not prior_task.name.endswith('_footer'):

            if prior_slot and prior_slot == prior_task:
                # skip this slot and move to the next one
                prior_task_idx += 1
                if prior_task_idx < len(self.tasks):
                    prior_task = self.tasks[prior_task_idx]
                else:
                    prior_task = None
                continue

            prior_eft = prior_task.end.lb
            if prior_eft > new_task_lst:
                break

            undo_stack = self.try_slot(new_task, prior_task)
            if undo_stack:
                results.append({
                    'task1_prior_task': prior_task,
                })
                # add in all metrics in metrics dict to above dict
                if 'slack' in metrics:
                    new_task_slack = new_task.get_sliding_slack()
                    results[-1]['slack'] = new_task_slack
                if 'travel' in metrics:
                    results[-1]['travel'] = self.tds.sum_total_travel()
                if 'flexibility' in metrics:
                    results[-1]['flexibility'] = self.tds.sum_total_flexibility()
                if 'makespan' in metrics:
                    results[-1]['makespan'] = self.tds.makespan()
                if 'slots' in metrics:
                    results[-1]['slots'] = self.tds.sum_total_slot()
                if 'total_slack' in metrics:
                    results[-1]['total_slack'] = self.tds.sum_total_slack()
                if 'max_slot' in metrics:
                    results[-1]['max_slot'] = self.tds.sum_max_slot_flexibility()
                if 'earliest_completion_time' in metrics:
                    results[-1]['earliest_completion_time'] = self.tds.sum_completion_time_diff()
                execute_undo_functions(undo_stack)

            prior_task_idx += 1
            if prior_task_idx < len(self.tasks):
                prior_task = self.tasks[prior_task_idx]
            else:
                prior_task = None

        return results            
                

    def try_task_on_timeline(self, prior_task, new_task, post_task, to_travel, from_travel):
        undo_stack = deque()

        prior_idx = self.tasks.index(prior_task)
        self.tasks.insert(prior_idx + 1, new_task)
        undo_stack.append((f'removing {new_task.name} from {self.resource.name} timeline list', lambda: self.tasks.remove(new_task)))

        # Constraint 1: sequence after prior_task
        constraint1 = new_task.constrain_after(prior_task, (self.resource.name, "sequence"), print_inconsistencies=False)
        if not constraint1:
            execute_undo_functions(undo_stack)
            return False
        undo_stack.append((f'removing sequence btwn {prior_task.name} and {new_task.name}', lambda: prior_task.remove_constraint_btwn(new_task, (self.resource.name, "sequence"))))

        # Constraint 2: travel after prior_task
        constraint2 = new_task.constrain_after(prior_task, (self.resource.name, "travel"), to_travel, print_inconsistencies=False)
        if not constraint2:
            execute_undo_functions(undo_stack)
            return False
        undo_stack.append((f'removing travel btwn {prior_task.name} and {new_task.name}', lambda: prior_task.remove_constraint_btwn(new_task, (self.resource.name, "travel"))))

        if post_task:
            # FIRST: Capture the old constraint values
            ub_seq = prior_task.end.ub_edge_weight(post_task.start, (self.resource.name, "sequence"))
            lb_seq = -prior_task.end.lb_edge_weight(post_task.start, (self.resource.name, "sequence"))
            travel_lb = -prior_task.end.lb_edge_weight(post_task.start, (self.resource.name, "travel"))
            
            # SECOND: Remove old constraints (relaxations - always succeed)
            prior_task.remove_constraint_btwn(post_task, (self.resource.name, "sequence"))
            # Capture values in lambda with default arguments
            undo_stack.append((f'restoring sequence btwn {prior_task.name} and {post_task.name}', lambda lb=lb_seq, ub=ub_seq: 
                            prior_task.restore_constraint_btwn(post_task, (self.resource.name, "sequence"), 
                                                            min_gap=lb, max_gap=ub)))
            
            prior_task.remove_constraint_btwn(post_task, (self.resource.name, "travel"))
            undo_stack.append((f'restoring travel btwn {prior_task.name} and {post_task.name}', lambda lb=travel_lb: 
                            prior_task.restore_constraint_btwn(post_task, (self.resource.name, "travel"), 
                                                            min_gap=lb)))
            
            # THIRD: Add new constraints (these can fail)
            constraint3 = post_task.constrain_after(new_task, (self.resource.name, "sequence"), print_inconsistencies=False)
            if not constraint3:
                execute_undo_functions(undo_stack)
                return False            
            undo_stack.append((f'removing sequence btwn {new_task.name} and {post_task.name}', lambda: new_task.remove_constraint_btwn(post_task, (self.resource.name, "sequence"))))

            constraint4 = post_task.constrain_after(new_task, (self.resource.name, "travel"), from_travel, print_inconsistencies=False)
            if not constraint4:
                execute_undo_functions(undo_stack)
                return False
            undo_stack.append((f'removing travel btwn {new_task.name} and {post_task.name}', lambda: new_task.remove_constraint_btwn(post_task, (self.resource.name, "travel"))))
        # set task to scheduled
        new_task.status = "scheduled"
        undo_stack.append((f'setting {new_task.name} status back to unscheduled', lambda: setattr(new_task, 'status', 'unscheduled')))
        return undo_stack


    def export_to_df(self):
        """
        Export the timeline tasks to a pandas DataFrame.
        Columns:
        resource, task_name, start_lb, start_ub, end_lb, end_ub, capability
        """
        rows = []
        for task in self.tasks:
            # Find which capability is assigned to this timeline's resource
            # TODO Add resource capability here
            if task.name.endswith('_header') or task.name.endswith('_footer'):
                slot_flexibility = "N/A"
            else:
                slot_flexibility = task.get_slot_flexibility()

            rows.append({
                "resource": self.resource.name,
                "task_name": task.name,
                "start_lb": np.abs(task.start.lb),
                "start_ub": task.start.ub,
                "end_lb": np.abs(task.end.lb),
                "end_ub": task.end.ub,
                "capability": task.capability,
                "location": task.locations[0],
                "duration": task.get_duration(),
                "slot_flexibility": slot_flexibility,
                "slack": task.get_sliding_slack()
            })

            # find travel constraint for ahead task unless on last task
            task_idx = self.tasks.index(task)
            if task_idx + 1 < len(self.tasks):
                next_task = self.tasks[task_idx + 1]
                travel_lb = np.abs(task.end.lb_edge_weight(next_task.start, (self.resource.name, "travel")))
                if travel_lb != np.inf and travel_lb != 0:
                    rows.append({
                        "resource": self.resource.name,
                        "task_name": f"travel_{task.name}_to_{next_task.name}",
                        "start_lb": np.abs(task.end.lb),
                        "start_ub": next_task.start.ub - travel_lb,
                        "end_lb": np.abs(task.end.lb) + travel_lb,
                        "end_ub": next_task.start.ub,
                        "capability": "travel",
                        "location": None,
                        "duration": task.get_duration(),
                        "slot_flexibility": "N/A",
                        "slack": "N/A"
                    })

        df = pd.DataFrame(rows)
        return df

    def __repr__(self):
        seq = " → ".join(t.name for t in self.tasks)
        return f"<Timeline {self.resource.name}: {seq}>"
    

