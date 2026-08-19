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
        self.refresh_saved_flexibility_after_now_update()


    def refresh_saved_flexibility_after_now_update(self):
        """
        Advancing 'now' can invalidate saved flexibility values on any resource,
        not just ones whose timeline was structurally edited: every slot probe
        requires start_after_now, so an alternate slot that was counted as
        available can silently expire as time passes, with no insertion or
        removal ever touching that resource. Only worth the cost if
        save_flexibility is actually in use (i.e. some task's dict has been
        populated) — otherwise this is a no-op.
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
        Yield (resource, task) for every task that counts toward the schedule-wide
        aggregates below: skips header/footer/downtime bookkeeping tasks, and once
        'now' exists, only counts tasks still 'scheduled' (executing/completed
        tasks are no longer live scheduling decisions).
        """
        for resource in self.resources.values():
            for task in resource.timeline.tasks:
                if task.name.endswith('_header') or task.name.endswith('_footer') or 'downtime' in task.name:
                    continue
                if self.now is not None and task.status != 'scheduled':
                    continue
                yield resource, task


    def sum_saved_flexibility(self):
        return sum(sum(task.flexibility.values()) for _, task in self._iter_countable_tasks())


    def verify_saved_flexibility(self, tol=1e-6):
        """
        Compare each task's saved flexibility dict against a full live
        recomputation (task.get_task_flexibility()). Mirrors the same filtering
        as sum_saved_flexibility/sum_total_flexibility so the two are directly
        comparable task-by-task.

        Cheaply checks the aggregate total first; only when that's off does it
        decompose entry by entry (task.capable_resources()) to find exactly
        which resource's cached value actually drifted — the resource a task's
        flexibility total is off on is not necessarily its current assignment.

        Returns a list of (task_name, resource_name, saved_value, live_value)
        for every drifted (task, resource) entry. An empty list means the saved
        dict is fully consistent with a from-scratch recalculation.
        """
        mismatches = []
        for _, task in self._iter_countable_tasks():
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
        return sum(task.get_max_slot_flexibility() for _, task in self._iter_countable_tasks())


    def sum_total_flexibility(self):
        return sum(task.get_task_flexibility() for _, task in self._iter_countable_tasks())


    def sum_total_slack(self):
        return sum(task.get_sliding_slack() for _, task in self._iter_countable_tasks())
    

    def sum_total_slot(self):
        return sum(task.get_slot_flexibility() for _, task in self._iter_countable_tasks())


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