import numpy as np

class Timepoint:
    """
    Represents a temporal event (a node) in the STN.
    Inspired by the 'timepoint' struct in the Lisp TDS layer.
    """

    def __init__(self, name, stn, earliest=None, latest=None):
        """
        Initialize a timepoint and optionally set temporal bounds.

        Parameters
        ----------
        name : str
            Symbolic identifier (e.g., 'task1_start')
        stn : STN
            Reference to the Simple Temporal Network instance
        earliest : float, optional
            Lower bound on time (relative to zero timepoint)
        latest : float, optional
            Upper bound on time (relative to zero timepoint)
        """
        self.name = name
        self.stn = stn
        self.stn.add_timepoint(name)
        self.constraints = []  # keep track of constraints involving this tp
        self.value = None      # assigned time, if computed later

        # Add optional constraints to the STN relative to zero timepoint
        if earliest is not None:
            # timepoint - zero ≥ earliest   → add_constraint(zero, tp, lb=earliest)
            self.stn.add_constraint(self.stn.cz, self.name, lb=earliest, ub=np.inf)
            self.constraints.append(("earliest", earliest))
        if latest is not None:
            # zero - timepoint ≥ -latest   → add_constraint(tp, zero, lb=-np.inf, ub=latest)
            self.stn.add_constraint(self.name, self.stn.cz, lb=0, ub=latest)
            self.constraints.append(("latest", latest))


    @property
    def lb(self):
        """Return the current lower bound in the STN."""
        return self.stn.nodes[self.name]['data']['lb']

    @property
    def ub(self):
        """Return the current upper bound in the STN."""
        return self.stn.nodes[self.name]['data']['ub']

    # --- Lisp-style helper operations ----------------------------------------

    def constrain_before(self, other, min_gap=0, max_gap=np.inf):
        """
        Ensure self occurs before 'other' by at least min_gap and at most max_gap.
        Equivalent to (constrain-before self other) in Lisp.

        Adds constraint: other - self ∈ [min_gap, max_gap]
        """
        ok = self.stn.add_constraint(self.name, other.name, lb=min_gap, ub=max_gap)
        self.constraints.append(("before", other.name, min_gap, max_gap))
        return ok

    def constrain_after(self, other, min_gap=0, max_gap=np.inf):
        """
        Ensure self occurs after 'other' by at least min_gap and at most max_gap.
        Adds constraint: self - other ∈ [min_gap, max_gap]
        """
        ok = self.stn.add_constraint(other.name, self.name, lb=min_gap, ub=max_gap)
        self.constraints.append(("after", other.name, min_gap, max_gap))
        return ok

    def set_value(self, value):
        """Store a concrete assigned time (if available)."""
        self.value = value

    def __repr__(self):
        lb = self.stn.nodes[self.name]['data']['lb']
        ub = self.stn.nodes[self.name]['data']['ub']
        return f"<Timepoint {self.name} LB={lb:.2f}, UB={ub:.2f}>"
