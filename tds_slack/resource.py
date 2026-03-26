from .timeline import Timeline

class Resource:
    def __init__(self, name, capabilities, base_location, tds_manager):
        self.name = name.lower()
        self.capabilities = {c.lower() for c in capabilities}
        self.tds = tds_manager
        self.base_location = base_location
        self.timeline = Timeline(self, tds_manager)

        self.tds.add_resource_to_manager(self)
        self.timeline.create_header_footer()

    def insert_task_to_timeline(self, task, capability, prev_task=None, generate_travel=True, return_affected_timepoint=False):
        # Ensure task is appended via timeline (this will add STN ordering when prev task given)
        if capability not in self.capabilities:
            raise ValueError(f"Resource '{self.name}' does not have capability '{capability}'")
        return self.timeline.insert_task(task, prev_task, generate_travel=generate_travel, return_affected_timepoint=return_affected_timepoint)

    def remove_task_from_timeline(self, task):
        self.timeline.remove_task(task)

    def has_capability(self, capability):
        return capability in self.capabilities

    def __repr__(self):
        return f"<Resource {self.name} caps={list(self.capabilities)}>"


