from timeline import Timeline
from config import *
import pandas as pd
import numpy as np

class Resource:
    def __init__(self, name, capabilities, base_location, tds_manager):
        self.name = name.lower()
        self.capabilities = {c.lower() for c in capabilities}
        self.tds = tds_manager
        self.base_location = base_location
        self.timeline = Timeline(self, tds_manager)

        self.tds.add_resource_to_manager(self)
        self.timeline.create_header_footer(GLOBAL_START, GLOBAL_END)

    def insert_task_to_timeline(self, task, capability, prev_task=None, generate_travel=True):
        # Ensure task is appended via timeline (this will add STN ordering when prev task given)
        if capability not in self.capabilities:
            raise ValueError(f"Resource '{self.name}' does not have capability '{capability}'")
        self.timeline.insert_task(task, capability, prev_task, generate_travel=generate_travel)

    def remove_task_from_timeline(self, task):
        self.timeline.remove_task(task)

    def has_capability(self, capability):
        return capability in self.capabilities
    
    def generate_transport_requests(self):
        # TODO: change this to be a proper request file format
        """
        Returns a DataFrame containing transport request rows.
        """
        rows = [] 
        for i in range(1, len(self.timeline.tasks)):
            prev_task = self.timeline.tasks[i-1]
            curr_task = self.timeline.tasks[i]

            if prev_task.locations[-1] != curr_task.locations[0]:
                job_data = {
                    "resource_name": self.name,
                    "job_name": f'{prev_task.name}_to_{curr_task.name}',
                    "pickup_location": prev_task.locations[-1],
                    "dropoff_location": curr_task.locations[0],
                    "prior_task": prev_task.name,
                    "next_task": curr_task.name,
                    "travel_time": self.tds.travel_matrix[prev_task.locations[-1]][curr_task.locations[0]]
                }

                rows.append(job_data)

        df = pd.DataFrame(rows)
        return df

    def __repr__(self):
        return f"<Resource {self.name} caps={list(self.capabilities)}>"


