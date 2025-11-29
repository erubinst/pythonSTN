from timeline import Timeline
from config import *

class Resource:
    def __init__(self, name, capabilities, base_location, tds_manager):
        self.name = name
        self.capabilities = set(capabilities)
        self.tds = tds_manager
        self.base_location = base_location
        self.timeline = Timeline(self, tds_manager)

        self.tds.add_resource_to_manager(self)
        self.timeline.create_header_footer(GLOBAL_START, GLOBAL_END)

    def insert_task_to_timeline(self, task, capability, prev_task=None, generate_travel=True):
        # Ensure task is appended via timeline (this will add STN ordering)
        if capability not in self.capabilities:
            raise ValueError(f"Resource '{self.name}' does not have capability '{capability}'")
        self.timeline.insert_task(task, prev_task, generate_travel=generate_travel)
        task.assign_resource(capability, self)

    def has_capability(self, capability):
        return capability in self.capabilities

    def __repr__(self):
        return f"<Resource {self.name} caps={list(self.capabilities)}>"


