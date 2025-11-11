from timeline import Timeline

class Resource:
    """
    Represents a resource (robot, caretaker, etc) that can perform tasks.
    Each resource has a set of capabilities and its own timeline of tasks.
    """

    def __init__(self, name, capabilities, tds_manager):
        """
        Initialize a resource within the TDS domain.

        Parameters
        ----------
        name : str
            Name of the resource.
        capabilities : list[str] or set[str]
            Capabilities or skills this resource provides.
        tds_manager : TDSManager
            Reference to the manager (which holds the shared STN).
        """
        self.name = name
        self.capabilities = set(capabilities)
        self.tds = tds_manager
        self.timeline = Timeline(self, tds_manager)
        self.tasks = []

    def add_task(self, task):
        """Assign a task to this resource and append it to its timeline."""
        self.tasks.append(task)
        self.timeline.add_task(task)

    def has_capability(self, capability):
        """Check if resource can perform a given capability."""
        return capability in self.capabilities

    def __repr__(self):
        return f"<Resource {self.name}, capabilities={list(self.capabilities)}>"
