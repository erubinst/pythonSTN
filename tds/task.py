import numpy as np
from .timepoint import Timepoint

class Task:
    def __init__(self, 
                 name, 
                 capabilities, 
                 tds_manager, 
                 order=None, 
                 template=None, 
                 locations=[]):
        """
        Create a Task and its start/end timepoints.
        """
        self.name = name.lower()
        self.capabilities = {c.lower() for c in capabilities}
        self.tds = tds_manager
        self.order = order
        self.template = template
        self.locations = locations # start and end locations in a list

        # create timepoints through the manager so they are registered there
        self.start = Timepoint(f'{name}_start', self.tds)
        self.end = Timepoint(f"{name}_end", self.tds)

        # register this task object with the manager under its name
        self.tds.add_task_to_manager(self)

    def update_task_name(self, new_name):
        old_name = self.name
        self.tds.tasks[new_name.lower()] = self.tds.tasks.pop(old_name)
        self.name = new_name.lower()
        self.start.update_name(f'{new_name}_start')
        self.end.update_name(f'{new_name}_end')

    def delete_task(self):
        for tp in [self.start, self.end]:
            tp.delete_timepoint()
        self.tds.tasks.pop(self.name, None)
        # add routine to remove from resource timelines if assigned
        for resource in self.tds.resources.values():
            if self in resource.timeline.tasks:
                resource.timeline.remove_task(self)
        

    def add_time_window_constraints(self, start_time, end_time):
        self.tds.cz.add_constraint(self.start, ("all", "release_time"), start_time)
        self.tds.cz.add_constraint(self.end, ("all","due_date"), 0, end_time)

    def add_duration_constraint(self, duration):
        self.start.add_constraint(self.end, ("all","duration"), duration, duration)

    def constrain_before(self, other_task, constraint_type, min_gap=0, max_gap=np.inf, print_inconsistencies=True):
        return self.end.add_constraint(other_task.start, constraint_type, min_gap=min_gap, max_gap=max_gap, print_inconsistencies=print_inconsistencies)

    def constrain_after(self, other_task, constraint_type, min_gap=0, max_gap=np.inf, print_inconsistencies=True):
        # sequence type constraint
        return other_task.end.add_constraint(self.start, constraint_type, min_gap=min_gap, max_gap=max_gap, print_inconsistencies=print_inconsistencies)
    
    # call on prior task, add constraint with next task start
    def restore_constraint_btwn(self, next_task, constraint_type, min_gap=0, max_gap=np.inf):
        self.end.add_constraint(next_task.start, constraint_type, min_gap=min_gap, max_gap=max_gap)

    # call on the prior task, delete edges both directions of that constraint type
    def remove_constraint_btwn(self, other_task, constraint_type):
        self.end.delete_constraint(other_task.start, constraint_type)

    def assign_resource(self, capability, resource):
        if capability not in self.capabilities:
            raise ValueError(f"{capability} not required by task {self.name}")
        

    def __eq__(self, other):
        if not isinstance(other, Task):
            return False
        return self.name == other.name  # or compare by whatever makes two tasks "the same"
        

    def __repr__(self):
        return f"<Task {self.name} caps={self.capabilities}>"

