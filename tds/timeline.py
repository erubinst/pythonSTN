import numpy as np
import pandas as pd

class Timeline:
    def __init__(self, resource, tds_manager):
        self.resource = resource
        self.tds = tds_manager
        self.tasks = []

    def append_task(self, task, min_gap=0, max_gap=np.inf):
        """Append a task at the end and post end->start constraint from previous task if present."""
        if self.tasks:
            prev = self.tasks[-1]
            # prev.end -> task.start constraint
            task.constrain_after(prev, min_gap=min_gap, max_gap=max_gap)
        self.tasks.append(task)

    def insert_task_after(self, previous_task, new_task, min_gap=0, max_gap=np.inf):
        idx = self.tasks.index(previous_task)
        # insert into list
        self.tasks.insert(idx+1, new_task)
        # link prev -> new
        new_task.constrain_after(previous_task,min_gap=min_gap, max_gap=max_gap)
        # if there is a next task, re-link new -> next (we add minimal 0 gap)
        if idx+2 < len(self.tasks):
            next_task = self.tasks[idx+2]
            new_task.constrain_before(next_task, min_gap=0)

    def export_to_df(self):
        """
        Export the timeline tasks to a pandas DataFrame.
        Columns:
        resource, task_name, start_lb, start_ub, end_lb, end_ub, capability
        """
        rows = []

        for task in self.tasks:
            # Find which capability is assigned to this timeline's resource
            cap_for_resource = None
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
    

