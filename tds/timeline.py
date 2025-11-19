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
        header_task = Task(
            name=f"{self.resource.name}_header",
            capabilities=[],
            tds_manager=self.tds,
        )
        header_task.add_time_window_constraints(global_start, global_start+1)
        header_task.add_duration_constraint(1)
        self.tasks.append(header_task)
        footer_task = Task(
            name=f"{self.resource.name}_footer",
            capabilities=[],
            tds_manager=self.tds,
        )
        footer_task.add_time_window_constraints(global_end, global_end+1)
        footer_task.add_duration_constraint(1)
        self.insert_task(footer_task, prev_task=header_task)

    def insert_task(self, task, prev_task=None):
        if prev_task is None:
            #TODO search for slot
            pass
        else:
            prev_task_idx = self.tasks.index(prev_task)
            self.tasks.insert(prev_task_idx + 1, task)
            task.constrain_after(prev_task)
        self.tasks.append(task)


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
    

