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
            capabilities=[],
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds,
        )
        header_task.add_time_window_constraints(global_start, global_start+1)
        header_task.add_duration_constraint(1)
        self.tasks.append(header_task)
        footer_task = Task(
            name=f"{self.resource.name}_footer",
            capabilities=[],
            locations=[self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds,
        )
        footer_task.add_time_window_constraints(global_end, global_end+1)
        footer_task.add_duration_constraint(1)
        self.insert_task(footer_task, prev_task=header_task, generate_travel=generate_travel)


    def insert_task(self, task, prev_task=None, generate_travel=True):
        if prev_task is None:
            #TODO search for slot
            pass
        else:
            prev_task_idx = self.tasks.index(prev_task)
            next_task = self.tasks[prev_task_idx + 1] if prev_task_idx + 1 < len(self.tasks) else None
            self.tasks.insert(prev_task_idx + 1, task)
            task.constrain_after(prev_task)
            if next_task is not None:
                task.constrain_before(next_task)
                prev_task.remove_constraint_btwn(next_task, "sequence")
                prev_task.remove_constraint_btwn(next_task, "travel")
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
        task.constrain_after(prev_task, prev_travel_time, constraint_type="travel")

        if next_task is not None:
            curr_task_end_location = task.locations[-1]
            next_task_location = next_task.locations[0]

            after_travel_time = self.tds.travel_matrix[curr_task_end_location][next_task_location]
            next_task.constrain_after(task, after_travel_time, constraint_type="travel")

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
        task_idx = self.tasks.index(curr_task)
        prev_task = self.tasks[task_idx - 1]
        next_task = self.tasks[task_idx + 1] 

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
        prev_task.remove_constraint_btwn(curr_task, "travel")
        return_home_task = Task(
            name=f'home_after_{prev_task.name}',
            capabilities=[],
            locations = [self.resource.base_location, self.resource.base_location],
            tds_manager=self.tds
        )
        return_home_task.add_duration_constraint(1)
        self.insert_task(return_home_task, prev_task=prev_task, generate_travel=True)


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
    

