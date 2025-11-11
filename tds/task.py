from timepoint import Timepoint

class Task:
    """
    Represents a task in the TDS that may require multiple capabilities,
    each fulfilled by a separate resource. The task is represented as
    a start and end timepoint in the shared STN.
    """

    def __init__(self, name, capabilities, tds_manager, assignments=None, duration=None):
        """
        Initialize a Task object.

        Parameters
        ----------
        name : str
            Task identifier.
        capabilities : list[str]
            List of required capabilities.
        tds_manager : TDSManager
            Reference to shared STN and resource manager.
        assignments : dict[str, Resource], optional
            Predefined mapping of capability → assigned Resource.
        duration : float, optional
            Fixed duration (if applicable).
        """
        self.name = name
        self.capabilities = capabilities
        self.tds = tds_manager
        self.duration = duration

        # Assignments is a dict: {capability: Resource}
        self.assigned_resources = assignments or {}

        # Create timepoints in STN
        self.start = Timepoint(f"{name}_start", tds_manager.stn)
        self.end = Timepoint(f"{name}_end", tds_manager.stn)

        # Apply fixed duration if provided
        if duration is not None:
            self.start.constrain_before(self.end, min_gap=duration, max_gap=duration)

        # Add to assigned resources' timelines
        for cap, res in self.assigned_resources.items():
            res.add_task(self)

    # -------------------------------
    # Resource assignment management
    # -------------------------------

    def assign_resource(self, capability, resource):
        """
        Assign a resource to one of this task's required capabilities.
        """
        if capability not in self.capabilities:
            raise ValueError(f"{capability} not in required capabilities {self.capabilities}")
        self.assigned_resources[capability] = resource
        resource.add_task(self)

    def all_resources_assigned(self):
        """Check if every required capability has an assigned resource."""
        return all(cap in self.assigned_resources for cap in self.capabilities)

    # -------------------------------
    # Temporal constraints
    # -------------------------------

    def add_duration_constraint(self, min_dur, max_dur=None):
        self.start.constrain_before(self.end, min_gap=min_dur, max_gap=max_dur)
        self.duration = (min_dur if max_dur is None else (min_dur, max_dur))

    def add_custom_constraint(self, other_task, relation, gap):
        """Add a temporal constraint relative to another task."""
        if relation == "start_after":
            other_task.end.constrain_before(self.start, min_gap=gap)
        elif relation == "end_after":
            other_task.end.constrain_before(self.end, min_gap=gap)
        elif relation == "start_before":
            self.start.constrain_before(other_task.start, min_gap=gap)
        elif relation == "end_before":
            self.end.constrain_before(other_task.end, min_gap=gap)
        else:
            raise ValueError(f"Invalid relation: {relation}")

    # -------------------------------
    # Debug / representation
    # -------------------------------

    def __repr__(self):
        caps = ", ".join(self.capabilities)
        assigned = {cap: res.name for cap, res in self.assigned_resources.items()}
        return (f"<Task {self.name}: [{caps}], "
                f"assigned={assigned}, duration={self.duration}>")
