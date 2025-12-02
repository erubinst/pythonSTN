import numpy as np
import pandas as pd
from task import Task
from config import MIN_HOME_TIME

class Timeline:
    def __init__(self, resource, tds_manager):
        self.resource = resource
        self.tds = tds_manager
        self.tasks = []
    
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
        footer_task = Task(
            name=f"{self.resource.name}_footer",
            capabilities=[f'{self.resource.name}_presence'],
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds,
        )
        footer_task.add_time_window_constraints(global_end, global_end+1)
        footer_task.add_duration_constraint(1)
        self.resource.insert_task_to_timeline(footer_task, f'{self.resource.name}_presence', prev_task=header_task, generate_travel=generate_travel)


    def insert_task(self, task, prev_task=None, generate_travel=True):
        # TODO: If any of these operations don't work, we have to undo all changes made to add the task, including generating travel
        if prev_task is None:
            #TODO search for slot
            pass
        else:
            prev_task_idx = self.tasks.index(prev_task)
            next_task = self.tasks[prev_task_idx + 1] if prev_task_idx + 1 < len(self.tasks) else None
            self.tasks.insert(prev_task_idx + 1, task)
            task.constrain_after(prev_task, (self.resource, "sequence"))
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
        task_idx = self.tasks.index(curr_task)
        prev_task = self.tasks[task_idx - 1]
        if prev_task.name.startswith('pickup_from_') or prev_task.name.startswith('dropoff_at_'):
            return
        # next_task = self.tasks[task_idx + 1] 

        prev_task_location = prev_task.locations[-1]
        curr_task_start_location = curr_task.locations[0]
        if prev_task_location != curr_task_start_location:
            self.generate_pickup_dropoff(prev_task, curr_task)

        # curr_task_end_location = curr_task.locations[-1]
        # next_task_location = next_task.locations[0]

        # if curr_task_end_location != next_task_location:
        #     self.generate_pickup_dropoff(curr_task, next_task)


    def generate_pickup_dropoff(self, prev_task, curr_task):
        pickup_task = Task(
            name=f'pickup_from_{prev_task.name}_{self.resource.name}',
            capabilities=[f'{self.resource.name}_presence', 'transport'],
            locations=[prev_task.locations[-1], prev_task.locations[-1]],
            tds_manager = self.tds
        )
        pickup_task.add_duration_constraint(0)
        pickup_task.add_time_window_constraints(prev_task.end.lb, curr_task.start.ub)
        self.resource.insert_task_to_timeline(pickup_task, f'{self.resource.name}_presence', prev_task=prev_task, generate_travel=True)

        dropoff_task = Task(
            name=f'dropoff_at_{curr_task.name}_{self.resource.name}',
            capabilities=[f'{self.resource.name}_presence', 'transport'],
            locations=[curr_task.locations[0], curr_task.locations[0]],
            tds_manager = self.tds
        )
        dropoff_task.add_duration_constraint(0)
        dropoff_task.add_time_window_constraints(prev_task.end.lb, curr_task.start.ub)
        self.resource.insert_task_to_timeline(dropoff_task, f'{self.resource.name}_presence', prev_task=pickup_task, generate_travel=True)

        travel_duration = self.tds.travel_matrix[prev_task.locations[-1]][curr_task.locations[0]]

        dropoff_task.constrain_after(pickup_task, (self.resource.name, "transport"), travel_duration)


    def check_all_travel_slots(self):
        """
        TODO: To support incremental task additions, 
        need to recheck all slots during insertion 
        to see if there is still enough space for return home
        """
        pass


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
            for cap, res in task.assigned_resources.items():
                if res == self.resource.name:
                    cap_for_resource = cap
                    break

            rows.append({
                "resource": self.resource.name,
                "task_name": task.name,
                "start_lb": np.abs(task.start.lb),
                "start_ub": task.start.ub,
                "end_lb": np.abs(task.end.lb),
                "end_ub": task.end.ub,
                "capability": cap_for_resource
            })

        df = pd.DataFrame(rows)
        return df

    def __repr__(self):
        seq = " → ".join(t.name for t in self.tasks)
        return f"<Timeline {self.resource.name}: {seq}>"
    

