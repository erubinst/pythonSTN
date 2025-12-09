import numpy as np
import networkx as nx

class Timepoint:
    def __init__(self, name, tds_manager, add_to_stn=True):
        """
        Creates a timepoint node in the STN via the STN API and keeps a wrapper.
        """
        self.name = name
        self.tds = tds_manager
        # create node in STN
        if add_to_stn:
            self.tds.stn.add_timepoint(name)


    def add_constraint(self, other, constraint_type, min_gap=0, max_gap=np.inf, print_inconsistencies=True):
        """
        Add: min_gap <= other - self <= max_gap
        Uses STN.add_constraint(self.name, other.name, lb, ub)
        """
        return self.tds.stn.add_constraint(self.name, other.name, lb=min_gap, ub=max_gap, constraint_type=constraint_type, print_inconsistencies=print_inconsistencies)
    
    def update_name(self, new_name):
        mapping = {self.name: new_name}
        nx.relabel_nodes(self.tds.stn, mapping, copy=False)
        self.name = new_name
    
    def delete_constraint(self, other, constraint_type):
        """
        Delete constraint between self and other timepoint.
        Uses STN.delete_constraint(self.name, other.name)
        """
        self.tds.stn.delete_constraint(self.name, other.name, constraint_type)

    def delete_timepoint(self):
        self.tds.stn.delete_timepoint(self.name)

    def ub_edge_weight(self, other, constraint_type):
        if self.tds.stn.has_edge(self.name, other.name, key=constraint_type):
            edge_data = self.tds.stn.get_edge_data(self.name, other.name, key=constraint_type)
            return edge_data['weight']
        else:
            return np.inf
        
    def lb_edge_weight(self, other, constraint_type):
        if self.tds.stn.has_edge(other.name, self.name, key=constraint_type):
            edge_data = self.tds.stn.get_edge_data(other.name, self.name, key=constraint_type)
            return edge_data['weight']
        else:
            return 0

    
    @property
    def lb(self):
        """Return the current lower bound of this timepoint."""
        return self.tds.stn.nodes[self.name]['data'].get('lb', None)

    @property
    def ub(self):
        """Return the current upper bound of this timepoint."""
        return self.tds.stn.nodes[self.name]['data'].get('ub', None)
    
    @property
    def pu(self):
        """Return the current upper bound constraining edge."""
        return self.tds.stn.nodes[self.name]['data'].get('pu', None)
    
    @property
    def pl(self):
        """Return the current lower bound constraining edge."""
        return self.tds.stn.nodes[self.name]['data'].get('pl', None)

    def __repr__(self):
        node = self.tds.stn.nodes[self.name]['data']
        return f"<Timepoint {self.name} LB={node['lb']}, UB={node['ub']}>"
