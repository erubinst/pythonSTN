from .timeline import Timeline
import numpy as np

class Resource:
    def __init__(self, name, capabilities, base_location, tds_manager, risk_weight=1.0):
        self.name = name.lower()
        self.capabilities = {c.lower() for c in capabilities}
        self.tds = tds_manager
        self.base_location = base_location
        self.timeline = Timeline(self, tds_manager)
        self.risk_weight = risk_weight
        self.risk_normalized = 1.0  # set by TDSManager.normalize_risk_weights() once all resources are loaded

        self.tds.add_resource_to_manager(self)
        self.timeline.create_header_footer()

    def insert_task_to_timeline(self, task, capability, prev_task=None, generate_travel=True, return_affected_timepoint=False, save_flexibility=False):
        # Ensure task is appended via timeline (this will add STN ordering when prev task given)
        if capability not in self.capabilities:
            raise ValueError(f"Resource '{self.name}' does not have capability '{capability}'")
        if self.tds.now is not None:
            # we have a now tp so we need to add a constraint on the task start tp to be after now tp
            self.tds.now.add_constraint(task.start, ('all', 'start_after_now'), min_gap=0, max_gap=np.inf)
        result = self.timeline.insert_task(task, prev_task, generate_travel=generate_travel, return_affected_timepoint=return_affected_timepoint)
        success = result[0] if return_affected_timepoint else result
        if success:
            # only mark as scheduled once actually placed; insert_task rolls back
            # the timeline/STN changes on failure, so the status must match
            task.status = "scheduled"
            if save_flexibility:
                # upon insertion, save entire flexibility dict as the task is newly added
                task.update_saved_flexibility()
                # all other tasks capable of this resource, update only the flexibility entry for this resource
                self.timeline.update_capable_tasks_flexibility(exclude_task=task)
        return result

    def remove_task_from_timeline(self, task):
        self.timeline.remove_task(task)

    def has_capability(self, capability):
        return capability in self.capabilities

    def __repr__(self):
        return f"<Resource {self.name} caps={list(self.capabilities)}>"


