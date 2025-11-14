import numpy as np

class Timepoint:
    def __init__(self, name, tds_manager, earliest=None, latest=None, add_to_stn=True):
        """
        Creates a timepoint node in the STN via the STN API and keeps a wrapper.
        """
        self.name = name
        self.tds = tds_manager
        # create node in STN
        if add_to_stn:
            self.tds.stn.add_timepoint(name)
        # optional bounds relative to zero

    def add_constraint(self, other, min_gap=0, max_gap=np.inf):
        """
        Add: min_gap <= other - self <= max_gap
        Uses STN.add_constraint(self.name, other.name, lb, ub)
        """
        return self.tds.stn.add_constraint(self.name, other.name, lb=min_gap, ub=max_gap)
    
    @property
    def lb(self):
        """Return the current lower bound of this timepoint."""
        return self.tds.stn.nodes[self.name]['data'].get('lb', None)

    @property
    def ub(self):
        """Return the current upper bound of this timepoint."""
        return self.tds.stn.nodes[self.name]['data'].get('ub', None)

    def __repr__(self):
        node = self.tds.stn.nodes[self.name]['data']
        return f"<Timepoint {self.name} LB={node['lb']}, UB={node['ub']}>"
