from timeline import Timeline

class Resource:
    def __init__(self, name, capabilities, tds_manager):
        self.name = name
        self.capabilities = set(capabilities)
        self.tds = tds_manager
        self.timeline = Timeline(self, tds_manager)

        self.tds.add_resource_to_manager(self)

    def append_task_to_timeline(self, task, capability):
        # Ensure task is appended via timeline (this will add STN ordering)
        if capability not in self.capabilities:
            raise ValueError(f"Resource '{self.name}' does not have capability '{capability}'")
        self.timeline.append_task(task)
        task.assign_resource(capability, self)

    def has_capability(self, capability):
        return capability in self.capabilities

    def __repr__(self):
        return f"<Resource {self.name} caps={list(self.capabilities)}>"


