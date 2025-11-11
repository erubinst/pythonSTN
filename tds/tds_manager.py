from stn.stn import STN
from timepoint import Timepoint
import numpy as np

class TDSManager:
    """
    A lightweight manager sitting on top of an STN.
    Responsible for creating, storing, and linking Timepoints.
    Simplified analogue of the Lisp TDS structure.
    """

    def __init__(self):
        # Initialize a fresh STN
        self.stn = STN()
        # Registry of timepoints (name -> Timepoint object)
        self.timepoints = {}

    # ----------------------------------------------------------------------
    # --- Timepoint management ---
    # ----------------------------------------------------------------------

    def add_timepoint(self, name, earliest=None, latest=None):
        """
        Create a new Timepoint and add it to the STN.

        Parameters
        ----------
        name : str
            The name of the timepoint.
        earliest : float, optional
            Lower bound on its occurrence (relative to zero).
        latest : float, optional
            Upper bound on its occurrence (relative to zero).

        Returns
        -------
        Timepoint
            The created Timepoint object.
        """
        if name in self.timepoints:
            raise ValueError(f"Timepoint '{name}' already exists in TDSManager.")
        tp = Timepoint(name, self.stn, earliest, latest)
        self.timepoints[name] = tp
        return tp

    def get_timepoint(self, name):
        """Retrieve an existing timepoint by name."""
        return self.timepoints.get(name, None)

    def remove_timepoint(self, name):
        """Remove a timepoint from the STN and registry (basic version)."""
        if name not in self.timepoints:
            raise KeyError(f"Timepoint '{name}' not found.")
        self.stn.remove_node(name)
        del self.timepoints[name]

    # ----------------------------------------------------------------------
    # --- Constraint management ---
    # ----------------------------------------------------------------------

    def constrain_before(self, tp1_name, tp2_name, min_gap=0, max_gap=np.inf):
        """
        Add a constraint between two existing timepoints.
        Equivalent to (constrain-before tp1 tp2) in Lisp.
        """
        tp1 = self.get_timepoint(tp1_name)
        tp2 = self.get_timepoint(tp2_name)
        if not tp1 or not tp2:
            raise KeyError(f"Both timepoints must exist: {tp1_name}, {tp2_name}")
        return tp1.constrain_before(tp2, min_gap, max_gap)


    def constrain_after(self, tp1_name, tp2_name, min_gap=0, max_gap=np.inf):
        """
        Add a constraint ensuring tp1 occurs after tp2.
        """
        tp1 = self.get_timepoint(tp1_name)
        tp2 = self.get_timepoint(tp2_name)
        if not tp1 or not tp2:
            raise KeyError(f"Both timepoints must exist: {tp1_name}, {tp2_name}")
        return tp1.constrain_after(tp2, min_gap, max_gap)


    def print_summary(self):
        """Print a summary of all timepoints and their bounds."""
        print("=== Temporal Domain Summary ===")
        for tp in self.timepoints.values():
            print(f"{tp.name:20s} LB={tp.lb:.2f}, UB={tp.ub:.2f}")
        print()


    def __repr__(self):
        return f"<TDSManager with {len(self.timepoints)} timepoints>"
