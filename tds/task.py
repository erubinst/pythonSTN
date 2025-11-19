import numpy as np
from timepoint import Timepoint

class Task:
    def __init__(self, 
                 name, 
                 capabilities, 
                 tds_manager, 
                 order=None, 
                 template=None, 
                 locations=[],
                 assigned_resources=None):
        """
        Create a Task and its start/end timepoints.
        assigned_resources: dict capability -> Resource (may be empty)
        """
        self.name = name
        self.capabilities = list(capabilities)
        self.tds = tds_manager
        self.order = order
        self.template = template
        self.locations = locations # start and end locations in a list
        self.assigned_resources = {} if assigned_resources is None else dict(assigned_resources)

        # create timepoints through the manager so they are registered there
        self.start = Timepoint(f'{name}_start', self.tds)
        self.end = Timepoint(f"{name}_end", self.tds)

        # register this task object with the manager under its name
        self.tds.add_task_to_manager(self)

    def add_time_window_constraints(self, start_time, end_time):
        self.tds.cz.add_constraint(self.start, start_time)
        self.tds.cz.add_constraint(self.end, 0, end_time)

    def add_duration_constraint(self, duration):
        self.start.add_constraint(self.end, duration, duration)

    # dont use
    def constrain_before(self, other_task, min_gap=0, max_gap=np.inf):
        self.end.add_constraint(other_task.start, min_gap=min_gap, max_gap=max_gap)

    def constrain_after(self, other_task, min_gap=0, max_gap=np.inf):
        other_task.end.add_constraint(self.start, min_gap=min_gap, max_gap=max_gap)

    def assign_resource(self, capability, resource):
        if capability not in self.capabilities:
            raise ValueError(f"{capability} not required by task {self.name}")
        self.assigned_resources[capability] = resource.name

    def __repr__(self):
        return f"<Task {self.name} caps={self.capabilities} assigned={list(self.assigned_resources.keys())}>"
