import numpy as np
import networkx as nx
from queue import deque


class STN(nx.MultiDiGraph):
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

    def delete_timepoint(self, tp):
        for succ in list(self.successors(tp)):
            for key in list(self[tp][succ].keys()):
                self.delete_constraint(tp, succ, key)
        for pred in list(self.predecessors(tp)):
            for key in list(self[pred][tp].keys()):
                self.delete_constraint(pred, tp, key)
        self.remove_node(tp)

    # release time, due date, sequence constraint, travel constraints, duration constraints
    def add_constraint(self, tp1, tp2, constraint_type, lb=0, ub=np.inf, print_inconsistencies=True):
        # if constraint type is not already a tuple make it one
        constraint_type = constraint_type if isinstance(constraint_type, tuple) else (constraint_type,)
        # add an element "new" to the tuple
        constraint_type = constraint_type + ("new",)
        self.add_edge(tp1, tp2, key=constraint_type, weight=ub, data={'new_p': True})
        self.add_edge(tp2, tp1, key=constraint_type, weight=-lb, data={'new_p': True})
        # propagate constraints
        return self.propagate([tp1, tp2], constraint_type, print_inconsistencies=print_inconsistencies)
    

    def delete_constraint(self, tp1, tp2, constraint_type, consistent=True):
        constraint_type = constraint_type if isinstance(constraint_type, tuple) else (constraint_type,)
        if not self.has_edge(tp1, tp2, key=constraint_type):
            return True  # Already deleted or never existed
        
        if consistent:
            # Step 1: Find all timepoints whose bounds depend on the edges being deleted
            v = set()  # timepoints needing bound resets
            
            # Check both directions for both UB and LB dependencies
            v.update(self._collect_affected_nodes(tp1, tp2, constraint_type, 'pu', self._ub_dependency_tree_forward))
            v.update(self._collect_affected_nodes(tp2, tp1, constraint_type, 'pu', self._ub_dependency_tree_forward))
            v.update(self._collect_affected_nodes(tp1, tp2, constraint_type, 'pl', self._lb_dependency_tree_forward))
            v.update(self._collect_affected_nodes(tp2, tp1, constraint_type, 'pl', self._lb_dependency_tree_forward))
            
            self._reset_ub_timepoints(v)
            self._reset_lb_timepoints(v)
            
            # Step 2: Collect neighbors for re-propagation
            q = set()
            for tp in v:
                q.update(self.predecessors(tp))
                q.update(self.successors(tp))
        
        # Step 3: Delete the actual edges
        self.remove_edge(tp1, tp2, key=constraint_type)
        self.remove_edge(tp2, tp1, key=constraint_type)
        
        if consistent:
            return self.propagate(q, new_constraint=False)
        
        return True

    def _collect_affected_nodes(self, tp_from, tp_to, constraint_type, parent_attr, tree_func):
        """
        Check if tp_to's bound is constrained by edge from tp_from, and collect dependency tree if so.
        
        Args:
            tp_from: Source timepoint of the edge being deleted
            tp_to: Target timepoint to check
            constraint_type: Type of constraint being deleted
            parent_attr: 'pu' for upper bound or 'pl' for lower bound
            tree_func: Function to build dependency tree (_ub_dependency_tree_forward or _lb_dependency_tree_forward)
        
        Returns:
            Set of affected timepoints (empty if edge doesn't constrain tp_to's bound)
        """
        parent_info = self.nodes[tp_to]['data'].get(parent_attr)
        if parent_info is not None:
            parent_tp, parent_constraint_type = parent_info
            if parent_tp == tp_from and parent_constraint_type == constraint_type:
                return tree_func(tp_to)
        return set()
    
    def _ub_dependency_tree_forward(self, start_tp, tree=None):
        """
        Build a tree of timepoints whose UB depends on start_tp.
        """
        if tree is None:
            tree = set()
        
        tree.add(start_tp)
        
        # Get all outgoing edges with their keys
        for _, neighbor, edge_key in self.out_edges(start_tp, keys=True):
            neighbor_pu = self.nodes[neighbor]['data'].get('pu')
            if neighbor_pu is not None:
                pu_tp, pu_constraint_type = neighbor_pu
                # Only recurse if this edge IS the UB-constraining edge
                if pu_tp == start_tp and pu_constraint_type == edge_key and neighbor not in tree:
                    self._ub_dependency_tree_forward(neighbor, tree)
        
        return tree
    
    def _lb_dependency_tree_forward(self, start_tp, tree=None):
        """
        Build a tree of timepoints whose LB depends on start_tp.
        """
        if tree is None:
            tree = set()
        
        tree.add(start_tp)
        
        # Get all incoming edges with their keys
        for predecessor, _, edge_key in self.in_edges(start_tp, keys=True):
            pred_pl = self.nodes[predecessor]['data'].get('pl')
            if pred_pl is not None:
                pl_tp, pl_constraint_type = pred_pl
                # Only recurse if this edge IS the LB-constraining edge
                if pl_tp == start_tp and pl_constraint_type == edge_key and predecessor not in tree:
                    self._lb_dependency_tree_forward(predecessor, tree)
        
        return tree
    
    def _reset_ub_timepoints(self, timepoints):
        """Reset upper bounds to infinity (unconstrained)."""
        for tp in timepoints:
            if tp != self.cz:  # Don't reset the zero timepoint
                self.nodes[tp]['data']['ub'] = np.inf
                self.nodes[tp]['data']['pu'] = None

    def _reset_lb_timepoints(self, timepoints):
        """Reset lower bounds to zero (unconstrained)."""
        for tp in timepoints:
            if tp != self.cz:  # Don't reset the zero timepoint
                self.nodes[tp]['data']['lb'] = 0
                self.nodes[tp]['data']['pl'] = None

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


    def propagate(self, initial_timepoints, new_constraint=False, print_inconsistencies=True):
        """
        Propagate constraints from initial timepoints.
        
        Args:
            initial_timepoints: Set or list of timepoints to start propagation from
            new_constraint: If True, this is a new constraint being added (enables undo on failure)
            print_inconsistencies: Whether to print error messages
        
        Returns:
            True if propagation succeeded, False if inconsistency detected
        """
        queue = deque(initial_timepoints)
        undo_info = deque() if new_constraint else None
        
        # Mark all initial timepoints for propagation
        for tp in initial_timepoints:
            self.nodes[tp]['data']['ub_p'] = True
            self.nodes[tp]['data']['lb_p'] = True
            if new_constraint:
                undo_info.append(lambda tp=tp: self._reset_timepoint_flags(tp))
        
        try:
            while queue:
                tp = queue.popleft()
                tp_node = self.nodes[tp]
                
                # Upper bound propagation
                if tp_node['data'].get('ub_p', False):
                    for tp_from, tp_to, key, data in self.out_edges(tp, keys=True, data=True):
                        tp_to_node = self.nodes[tp_to]
                        new_ub = tp_node['data']['ub'] + data['weight']
                        
                        old_ub = tp_to_node['data']['ub']
                        old_pu = tp_to_node['data'].get('pu', None)
                        
                        if new_ub < old_ub or (tp_to != self.cz and old_pu is None):
                            if new_constraint:
                                undo_info.append(lambda tp_to=tp_to, old_ub=old_ub, old_pu=old_pu:
                                            self._undo_ub(tp_to, old_ub, old_pu))
                            
                            tp_to_node['data']['ub'] = new_ub
                            tp_to_node['data']['ub_p'] = True
                            
                            # Store parent constraint (strip "new" marker if present)
                            pu_constraint_type = key
                            if isinstance(key, tuple) and "new" in key:
                                pu_constraint_type = tuple(x for x in key if x != "new")
                            tp_to_node['data']['pu'] = (tp, pu_constraint_type)
                            
                            # Check for inconsistency
                            if tp_to_node['data']['ub'] < -tp_to_node['data']['lb']:
                                raise Exception(f"Inconsistent STN: UB < LB at {tp_to}")
                            
                            # Check for cycle on new constraints
                            if new_constraint and data.get('new_p', False) and data.get('ub_p', False):
                                raise Exception(f"Inconsistent STN: revisiting edge ({tp}, {tp_to})")
                            
                            data['ub_p'] = True
                            if tp_to not in queue:
                                queue.append(tp_to)
                
                # Lower bound propagation
                if tp_node['data'].get('lb_p', False):
                    for tp_from, tp_to, key, data in self.in_edges(tp, keys=True, data=True):
                        tp_from_node = self.nodes[tp_from]
                        new_lb = tp_node['data']['lb'] + data['weight']
                        
                        old_lb = tp_from_node['data']['lb']
                        old_pl = tp_from_node['data'].get('pl', None)
                        
                        if new_lb < old_lb or (tp_from != self.cz and old_pl is None):
                            if new_constraint:
                                undo_info.append(lambda tp_from=tp_from, old_lb=old_lb, old_pl=old_pl:
                                            self._undo_lb(tp_from, old_lb, old_pl))
                            
                            tp_from_node['data']['lb'] = new_lb
                            tp_from_node['data']['lb_p'] = True
                            
                            # Store parent constraint (strip "new" marker if present)
                            pl_constraint_type = key
                            if isinstance(key, tuple) and "new" in key:
                                pl_constraint_type = tuple(x for x in key if x != "new")
                            tp_from_node['data']['pl'] = (tp, pl_constraint_type)
                            
                            # Check for inconsistency
                            if tp_from_node['data']['ub'] < -tp_from_node['data']['lb']:
                                raise Exception(f"Inconsistent STN: UB < LB at {tp_from}")
                            
                            # Check for cycle on new constraints
                            if new_constraint and data.get('new_p', False) and data.get('lb_p', False):
                                raise Exception(f"Inconsistent STN: revisiting edge ({tp_from}, {tp})")
                            
                            data['lb_p'] = True
                            if tp_from not in queue:
                                queue.append(tp_from)
                
                tp_node['data']['ub_p'] = False
                tp_node['data']['lb_p'] = False
            
            # Clean up new constraint markers if this was a new constraint
            if new_constraint and len(initial_timepoints) == 2:
                tp1, tp2 = list(initial_timepoints)
                # Find the constraint_type from the edge
                for key in self[tp1][tp2]:
                    if isinstance(key, tuple) and "new" in key:
                        constraint_type = key
                        self.reset_edge_flags(self[tp1][tp2][constraint_type])
                        self.reset_edge_flags(self[tp2][tp1][constraint_type])
                        
                        new_key = tuple(k for k in constraint_type if k != "new")
                        self.rename_edge_key(tp1, tp2, constraint_type, new_key)
                        self.rename_edge_key(tp2, tp1, constraint_type, new_key)
                        break
            
            return True
        
        except Exception as e:
            if new_constraint and undo_info:
                # Undo all changes
                while undo_info:
                    undo_fn = undo_info.pop()
                    undo_fn()
                
                # Delete the constraint that caused the problem
                if len(initial_timepoints) == 2:
                    tp1, tp2 = list(initial_timepoints)
                    for key in list(self[tp1][tp2].keys()):
                        if isinstance(key, tuple) and "new" in key:
                            self.delete_constraint(tp1, tp2, key, consistent=False)
                            break
            
            if print_inconsistencies:
                print(e)
            return False
        

    def rename_edge_key(self, u, v, old_key, new_key):
        data = self[u][v][old_key]
        self.remove_edge(u, v, key=old_key)
        self.add_edge(u, v, key=new_key, **data)
