"""Boundary audit documenting production return semantics."""
from aedge.common import load_config
from aedge.runtime import carrier_and_bound
from aedge.scenario import ScenarioGenerator,ScenarioKey,ScenarioModel
from aedge.routing import RouteEvaluator,Snapshot,TravelBelief

def setup():
 cfg=load_config();sc=ScenarioGenerator(cfg).build(ScenarioKey(7102,2,.5,False,False,'N0'))
 model=ScenarioModel(sc,cfg);_,bound=carrier_and_bound(sc,cfg)
 ev=RouteEvaluator(model,cfg,bound,TravelBelief(model).exp0,allow_swaps=False)
 return ev,bound

def test_empty_late_return_is_continuation_not_certificate():
 ev,b=setup();snap=Snapshot(0,0,ev.limit,b.full_j,12,False)
 result=ev.evaluate(snap,[],strict=True)
 assert result is not None and result.end>ev.limit and result.score<0

def test_empty_energy_deficit_is_not_feasible_column_check():
 ev,b=setup();snap=Snapshot(0,0,10,0,12,False)
 result=ev.evaluate(snap,[],strict=True)
 assert result is not None
 assert ev.rate_min*(result.end-snap.t_free)>snap.e_rem-b.reserve_j
