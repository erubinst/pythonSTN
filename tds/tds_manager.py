from stn.stn import STN
from .timepoint import Timepoint
import pandas as pd
import numpy as np

class TDSManager:
    def __init__(self, travel_matrix=None):
        self.stn = STN()
        self.resources = {}     # name -> Resource
        self.tasks = {}         # name or order -> Task
        self.cz = Timepoint('zero', self, add_to_stn=False)
        self.travel_matrix = travel_matrix
        self.type_ranking = [
            "medical_appointment",
            "medication_pickup",
            "food_shopping",
            "shopping",
            "cleaning",
            "work",
            "social"
        ]

    def sort_by_type_ranking(self):
        ranked_tasks = []
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
            task_type = task.task_type
            if task_type is not None:
                rank = self.type_ranking.index(task_type)
            else:
                rank = len(self.type_ranking)  # lowest priority if type not found
            ranked_tasks.append((rank, task.name, task))
        ranked_tasks.sort()
        sorted_tasks = [task for _, _, task in ranked_tasks]
        return sorted_tasks
    

    def get_caregiver_total_time(self):
        caregiver_time = {}
        for resource in self.resources.values():
            if resource.type == 'cg':
                total_time = 0
                # sum task durations (skip headers/footers/downtime)
                for task in resource.timeline.tasks:
                    if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                        continue
                    total_time += task.get_duration()

                # sum travel edges for this resource once (use absolute weights to match sum_total_travel)
                travel_time = sum(
                    np.abs(data.get("weight", 0))
                    for _, _, key, data in self.stn.edges(keys=True, data=True)
                    if (
                        isinstance(key, tuple)
                        and len(key) > 1
                        and key[1] == "travel"
                        and not np.isinf(data.get("weight", 0))
                        and key[0] == resource.name
                    )
                )
                total_time += travel_time
                caregiver_time[resource.name] = int(total_time)
        return caregiver_time


    def get_driver_capabilities(self):
        driver_capabilities = set()

        for resource in self.resources.values():
            if 'traveler' in resource.capabilities:
                driver_capabilities.update(resource.capabilities)

        return driver_capabilities

    
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
    

    def sort_tasks_by_caregiver_routine(self, task_lst=None):
        # put caregiver routine tasks first, then sort by flexibility within each group
        if task_lst is None:
            task_lst = self.tasks.values()
        routine_tasks = []
        non_routine_tasks = []
        for task in task_lst:
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
            if task.caregiver_routine:
                routine_tasks.append(task)
            else:
                non_routine_tasks.append(task)
        sorted_routine_tasks = self.sort_tasks_by_flexibility(routine_tasks)
        sorted_non_routine_tasks = self.sort_tasks_by_flexibility(non_routine_tasks)
        return sorted_routine_tasks + sorted_non_routine_tasks
    

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
                and self.resources[key[0]].type == 'cg'  # only count travel for caregivers
            )
        )
        return total_travel_weight
    

    def min_makespan(self):
        max_completion_time = 0
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
            task_completion_time = np.abs(task.end.lb)
            if task_completion_time > max_completion_time:
                max_completion_time = task_completion_time
        return max_completion_time
    

    def sum_total_ride_time(self):
        total_ride_time = 0
        for resource in self.resources.values():
            if 'traveler' not in resource.capabilities:
                pickup_task = None
                for task in resource.timeline.tasks:
                    if task.name.startswith('pickup_from_'):
                        pickup_task = task
                    elif task.name.startswith('dropoff_at_') and pickup_task is not None:
                        ride_time = np.abs(task.end.lb) - np.abs(pickup_task.start.lb)
                        # print(f'Ride time for {pickup_task.name} is {ride_time}')
                        total_ride_time += ride_time
                        pickup_task = None
        return total_ride_time
    

    # sum total completion time
    def sum_total_completion_time(self):
        max_completion_time = 0
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
            task_completion_time = np.abs(task.end.lb)
            if task_completion_time > max_completion_time:
                max_completion_time = task_completion_time
        return max_completion_time
                    

    def add_task_to_manager(self, task):
        """Register a task with the manager."""
        if task.name in self.tasks:
            raise ValueError(f"Task '{task.name}' already exists")
        self.tasks[task.name] = task
    

    def add_resource_to_manager(self, resource):
        """Register a resource."""
        self.resources[resource.name] = resource


    def same_task_groups(self):
        resource_same_tasks = {}
        for res_name, resource in self.resources.items():
            task_groups = resource.timeline.find_same_task_groups()
            resource_same_tasks[res_name] = task_groups
        return resource_same_tasks
    

    def calculate_total_travel_task_time(self):
        total_travel = self.sum_total_travel()
        # find total task duration time
        total_task_time = 0
        for resource in self.resources.values():
            # only count for caregivers
            if resource.type != 'cg':
                continue
            for task in resource.timeline.tasks:
                # skip downtime tasks and header/footer tasks
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue   
                total_task_time += task.get_duration()
        total_time = total_task_time + total_travel
        return total_time


    def export_to_df(self):
        df = pd.DataFrame()
        for resource in self.resources.values():
            timeline_df = resource.timeline.export_to_df()
            df = pd.concat([df, timeline_df])
        return df
    

    def export_transport_request(self):
        output = {
            "resources": []
        }

        for resource in self.resources.values(): 
            if 'traveler' not in resource.capabilities:
                transport_df = resource.generate_transport_requests()
                jobs = []
                for _, row in transport_df.iterrows():
                    job_entry = {
                        "name": row["job_name"],  # job/sequence name
                        "pickup": {
                            "location": row["pickup_location"]
                        },
                        "dropoff": {
                            "location": row["dropoff_location"]
                        },
                        "prior_task": row["prior_task"],
                        "next_task": row["next_task"],
                        "travel_time": row["travel_time"]
                    }
                    jobs.append(job_entry)

                # Add this resource + its jobs to output JSON
                resource_entry = {
                    "name": resource.name,  # ← your resource attribute
                    "tasks": jobs
                }

                output["resources"].append(resource_entry)

        return output