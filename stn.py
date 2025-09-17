import numpy as np
import networkx as nx
from queue import deque


class STN(nx.DiGraph):
    def __init__(self):
        super().__init__()
        self.cz = self.add_cz_tp()

    def __str__(self):
        stn_str = "STN:\n"
        for tp in self.nodes:
            stn_str += f"Timepoint: {tp}\n"
            stn_str += f"  LB: {self.nodes[tp]['data']['lb']}\n"
            stn_str += f"  UB: {self.nodes[tp]['data']['ub']}\n"
            stn_str += f"  LB Constricting Edge: {self.nodes[tp]['data'].get('pl', None)}\n"
            stn_str += f"  UB Constricting Edge: {self.nodes[tp]['data'].get('pu', None)}\n"
        return stn_str

    def add_cz_tp(self):
        "Creating the zero timepoint"
        cz = "zero"
        self.add_node(cz, data={'ub': 0, 'lb': 0})
        return cz

    def add_timepoint(self, tp):
        self.add_node(tp, data = {'ub': np.inf, 'lb': 0})

    def remove_timepoint(self, tp):
        self.remove_node(tp)

    def add_constraint(self, tp1, tp2, lb=0, ub=np.inf):
        print(f"Adding constraint: {tp1} -> {tp2}, lb: {lb}, ub: {ub}")
        self.add_edge(tp1, tp2, weight=ub, data={'new_p': True})
        self.add_edge(tp2, tp1, weight=-lb, data={'new_p': True})
        # propagate constraints
        self.propagate(tp1, tp2)

    def propagate(self, tp1, tp2):
        """
        Incremental approach to propagate constraints
        Need to save update information to be able to undo if constraint does not work
        """

        queue = deque([tp1, tp2])
        for tp in [tp1, tp2]:
            self.nodes[tp]['data']['ub_p'] = True
            self.nodes[tp]['data']['lb_p'] = True
        while queue:
            tp = queue.popleft()
            tp_node = self.nodes[tp]
            if tp_node['data']['ub_p']:
                for tp, connected_tp, data in self.out_edges(tp, data=True):
                    connected_tp_node = self.nodes[connected_tp]
                    new_ub = tp_node['data']['ub'] + self[tp][connected_tp]['weight']
                    if new_ub < connected_tp_node['data']['ub'] or (connected_tp != self.cz and connected_tp_node['data'].get('pu') is None):
                        connected_tp_node['data']['ub'] = new_ub
                        connected_tp_node['data']['ub_p'] = True
                        connected_tp_node['data']['pu'] = tp
                        # if new upper bound is less than the lower bound, raise inconsistency
                        if connected_tp_node['data']['ub'] < -connected_tp_node['data']['lb']:
                            raise Exception("Inconsistent STN")
                        # If revisiting edge, raise inconsistency
                        if data.get('new_p', False) and data.get('ub_p', False):
                            raise Exception("Inconsistent STN")
                        data['ub_p'] = True
                        if connected_tp not in queue:
                            queue.append(connected_tp)                        

            if tp_node['data']['lb_p']:
                for connected_tp, tp, data in self.in_edges(tp, data=True):
                    connected_tp_node = self.nodes[connected_tp]
                    new_lb = tp_node['data']['lb'] + self[connected_tp][tp]['weight']
                    if new_lb < connected_tp_node['data']['lb'] or (connected_tp != self.cz and connected_tp_node['data'].get('pl') is None):
                        connected_tp_node['data']['lb'] = new_lb
                        connected_tp_node['data']['lb_p'] = True
                        connected_tp_node['data']['pl'] = tp
                        # if upper bound is less than the new lower bound, raise inconsistency
                        if connected_tp_node['data']['ub'] < -connected_tp_node['data']['lb']:
                            raise Exception("Inconsistent STN")
                        # If revisiting edge, raise inconsistency
                        if data.get('new_p', False) and data.get('lb_p', False):
                            raise Exception("Inconsistent STN")
                        data['lb_p'] = True
                        if connected_tp not in queue:
                            queue.append(connected_tp)                        
            tp_node['data']['ub_p'] = False
            tp_node['data']['lb_p'] = False



