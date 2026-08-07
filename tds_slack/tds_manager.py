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
        self.now = None  # will be set when simulation starts


    def print_now_edges(self):
        print('Edges incident to STN node "now":')
        for start_node, end_node, key, data in self.stn.edges("now", keys=True, data=True):
            print(f"  {start_node} -> {end_node} | key={key} | data={data}")


    def create_now_tp(self):
        """Create a timepoint to represent the current time in the simulation."""
        self.now = Timepoint('now', self, add_to_stn=True)
        # to start, now tp is constrained to be after the zero tp
        self.cz.add_constraint(self.now, ('all', 'now_after_zero'), min_gap=0, max_gap=np.inf)
        print(f"Created now timepoint at {np.abs(self.now.lb)}. Constrained to be after zero timepoint.")
        # all scheduled tasks will have their start timepoint constrained to be after the now timepoint
        for task in self.tasks.values():
            if task.status == 'scheduled' and not task.name.endswith('_header') and not task.name.endswith('_footer'):
                self.now.add_constraint(task.start, ('all', 'start_after_now'), min_gap=0, max_gap=np.inf)


    def update_now_tp(self, new_time):
        """Update the now timepoint to a new time."""
        # Rebuild the now-after-zero constraint instead of strengthening it in place.
        self.cz.add_constraint(self.now, ('all', 'now_after_zero'), min_gap=new_time, max_gap=np.inf)


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
    

    def sum_completion_time_diff(self):
        total_diff = 0
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            if self.now is not None:
                if task.status in ['scheduled', 'executing']:
                    total_diff += task.get_completion_time_diff()
            else:
                total_diff += task.get_completion_time_diff()
        return total_diff
    

    def makespan(self):
        max_time = 0
        for resource in self.resources.values():
            for task in resource.timeline.tasks:
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                if np.abs(task.end.lb) > max_time:
                    max_time = np.abs(task.end.lb)
        return max_time
    

    def sum_max_slot_flexibility(self):
        total_max_slot_flexibility = 0
        for resource in self.resources.values():
            for task in resource.timeline.tasks:
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                if self.now is not None:
                    if task.status in ['scheduled']:
                        total_max_slot_flexibility += task.get_max_slot_flexibility()
                else:
                    total_max_slot_flexibility += task.get_max_slot_flexibility()
        return total_max_slot_flexibility
    

    def sum_total_flexibility(self):
        total_flexibility = 0
        # loop through timelines
        for resource in self.resources.values():
            for task in resource.timeline.tasks:
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                # if a now timepoint is set, we only count flexibility for executing and scheduled tasks
                if self.now is not None:
                    if task.status in ['scheduled']:
                        total_flexibility += task.get_task_flexibility()
                else:
                    total_flexibility += task.get_task_flexibility()
        return total_flexibility
    

    def sum_total_slack(self):
        total_slack = 0
        # loop through timelines
        for resource in self.resources.values():
            for task in resource.timeline.tasks:
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                if self.now is not None:
                    if task.status in ['scheduled']:
                        total_slack += task.get_sliding_slack()
                else:
                    total_slack += task.get_sliding_slack()
        return total_slack
    

    def sum_total_slot(self):
        total_slot = 0
        # loop through timelines
        for resource in self.resources.values():
            for task in resource.timeline.tasks:
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                if self.now is not None:
                    if task.status in ['scheduled']:
                        total_slot += task.get_slot_flexibility()
                else:   
                    total_slot += task.get_slot_flexibility()
        return total_slot


    def sum_total_travel(self):
        # if we have a now timepoint, we only count travel after the now timepoint, otherwise we count all travel
        # TODO: check if this the right way to access
        if self.now is not None:
            total_travel_weight = sum(
                np.abs(data.get("weight", 0))
                for _, _, key, data in self.stn.edges(keys=True, data=True)
                if (
                    isinstance(key, tuple)
                    and len(key) > 1
                    and key[1] == "travel"
                    and not np.isinf(data.get("weight", 0))
                    and key[0] in self.resources
                    and self.stn.nodes[key[2]]['timepoint'].lb >= self.now.lb
                )
            )
        else:
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