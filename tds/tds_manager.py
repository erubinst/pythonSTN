from stn.stn import STN
from timepoint import Timepoint
import pandas as pd
import numpy as np

class TDSManager:
    def __init__(self, travel_matrix=None):
        self.stn = STN()
        self.resources = {}     # name -> Resource
        self.tasks = {}         # name or order -> Task
        self.cz = Timepoint('zero', self, add_to_stn=False)
        self.travel_matrix = travel_matrix

    def sum_total_travel(self):
        total_travel_weight = sum(
            np.abs(data.get("weight", 0))
            for _, _, key, data in self.stn.edges(keys=True, data=True)
            if (
                isinstance(key, tuple)
                and len(key) > 1
                and key[1] == "travel"
                and not np.isinf(data.get("weight", 0))
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