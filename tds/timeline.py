class Timeline:
    """
    Represents an ordered sequence of tasks for a single resource.
    Responsible for adding intra-resource temporal constraints.
    """

    def __init__(self, resource, tds_manager):
        self.resource = resource
        self.tds = tds_manager
        self.tasks = []

    def add_task(self, task):
        """
        Add a task to this timeline.
        Automatically add ordering constraints between consecutive tasks.
        """
        if self.tasks:
            prev_task = self.tasks[-1]
            # Enforce that the new task starts after the previous one ends.
            prev_task.end.constrain_before(task.start, min_gap=0)
        self.tasks.append(task)

    def __repr__(self):
        return f"<Timeline of {self.resource.name} with {len(self.tasks)} tasks>"
