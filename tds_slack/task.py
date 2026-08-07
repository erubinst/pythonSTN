import numpy as np
from .timepoint import Timepoint
from tds_slack.slack_search import determine_slot_slack, determine_max_slot_slack

class Task:
    def __init__(self, 
                 name, 
                 capability, 
                 tds_manager,
                 locations=[],
                 task_type = "NA"):
        """
        Create a Task and its start/end timepoints.
        """
        
        self.name = name.lower()
        self.capability = capability.lower()
        self.tds = tds_manager
        self.locations = locations # start and end locations in a list

        # create timepoints through the manager so they are registered there
        self.start = Timepoint(f'{name}_start', self.tds)
        self.end = Timepoint(f"{name}_end", self.tds)

        # register this task object with the manager under its name
        self.tds.add_task_to_manager(self)

        self.task_type = task_type
        self.status = "unscheduled"  # can be "unscheduled", "scheduled", "executing", "completed",
       

    def begin_execution(self):
        self.status = "executing"
        # add a constraint from cz to start tp to ensure that the task cannot start before the now timepoint
        current_time = np.abs(self.tds.now.lb)
        # remove the constraint on its start tp and add a constraint on its end tp to be after the now timepoint
        self.start.delete_constraint(self.tds.now, ('all', 'start_after_now'))
        self.tds.cz.add_constraint(self.start, ('all', 'start_after_current_time'), min_gap=current_time, max_gap=np.inf)
        self.tds.now.add_constraint(self.end, ('all', 'end_after_now'), min_gap=0, max_gap=np.inf)
        

    def complete_execution(self):
        self.status = "completed"
        # remove the constraint on its end tpz
        self.end.delete_constraint(self.tds.now, ('all', 'end_after_now'))
        # set the last executed task for the resource timeline to this task
        # TODO: Is there a better way than looping through all resources to find the one that has this task in its timeline? Maybe store a reference to the resource in the task object when it is assigned to a resource.
        for resource in self.tds.resources.values():
            if self in resource.timeline.tasks:
                resource.timeline.last_executed_task = self
                break

    def get_release_time(self):
        # get edge weight with cz to start constraint type 'release_time'
        return np.abs(self.start.ub_edge_weight(self.tds.cz, ('all', 'release_time')))

    def get_due_date(self):
        return np.abs(self.end.lb_edge_weight(self.tds.cz, ('all', 'due_date')))
    
    def get_duration(self):
        return np.abs(self.start.ub_edge_weight(self.end, ('all', 'duration')))
    
    def get_task_starting_flexibility(self):
        return self.get_due_date() - self.get_release_time() - self.get_duration()
    
    def capable_resources(self):
        return [r for r in self.tds.resources.values() if r.has_capability(self.capability)]
    
    def get_sliding_slack(self):
        # sliding slack - difference between duration and task ub - lb
        sliding_slack = (self.end.ub - np.abs(self.start.lb)) - self.get_duration() + 1
        # print(f"Task {self.name} sliding slack calculation: end.ub={self.end.ub}, start.lb={self.start.lb}, duration={self.get_duration()}, sliding_slack={sliding_slack}")
        return sliding_slack
    
    
    def get_max_slot_flexibility(self):
        # find alternate slot with biggest slack
        current_resource = None
        for resource in self.tds.resources.values():
            if self in resource.timeline.tasks:
                current_resource = resource
                break
        max_slot_slack = determine_max_slot_slack(self.tds, self, current_resource)
        return max_slot_slack
    

    def get_slot_flexibility(self):
        # slot slack - sum of task1_slack on all alternate slots for this task 
        current_resource = None
        for resource in self.tds.resources.values():
            if self in resource.timeline.tasks:
                current_resource = resource
                break
        slot_slack = determine_slot_slack(self.tds, self, current_resource)
        # print(f"Task {self.name} slot flexibility: {slot_slack}")
        return slot_slack
    
    def get_task_flexibility(self):
        # sliding slack - difference between duration and task ub - lb
        sliding_slack = self.get_sliding_slack()
        # slot slack - sum of task1_slack on all alternate slots for this task 
        slot_slack = self.get_slot_flexibility()
        total_flexibility = sliding_slack + slot_slack
        return total_flexibility

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
    
    def get_completion_time_diff(self):
        # get the difference between the current completion time and the due date
        completion_time = np.abs(self.end.lb)
        due_date = self.get_due_date()
        return due_date - completion_time
        
    def add_time_window_constraints(self, start_time, end_time):
        self.tds.cz.add_constraint(self.start, ("all", "release_time"), start_time)
        self.tds.cz.add_constraint(self.end, ("all","due_date"), 0, end_time)

    def add_duration_constraint(self, duration):
        self.start.add_constraint(self.end, ("all","duration"), duration, duration)

    def constrain_before(self, other_task, constraint_type, min_gap=0, max_gap=np.inf, print_inconsistencies=True, return_affected_timepoint=False):
        return self.end.add_constraint(other_task.start, constraint_type, min_gap=min_gap, max_gap=max_gap, print_inconsistencies=print_inconsistencies, return_affected_timepoint=return_affected_timepoint)

    def constrain_after(self, other_task, constraint_type, min_gap=0, max_gap=np.inf, print_inconsistencies=True, return_affected_timepoint=False):
        # sequence type constraint
        # if other_task is a tp not a task, just call with the tp
        if isinstance(other_task, Timepoint):
            return self.start.add_constraint(other_task, constraint_type, min_gap=min_gap, max_gap=max_gap, print_inconsistencies=print_inconsistencies, return_affected_timepoint=return_affected_timepoint)
        else:
            return other_task.end.add_constraint(self.start, constraint_type, min_gap=min_gap, max_gap=max_gap, print_inconsistencies=print_inconsistencies, return_affected_timepoint=return_affected_timepoint)

    # call on prior task, add constraint with next task start
    def restore_constraint_btwn(self, next_task, constraint_type, min_gap=0, max_gap=np.inf):
        self.end.add_constraint(next_task.start, constraint_type, min_gap=min_gap, max_gap=max_gap)

    # call on the prior task, delete edges both directions of that constraint type
    def remove_constraint_btwn(self, other_task, constraint_type):
        return self.end.delete_constraint(other_task.start, constraint_type)        

    def __eq__(self, other):
        if not isinstance(other, Task):
            return False
        return self.name == other.name  # or compare by whatever makes two tasks "the same"
        

    def __repr__(self):
        return f"<Task {self.name} caps={self.capability}>"