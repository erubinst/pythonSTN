import numpy as np
import pandas as pd
from .task import Task
from .config import MIN_HOME_TIME
from queue import deque
from .utils import execute_undo_functions

class Timeline:
    def __init__(self, resource, tds_manager):
        self.resource = resource
        self.tds = tds_manager
        self.tasks = [] 
        self.capability_assigned = []
    
    def create_header_footer(self, global_start=0, global_end=np.inf):
        """Create header and footer tasks for the timeline."""
        generate_travel = generate_travel = True if 'traveler' in self.resource.capabilities else False
        header_task = Task(
            name=f"{self.resource.name}_header",
            capabilities=[f'{self.resource.name}_presence'],
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds,
        )
        header_task.add_time_window_constraints(global_start, global_start+1)
        header_task.add_duration_constraint(1)
        self.tasks.append(header_task)
        self.capability_assigned.append(f'{self.resource.name}_presence')
        footer_task = Task(
            name=f"{self.resource.name}_footer",
            capabilities=[f'{self.resource.name}_presence'],
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds
        )
        footer_task.add_time_window_constraints(global_end, global_end+1)
        footer_task.add_duration_constraint(1)
        self.resource.insert_task_to_timeline(footer_task, f'{self.resource.name}_presence', prev_task=header_task, generate_travel=generate_travel)


    def generate_downtime(self, start, end, duration, location, prev_task):
        generate_travel = True if 'traveler' in self.resource.capabilities else False
        downtime_task = Task(
            name=f'{self.resource.name}_downtime_{start}',
            capabilities = [f'{self.resource.name}_presence'],
            locations = [location, location],
            tds_manager = self.tds
        )
        downtime_task.add_time_window_constraints(start, end)
        downtime_task.add_duration_constraint(duration)
        self.resource.insert_task_to_timeline(downtime_task, f'{self.resource.name}_presence', prev_task=prev_task, generate_travel=generate_travel)
        return downtime_task

    
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
            prev_task.remove_constraint_btwn(task, (self.resource.name, "travel"))
            task.remove_constraint_btwn(next_task, (self.resource.name, "travel"))
            travel_duration = self.tds.travel_matrix[prev_task.locations[-1]][next_task.locations[0]]
            prev_task.constrain_before(next_task, (self.resource.name, "travel"), travel_duration)
            if generate_undo:
                undo_stack.append((f'restoring travel btwn {prev_task.name} and {task.name}', lambda: prev_task.constrain_before(task, (self.resource.name, "travel"), travel_duration)))
                undo_stack.append((f'restoring travel btwn {task.name} and {next_task.name}', lambda: task.constrain_before(next_task, (self.resource.name, "travel"), travel_duration)))
                undo_stack.append((f'removing travel btwn {prev_task.name} and {next_task.name}', lambda: prev_task.remove_constraint_btwn(next_task, (self.resource.name, "travel"))))
        # Add sequence constraint between prev_task and next_task if both exist
        if prev_task and next_task:
            prev_task.constrain_before(next_task, (self.resource.name, "sequence"))
            if generate_undo:
                undo_stack.append((f'removing sequence btwn {prev_task.name} and {next_task.name}', lambda: prev_task.remove_constraint_btwn(next_task, (self.resource.name, "sequence"))))

        # Finally remove the task from the timeline
        self.tasks.remove(task)
        self.capability_assigned.pop(task_idx)
        if generate_undo:
            undo_stack.append((f'restoring {task.name} to {self.resource.name} timeline list', lambda: self.tasks.insert(task_idx, task)))
            undo_stack.append((f'restoring {task.capabilities[0]} to {self.resource.name} timeline list', lambda: self.capability_assigned.insert(task_idx, task.capabilities[0])))

        return undo_stack if generate_undo else None

    def print_tasks(self):
        task_names = []
        for task in self.tasks:
            task_names.append(task.name)
        print(task_names)

    def list_task_names(self):
        task_lst = []
        for task in self.tasks:
            task_lst.append(task.name)
        return task_lst

    def insert_task(self, task, capability, prev_task=None, generate_travel=True):
        # TODO: If any of these operations don't work, we have to undo all changes made to add the task, including generating travel
        if prev_task is None:
            #TODO search for slot
            pass
        else:
            prev_task_idx = self.tasks.index(prev_task)
            next_task = self.tasks[prev_task_idx + 1] if prev_task_idx + 1 < len(self.tasks) else None
            self.tasks.insert(prev_task_idx + 1, task)
            self.capability_assigned.insert(prev_task_idx + 1, capability)
            task.constrain_after(prev_task, (self.resource.name, "sequence"))
            if next_task is not None:
                task.constrain_before(next_task, (self.resource.name, "sequence"))
                prev_task.remove_constraint_btwn(next_task, (self.resource.name,"sequence"))
                prev_task.remove_constraint_btwn(next_task, (self.resource.name,"travel"))
            if generate_travel:
                self.generate_travel(task)


    def generate_travel(self, task):
        task_idx = self.tasks.index(task)
        prev_task = self.tasks[task_idx - 1]
        next_task = self.tasks[task_idx + 1] if task_idx + 1 < len(self.tasks) else None

        prev_task_location = prev_task.locations[-1]
        curr_task_start_location = task.locations[0]

        prev_travel_time = self.tds.travel_matrix[prev_task_location][curr_task_start_location]
        # Routine for creating travel constraint
        # if travel edge already exists, overrwrite native to multidigraph based on keys
        task.constrain_after(prev_task, (self.resource.name, "travel"), prev_travel_time)

        if next_task is not None:
            curr_task_end_location = task.locations[-1]
            next_task_location = next_task.locations[0]

            after_travel_time = self.tds.travel_matrix[curr_task_end_location][next_task_location]
            next_task.constrain_after(task, (self.resource.name, "travel"), after_travel_time)

        # Routine for creating travel task
        # travel_task = Task(
        #     name=f"travel_{prev_task.name}_to_{task.name}",
        #     capabilities=[],
        #     locations=[prev_task_location, curr_task_location],
        #     tds_manager=self.tds,
        # )
        # travel_task.add_duration_constraint(travel_time)
        # self.insert_task(travel_task, prev_task=prev_task, generate_travel=False)

    def try_slot_no_travel(self, new_task, prior_task, capability):
        new_task_duration = new_task.get_duration()
        new_task_eft = new_task.end.lb
        prior_task_idx = self.tasks.index(prior_task)
        next_task_idx = prior_task_idx + 1
        post_task = self.tasks[next_task_idx] if next_task_idx < len(self.tasks) else None
        prior_eft = prior_task.end.lb
        
        if post_task is not None:
            post_task_lst = post_task.start.ub
            available_time = post_task_lst - prior_eft
            required_time = new_task_duration
            if available_time < required_time:
                return False
            if new_task_eft > post_task_lst:
                return False
            
        return False
    
    def try_task_on_timeline_no_travel(self, prior_task, new_task, post_task, capability):
        undo_stack = deque()

        prior_idx = self.tasks.index(prior_task)
        self.tasks.insert(prior_idx + 1, new_task)
        self.capability_assigned.insert(prior_idx + 1, capability)
        undo_stack.append((f'removing {new_task.name} from {self.resource.name} timeline list', lambda: self.tasks.remove(new_task)))
        undo_stack.append((f'removing {capability} from {self.resource.name} timeline list', lambda: self.capability_assigned.pop(prior_idx + 1)))

        # Constraint 1: sequence after prior_task
        constraint1 = new_task.constrain_after(prior_task, (self.resource.name, "sequence"), print_inconsistencies=False)
        if not constraint1:
            execute_undo_functions(undo_stack)
            return False
        undo_stack.append((f'removing sequence btwn {prior_task.name} and {new_task.name}', lambda: prior_task.remove_constraint_btwn(new_task, (self.resource.name, "sequence"))))

        if post_task:
            # FIRST: Capture the old constraint values
            ub_seq = prior_task.end.ub_edge_weight(post_task.start, (self.resource.name, "sequence"))
            lb_seq = -prior_task.end.lb_edge_weight(post_task.start, (self.resource.name, "sequence"))
            
            # SECOND: Remove old constraints (relaxations - always succeed)
            prior_task.remove_constraint_btwn(post_task, (self.resource.name, "sequence"))
            # Capture values in lambda with default arguments
            undo_stack.append((f'restoring sequence btwn {prior_task.name} and {post_task.name}', lambda lb=lb_seq, ub=ub_seq: 
                            prior_task.restore_constraint_btwn(post_task, (self.resource.name, "sequence"), 
                                                            min_gap=lb, max_gap=ub)))
            
            # THIRD: Add new constraints (these can fail)
            constraint3 = post_task.constrain_after(new_task, (self.resource.name, "sequence"), print_inconsistencies=False)
            if not constraint3:
                execute_undo_functions(undo_stack)
                return False            
            undo_stack.append((f'removing sequence btwn {new_task.name} and {post_task.name}', lambda: new_task.remove_constraint_btwn(post_task, (self.resource.name, "sequence"))))

        return undo_stack


    def try_slot(self, new_task, prior_task, capability):
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
            
        return self.try_task_on_timeline(prior_task, new_task, post_task, capability, to_travel, from_travel)


    def map_feasible_slots(self, new_task, capability, starting_task=None):
        if starting_task is None:
            starting_task = self.tasks[0] if self.tasks else None

        new_task_lst = new_task.start.ub 
        results = []
        
        # Start scanning from starting_task
        prior_task = starting_task
        prior_task_idx = self.tasks.index(prior_task)

        while prior_task is not None and not prior_task.name.endswith('_footer'):
            prior_eft = prior_task.end.lb
            if prior_eft > new_task_lst:
                break

            undo_stack = self.try_slot(new_task, prior_task, capability)
            if undo_stack:
                results.append({
                    'task1_prior_task': prior_task,
                    'total_travel': self.tds.sum_total_travel()
                })
                execute_undo_functions(undo_stack)

            prior_task_idx += 1
            if prior_task_idx < len(self.tasks):
                prior_task = self.tasks[prior_task_idx]
            else:
                prior_task = None

        return results            


    def map_feasible_slots_linked_tasks(self, task1, task2, starting_task=None):
        # use for pickup/dropoff
        if starting_task is None:
            starting_task = self.tasks[0] if self.tasks else None
        
        results = []

        prior1_task = starting_task
        prior1_task_idx = self.tasks.index(prior1_task)
        task1_lst = task1.start.ub
        task2_lst = task2.start.ub
        
        while prior1_task is not None and not prior1_task.name.endswith('_footer'):
            prior1_eft = prior1_task.end.lb
            if prior1_eft > task1_lst:
                break
            
            undo1_stack = self.try_slot(task1, prior1_task, 'transport')
            if undo1_stack:
                # Reset prior2_task for each task1 placement
                prior2_task = task1  # Start from task1, not starting_task
                prior2_task_idx = self.tasks.index(prior2_task)
                
                while prior2_task is not None and not prior2_task.name.endswith('_footer'):
                    prior2_eft = prior2_task.end.lb
                    if prior2_eft > task2_lst:
                        break
                    undo2_stack = self.try_slot(task2, prior2_task, 'transport')
                    if undo2_stack:
                        results.append({
                            'task1_prior_task': prior1_task,
                            'task2_prior_task': prior2_task,
                            'total_travel': self.tds.sum_total_travel()
                        })
                        execute_undo_functions(undo2_stack)

                    prior2_task_idx += 1
                    if prior2_task_idx < len(self.tasks):
                        prior2_task = self.tasks[prior2_task_idx]
                    else:
                        prior2_task = None
                execute_undo_functions(undo1_stack)
                
            prior1_task_idx += 1
            if prior1_task_idx < len(self.tasks):
                prior1_task = self.tasks[prior1_task_idx]
            else:
                prior1_task = None
        return results
                

    def try_task_on_timeline(self, prior_task, new_task, post_task, capability, to_travel, from_travel):
        undo_stack = deque()

        prior_idx = self.tasks.index(prior_task)
        self.tasks.insert(prior_idx + 1, new_task)
        self.capability_assigned.insert(prior_idx + 1, capability)
        undo_stack.append((f'removing {new_task.name} from {self.resource.name} timeline list', lambda: self.tasks.remove(new_task)))
        undo_stack.append((f'removing {capability} from {self.resource.name} timeline list', lambda: self.capability_assigned.pop(prior_idx + 1)))

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
        return undo_stack


    def add_return_stops(self, curr_task):
        # not intended for use on header or footer task !!!
        task_idx = self.tasks.index(curr_task)
        prev_task = self.tasks[task_idx - 1]
        next_task = self.tasks[task_idx + 1] 

        # if any task starts with home_after, skip - this is also checked when you check if you are at home
        if curr_task.name.startswith('home_after_') or prev_task.name.startswith('home_after_') or next_task.name.startswith('home_after_'):
            return

        prev_task_location = prev_task.locations[-1]
        curr_task_start_location = curr_task.locations[0]
        
        # max time between before and now - do we have to remove travel first?
        max_time = curr_task.start.ub - np.abs(prev_task.end.lb)
        time_home = self.tds.travel_matrix[prev_task_location][self.resource.base_location]
        time_back = self.tds.travel_matrix[self.resource.base_location][curr_task_start_location]
        # neither location can be home
        not_at_home = time_home != 0 and time_back != 0
        if (time_home + time_back + MIN_HOME_TIME < max_time) and (not_at_home):
            self.generate_return_home_task(prev_task, curr_task)

        # max time between now and after
        curr_task_end_location = curr_task.locations[-1]
        next_task_location = next_task.locations[0]

        max_time = next_task.start.ub - np.abs(curr_task.end.lb)
        time_home = self.tds.travel_matrix[curr_task_end_location][self.resource.base_location]
        time_back = self.tds.travel_matrix[self.resource.base_location][next_task_location]
        not_at_home = time_home != 0 and time_back != 0
        if (time_home + time_back + MIN_HOME_TIME < max_time) and (not_at_home):
            self.generate_return_home_task(curr_task, next_task)


    def generate_return_home_task(self, prev_task, curr_task):
        # TODO: If any of these operations fail, need to undo all changes made
        prev_task.remove_constraint_btwn(curr_task, (self.resource.name, "travel"))
        return_home_task = Task(
            name=f'home_after_{prev_task.name}_{self.resource.name}',
            capabilities=[f'{self.resource.name}_presence'],
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds
        )
        return_home_task.add_duration_constraint(1)
        self.resource.insert_task_to_timeline(return_home_task, f'{self.resource.name}_presence', prev_task=prev_task, generate_travel=True)


    def add_pickup_dropoffs(self, curr_task):
        # not intended for use on header or footer task !!!
        # used for the timeline that NEEDS the pickup/dropoff, not the driver
        pickup, dropoff = None, None
        task_idx = self.tasks.index(curr_task)
        prev_task = self.tasks[task_idx - 1]
        if prev_task.name.startswith('pickup_from_') or prev_task.name.startswith('dropoff_at_'):
            return

        prev_task_location = prev_task.locations[-1]
        curr_task_start_location = curr_task.locations[0]
        if prev_task_location != curr_task_start_location:
            pickup, dropoff,_ = self.generate_possible_pickup_dropoff(prev_task, curr_task)
        if pickup:
            self.resource.insert_task_to_timeline(pickup, f'{self.resource.name}_presence', prev_task=prev_task, generate_travel=True)
        if dropoff:
            self.resource.insert_task_to_timeline(dropoff, f'{self.resource.name}_presence', prev_task=pickup, generate_travel=True)




    def generate_possible_pickup_dropoff(self, prev_task, task):
        # TODO: Support undo if does not work
        undo_stack = deque()
        pickup_task = Task(
            name=f'pickup_from_{prev_task.name}_{self.resource.name}',
            capabilities=[f'{self.resource.name}_presence', 'transport'],
            locations=[prev_task.locations[-1], prev_task.locations[-1]],
            tds_manager = self.tds
        )
        pickup_task.add_duration_constraint(0)
        pickup_task.add_time_window_constraints(np.abs(prev_task.end.lb), task.start.ub)
        undo_stack.append((f'deleting {pickup_task.name}', lambda: pickup_task.delete_task()))
        # self.resource.insert_task_to_timeline(pickup_task, f'{self.resource.name}_presence', prev_task=prev_task, generate_travel=True)
        # undo_stack.append(lambda: self.resource.timeline.tasks.remove(pickup_task))
        dropoff_task = Task(
            name=f'dropoff_at_{task.name}_{self.resource.name}',
            capabilities=[f'{self.resource.name}_presence', 'transport'],
            locations=[task.locations[0], task.locations[0]],
            tds_manager = self.tds
        )
        dropoff_task.add_duration_constraint(0)
        dropoff_task.add_time_window_constraints(np.abs(prev_task.end.lb), task.start.ub)
        undo_stack.append((f'deleting {dropoff_task.name}', lambda: dropoff_task.delete_task()))
        # undo_stack.append(lambda: self.resource.timeline.tasks.remove(dropoff_task))
        # self.resource.insert_task_to_timeline(dropoff_task, f'{self.resource.name}_presence', prev_task=pickup_task, generate_travel=True)
        # travel_duration = self.tds.travel_matrix[prev_task.locations[-1]][curr_task.locations[0]]

        # dropoff_task.constrain_after(pickup_task, (self.resource.name, "travel"), travel_duration)
        # TODO: maybe add constraints on max wait time but this will be dependent on which tasks are at home or not
        # TODO: Also maybe add max ride time constraints
        return pickup_task, dropoff_task, undo_stack
    
    
    def find_same_task_groups(self):
        grouped_tasks = []
        curr_group = []
        
        for i in range (len(self.tasks)):
            task = self.tasks[i]
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
            task_type = task.task_type

            if (curr_group == []):
                curr_group = [task]
                continue

            prev_task = curr_group [-1]
            prev_task_type = prev_task.task_type

            if ((prev_task_type==task_type) and (np.abs(task.start.lb) - np.abs(prev_task.end.lb) == 0)):
                curr_group.append(task)
            else:
                if (len(curr_group)>=2):
                    grouped_tasks.append(curr_group)
                curr_group = [task]
        if (len(curr_group)>=2):
                    grouped_tasks.append(curr_group)
        return grouped_tasks


    def export_to_df(self):
        """
        Export the timeline tasks to a pandas DataFrame.
        Columns:
        resource, task_name, start_lb, start_ub, end_lb, end_ub, capability
        """
        rows = []

        for task in self.tasks:
            # Find which capability is assigned to this timeline's resource
            cap_for_resource = "N/A"
            # TODO Add resource capability here

            rows.append({
                "resource": self.resource.name,
                "task_name": task.name,
                "start_lb": np.abs(task.start.lb),
                "start_ub": task.start.ub,
                "end_lb": np.abs(task.end.lb),
                "end_ub": task.end.ub,
                "capability": cap_for_resource,
                "location": task.locations[0]
            })

            # find travel constraint for ahead task unless on last task
            task_idx = self.tasks.index(task)
            if task_idx + 1 < len(self.tasks):
                next_task = self.tasks[task_idx + 1]
                travel_lb = np.abs(task.end.lb_edge_weight(next_task.start, (self.resource.name, "travel")))
                if travel_lb != np.inf and travel_lb != 0:
                    # adding 0.5 for display purposes to show the 0 time pickup dropoff
                    rows.append({
                        "resource": self.resource.name,
                        "task_name": f"travel_{task.name}_to_{next_task.name}",
                        "start_lb": np.abs(task.end.lb),
                        "start_ub": next_task.start.ub - travel_lb,
                        "end_lb": np.abs(task.end.lb) + travel_lb,
                        "end_ub": next_task.start.ub,
                        "capability": "travel",
                        "location": None
                    })

        df = pd.DataFrame(rows)
        return df

    def __repr__(self):
        seq = " → ".join(t.name for t in self.tasks)
        return f"<Timeline {self.resource.name}: {seq}>"
    

