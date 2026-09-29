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
        # Set True while the active objective is 'full_flex', so the flexibility
        self.include_unscheduled_in_flexibility = False
        # Set False for the 'slots' (RFlex-only) objective 
        self.include_slack_in_flexibility = True
        # Set True for the 'full_flex_swap' objective: 
        self.include_swapsols = False
        # Set True for the 'full_flex_concave' objective: apply a concave
        # (sqrt) transform to each individual slot's slack before summing
        self.include_slot_concavity = False
        # Set True for 'full_flex_matching': repair-time retraction scoring
        # uses a resource-level max-matching count instead of summed RFlex,
        # so shared backup capacity can't be double-counted across tasks.
        self.include_matching_redundancy = False
        self.cp_solve_log = []


    def normalize_risk_weights(self):
        weights = [r.risk_weight for r in self.resources.values()]
        mean_weight = sum(weights) / len(weights) if weights else 1.0
        for r in self.resources.values():
            r.risk_normalized = r.risk_weight / mean_weight if mean_weight > 0 else 1.0


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


    def refresh_saved_flexibility_after_now_update(self):
        """
        Advancing 'now' can invalidate saved flexibility values on any resource
        """
        if not any(task.flexibility for task in self.tasks.values()):
            return
        for resource in self.resources.values():
            resource.timeline.update_capable_tasks_flexibility()


    def find_task_by_timepoint(self, stn_tp):
        for task in self.tasks.values():
            if stn_tp == task.start.name or stn_tp == task.end.name:
                return task
        return None


    def _iter_countable_tasks(self):
        """
        Yield every task counting toward the schedule-wide aggregates below:
        skips header/footer/downtime tasks, and only counts 'scheduled' tasks,
        or 'scheduled' plus 'unscheduled' when include_unscheduled_in_flexibility
        is set (full_flex mode only). Applies during initial generation too.
        """
        allowed_statuses = ('scheduled', 'unscheduled') if self.include_unscheduled_in_flexibility else ('scheduled',)
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            if task.status not in allowed_statuses:
                continue
            yield task


    def sum_saved_flexibility(self):
        return sum(sum(task.flexibility.values()) for task in self._iter_countable_tasks())


    def verify_saved_flexibility(self, tol=1e-6):
        mismatches = []
        for task in self._iter_countable_tasks():
            saved_total = sum(task.flexibility.values())
            live_total = task.get_task_flexibility()
            if abs(saved_total - live_total) <= tol:
                continue

            assigned_resource = task.get_assigned_resource()
            for capable_resource in task.capable_resources():
                saved_value = task.flexibility.get(capable_resource.name, 0)
                live_value = task.get_slot_flexibility_for_resource(capable_resource)
                if capable_resource is assigned_resource:
                    live_value += task.get_sliding_slack()
                if abs(saved_value - live_value) > tol:
                    mismatches.append((task.name, capable_resource.name, saved_value, live_value))
        return mismatches


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
    

    def wipe_all_scheduled_tasks(self, save_flexibility=False):
        """
        Remove every currently 'scheduled' (not yet started) task tds-wide from its
        resource's timeline, leaving executing/completed tasks and downtime blocks
        untouched. 
        """
        wiped_tasks = []
        for resource in self.resources.values():
            for task in list(resource.timeline.tasks):
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                if task.status == 'scheduled':
                    resource.timeline.remove_task(task, save_flexibility=save_flexibility)
                    wiped_tasks.append(task)
        return wiped_tasks


    def sum_completion_time_diff(self):
        total_diff = 0
        for task in self.tasks.values():
            if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                continue
            if task.status in ['scheduled', 'executing']:
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
        return sum(task.get_max_slot_flexibility() for task in self._iter_countable_tasks())


    def sum_total_flexibility(self):
        return sum(task.get_task_flexibility() for task in self._iter_countable_tasks())


    def sum_total_slack(self):
        return sum(task.get_sliding_slack() for task in self._iter_countable_tasks())
    

    def sum_total_slot(self):
        return sum(task.get_slot_flexibility() for task in self._iter_countable_tasks())


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