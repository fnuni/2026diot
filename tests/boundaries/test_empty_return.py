"""Boundary checks for the manuscript equations and route evaluator.
No-request snapshots expose costs and infeasibility hidden by hub-only T1 tests.
"""
import numpy as np
import pytest
from scipy.optimize import milp, Bounds, LinearConstraint

@pytest.mark.parametrize('t0,travel,budget,expected',[(0,0,100,0),(10,12,100,-12),(355,12,100,-26),(10,12,11,None)])
def test_arc_and_required_empty_column(t0,travel,budget,expected):
    # x=start->end, E=return time, O=overtime; heat rate=1; H=360.
    # Minimise travel*x + 2*O; direct-return timing is mandatory.
    arc=milp([travel,0,2],integrality=[1,0,0],bounds=Bounds([1,t0,0],[1,405,np.inf]),
             constraints=LinearConstraint([[0,1,0],[0,-1,1],[0,1,0]],
                                          [t0+travel,-360,-np.inf],[np.inf,np.inf,t0+budget]))
    feasible=t0+travel<=405 and travel<=budget
    # The only column is the empty service sequence with its return cost.
    if not feasible:
        assert arc.status==2 and expected is None
        return
    value=-travel-2*max(0,t0+travel-360)
    col=milp([-value],integrality=[1],bounds=Bounds([0],[1]),constraints=LinearConstraint([[1]],[1],[1]))
    assert arc.status==0 and col.status==0
    assert -arc.fun==pytest.approx(value)==expected
    assert -col.fun==pytest.approx(value)
    if value<0:
        optional=milp([-value],integrality=[1],bounds=Bounds([0],[1]))
        assert optional.fun==0 # old <=1 skips mandatory negative return cost
