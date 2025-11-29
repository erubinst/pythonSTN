import numpy as np
import pandas as pd
from task import Task

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
            if generate_travel:
                self.generate_travel(task)


    def generate_travel(self, task):
        prev_task = self.tasks[self.tasks.index(task) - 1]
        prev_task_location = prev_task.locations[-1]
        curr_task_location = task.locations[0]
        try:
            travel_time = self.tds.travel_matrix[prev_task_location][curr_task_location]
        except KeyError:
            print(f"Warning: travel time from '{prev_task_location}' to '{curr_task_location}' not found; assuming 0")
            travel_time = 0

        # Routine for creating travel constraint
        task.constrain_after(prev_task, travel_time, constraint_type="travel")

        # Routine for creating travel task
        # travel_task = Task(
        #     name=f"travel_{prev_task.name}_to_{task.name}",
        #     capabilities=[],
        #     locations=[prev_task_location, curr_task_location],
        #     tds_manager=self.tds,
        # )
        # travel_task.add_duration_constraint(travel_time)
        # self.insert_task(travel_task, prev_task=prev_task, generate_travel=False)


    # temporary for visibility
    def surface_transports(self):
        for task in self.tasks:
            if task.locations[0] != self.resource.base_location:
                print(f'Transportation for {task.name}')
                print(f'Need pickup at {self.resource.base_location}')
                print(f'Need dropoff at {task.locations[0]}')
                print('\n')


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
    

