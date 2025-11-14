from stn.stn import STN
from timepoint import Timepoint
import pandas as pd

class TDSManager:
    def __init__(self):
        self.stn = STN()
        self.resources = {}     # name -> Resource
        self.tasks = {}         # name or order -> Task
        self.cz = Timepoint('zero', self, add_to_stn=False)

    def add_task_to_manager(self, task):
        """Register a task with the manager."""
        if task.name in self.tasks:
            raise ValueError(f"Task '{task.name}' already exists")
        self.tasks[task.name] = task
    
    def add_resource_to_manager(self, resource):
        """Register a resource."""
        self.resources[resource.name] = resource

    def export_to_df(self):
        df = pd.DataFrame()
        for resource in self.resources.values():
            timeline_df = resource.timeline.export_to_df()
            df = pd.concat([df, timeline_df])
        return df
