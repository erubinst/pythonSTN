from stn.stn import STN
from tds_slack.timepoint import Timepoint
import pandas as pd
import numpy as np

class TDSManager:
    def __init__(self, travel_matrix=None):
        self.stn = STN()
        self.resources = {}     # name -> Resource
        self.tasks = {}         # name or order -> Task
        self.cz = Timepoint('zero', self, add_to_stn=False)
        self.travel_matrix = travel_matrix


    def find_task_by_timepoint(self, stn_tp):
        for task in self.tasks.values():
            if stn_tp == task.start.name or stn_tp == task.end.name:
                return task
        return None
    

    def sort_tasks_by_flexibility(self, task_lst=None):
        if task_lst is None:
            task_lst = self.tasks.values()
        flex_list = []
        for task in task_lst:
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            flex_list.append((task.get_task_starting_flexibility(),task.name,task))
        flex_list.sort()
        sorted_tasks = []
        for _,_,task in flex_list:
            sorted_tasks.append(task)
        return sorted_tasks
    

    def sum_total_flexibility(self):
        total_flexibility = 0
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            total_flexibility += task.get_task_flexibility()
        return total_flexibility


    def sum_total_travel(self):
        total_travel_weight = sum(
            np.abs(data.get("weight", 0))
            for _, _, key, data in self.stn.edges(keys=True, data=True)
            if (
                isinstance(key, tuple)
                and len(key) > 1
                and key[1] == "travel"
                and not np.isinf(data.get("weight", 0))
                and key[0] in self.resources
            )
        )
        return total_travel_weight                

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