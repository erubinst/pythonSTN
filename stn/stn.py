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

    def add_constraint(self, tp1, tp2, lb=0, ub=np.inf):
        self.add_edge(tp1, tp2, weight=ub, data={'new_p': True})
        self.add_edge(tp2, tp1, weight=-lb, data={'new_p': True})
        # propagate constraints
        return self.propagate(tp1, tp2)

    def delete_constraint(self, tp1, tp2, consistent):
        if consistent:
            # TODO: regular delete, call propagate
            pass
        else:
            # delete the edges
            self.remove_edge(tp1, tp2)
            self.remove_edge(tp2, tp1)

    def _reset_timepoint_flags(self, tp):
        self.nodes[tp]['data']['lb_p'] = False
        self.nodes[tp]['data']['ub_p'] = False

    def _undo_ub(self, tp, old_ub, old_pu):
        self.nodes[tp]['data']['ub'] = old_ub
        self.nodes[tp]['data']['pu'] = old_pu
        self.nodes[tp]['data']['ub_p'] = False

    def _undo_lb(self, tp, old_lb, old_pl):
        self.nodes[tp]['data']['lb'] = old_lb
        self.nodes[tp]['data']['pl'] = old_pl
        self.nodes[tp]['data']['lb_p'] = False

    def reset_edge_flags(self, edge):
        edge['data']['new_p'] = False
        edge['data']['ub_p'] = False
        edge['data']['lb_p'] = False

    def propagate(self, tp1, tp2, new_constraint=True):
        """
        Incremental approach to propagate constraints
        Need to save update information to be able to undo if constraint does not work
        """
        # TODO: find cycle p for debugging
        queue = deque([tp1, tp2])
        # stack to hold undo information
        undo_info = deque()
        for tp in [tp1, tp2]:
            self.nodes[tp]['data']['ub_p'] = True
            self.nodes[tp]['data']['lb_p'] = True
            if new_constraint:
                undo_info.append(lambda tp=tp: self._reset_timepoint_flags(tp))

        try:
            while queue:
                tp = queue.popleft()
                tp_node = self.nodes[tp]
                if tp_node['data']['ub_p']:
                    # loop through edges going out of tp
                    for tp_from, tp_to, data in self.out_edges(tp, data=True):
                        tp_to_node = self.nodes[tp_to]
                        # calculate projected upper bound
                        new_ub = tp_node['data']['ub'] + self[tp_from][tp_to]['weight']

                        old_ub = tp_to_node['data']['ub']
                        old_pu = tp_to_node['data'].get('pu', None)

                        if new_ub < old_ub or (tp_to != self.cz and old_pu is None):
                            if new_constraint:
                                undo_info.append(lambda tp_to=tp_to, old_ub=old_ub, old_pu=old_pu:
                                                self._undo_ub(tp_to, old_ub, old_pu))
                            
            
                            tp_to_node['data']['ub'] = new_ub
                            tp_to_node['data']['ub_p'] = True
                            tp_to_node['data']['pu'] = tp

                            # if new upper bound is less than the lower bound, raise inconsistency
                            if tp_to_node['data']['ub'] < -tp_to_node['data']['lb']:
                                raise Exception("Inconsistent STN: upper bound less than lower bound")
                            # If revisiting edge, raise inconsistency
                            if data.get('new_p', False) and data.get('ub_p', False):
                                raise Exception("Inconsistent STN: revisiting edge")
                            data['ub_p'] = True
                            if tp_to not in queue:
                                queue.append(tp_to)                        

                if tp_node['data']['lb_p']:
                    for tp_from, tp_to, data in self.in_edges(tp, data=True):
                        tp_from_node = self.nodes[tp_from]
                        new_lb = tp_node['data']['lb'] + self[tp_from][tp_to]['weight']

                        old_lb = tp_from_node['data']['lb']
                        old_pl = tp_from_node['data'].get('pl', None)
                        if new_lb < old_lb or (tp_from != self.cz and old_pl is None):
                            if new_constraint:
                                undo_info.append(lambda tp_from=tp_from, old_lb=old_lb, old_pl=old_pl:
                                                self._undo_lb(tp_from, old_lb, old_pl)) 
                            
                            tp_from_node['data']['lb'] = new_lb
                            tp_from_node['data']['lb_p'] = True
                            tp_from_node['data']['pl'] = tp

                            # if upper bound is less than the new lower bound, raise inconsistency
                            if tp_from_node['data']['ub'] < -tp_from_node['data']['lb']:
                                raise Exception("Inconsistent STN: upper bound less than lower bound")
                            
                            # If revisiting edge, raise inconsistency
                            if data.get('new_p', False) and data.get('lb_p', False):
                                raise Exception("Inconsistent STN: revisiting edge")
                            data['lb_p'] = True
                            if tp_from not in queue:
                                queue.append(tp_from)                        
                tp_node['data']['ub_p'] = False
                tp_node['data']['lb_p'] = False
            
            # reset edge flags on new constraint, data.lb_p, data.ub_p only needed internally so don't need to reset
            if new_constraint:
                self.reset_edge_flags(self[tp1][tp2])
                self.reset_edge_flags(self[tp2][tp1])

            return True
        
        except Exception as e:
            while undo_info: # pop in LIFO order
                undo_fn = undo_info.pop()
                undo_fn()
            self.delete_constraint(tp1, tp2, consistent=False)
            print(e)
            return False
