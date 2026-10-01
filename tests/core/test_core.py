"""Unit and integration tests for the study simulator."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aedge.common import load_config, sha, SWAP
from aedge.metrics import reduce, stable_view
from aedge.protocol import HELD_BY_CENTER, TokenRegistry, TokenWallet
from aedge.routing import RouteEvaluator, Snapshot, TravelBelief
from aedge.runtime import InProcessRuntime, MultiprocessRuntime, carrier_and_bound, morning_plan
from aedge.scenario import ScenarioGenerator, ScenarioKey, ScenarioModel
from aedge.thermal import calibrate_class, cold_life_hours
from aedge.verification import arc_value, check_t2, check_t3, column_value, small_instance

CFG = load_config()


def test_calibration_hits_pqs_cold_life():
    for name in ("PQS_SR", "NQ_BAG"):
        cls = calibrate_class(CFG, name)
        assert abs(cold_life_hours(cls) - CFG["carrier"]["classes"][name]["cold_life_43_h"]) < 0.05


def test_calibration_fails_when_target_is_not_bracketed():
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["carrier"]["ua_pack_to_wall_ratio"] = 3.0
    try:
        calibrate_class(cfg, "PQS_SR")
    except ValueError as exc:
        assert "not bracketed" in str(exc)
    else:
        raise AssertionError("incompatible calibration must fail loudly")


def test_factor_masks_share_requests_and_links():
    g = ScenarioGenerator(CFG)
    a = g.build(ScenarioKey(7001, 8, 0.9, False, False, "N3"))
    b = g.build(ScenarioKey(7001, 8, 0.9, True, True, "N3"))
    assert a["requests"] == b["requests"] and a["links"] == b["links"] and a["towns"] == b["towns"]
    assert a["incident"] is None and b["incident"] is not None


def test_load_and_size_are_controlled():
    g = ScenarioGenerator(CFG)
    for K in (4, 8, 16):
        sc = g.build(ScenarioKey(7001, K, 0.9, True, True, "N1"))
        area_per_team = sc["side_km"] ** 2 / K
        assert abs(area_per_team - CFG["geography"]["area_per_team_km2"]) < 1e-3
        n = len(sc["requests"])
        assert abs(n - 0.9 * K * 360 / CFG["demand"]["mean_workload_per_visit_min"]) <= 0.5


def test_strict_evaluation_respects_windows_and_budget():
    sc = ScenarioGenerator(CFG).build(ScenarioKey(7003, 8, 0.9, False, True, "N0"))
    model = ScenarioModel(sc, CFG)
    cls, bound = carrier_and_bound(sc, CFG)
    ev = RouteEvaluator(model, CFG, bound, TravelBelief(model).exp0)
    rng = random.Random(1)
    for _ in range(300):
        k = rng.randrange(model.K)
        seq = rng.sample(range(model.N), rng.randint(1, 8))
        snap = Snapshot(k, model.hub_node(k), 0, bound.full_j, 12, False)
        res = ev.evaluate(snap, seq, strict=False)
        for j in res.served:
            r = model.R[j]
            assert r["a"] <= res.starts[j] <= r["b"]
        assert res.end <= model.shift + model.max_ot or not res.served


def test_column_arc_equivalence_small():
    for seed in (9301, 9302, 9303, 9306):
        cfg, sc, model, bound, ev, T = small_instance(seed, 5, seed % 2 == 0, 1.0 if seed % 3 else 0.35)
        cv, _ = column_value(ev, model, bound, 2)
        av, st = arc_value(cfg, model, bound, T, 2)
        assert st == 0 and abs(cv - av) < 1e-6


def test_thermal_bound_monte_carlo():
    r = check_t2(300)
    assert r["theta_violations"] == 0 and r["energy_violations"] == 0 and r["low_violations"] == 0


def test_sensor_error_margin_is_explicit():
    sc = ScenarioGenerator(CFG).build(ScenarioKey(7001, 8, 0.9, False, False, "N0"))
    _, bound = carrier_and_bound(sc, CFG)
    assert bound.safe_with_sensor_error(0.5)
    assert not bound.safe_with_sensor_error(10.0)


def test_token_protocol_adversarial():
    r = check_t3(400)
    assert r["multi_holder"] == 0 and r["double_service"] == 0 and r["unauthorized"] == 0


def test_token_check_detects_broken_wallet(monkeypatch):
    """Mutation test: a wallet that accepts stale versions must be caught by the checker."""
    def broken(self, r, v, t):
        self.known[r] = v
        self.held[r] = v
        return True
    monkeypatch.setattr(TokenWallet, "on_assign", broken)
    r = check_t3(400)
    assert r["multi_holder"] > 0 or r["double_service"] > 0


def test_revoke_before_assign_is_fenced():
    reg = TokenRegistry()
    w = TokenWallet()
    reg.create(0)
    v = reg.assign(0, 1, 0)
    reg.request_revoke(0, None, 1)
    assert w.on_revoke(0, v, 2, committed=False) == "REL"
    ok, _ = reg.on_released(0, v, 1, 3)
    assert ok and reg.holder[0] == HELD_BY_CENTER
    assert w.on_assign(0, v, 4) is False and not w.holds(0)


def test_run_invariants_and_backend_equivalence():
    sc = ScenarioGenerator(CFG).build(ScenarioKey(7002, 8, 0.9, True, True, "N3"))
    mp = morning_plan(sc, CFG)
    for method in ("CENTRAL", "HYBRID", "EDGE_MARKET"):
        l1 = InProcessRuntime(sc, CFG, method, mp).run()
        k = reduce(sc, l1, CFG)
        assert k["violations"] == 0 and k["token_overlaps"] == 0 and k["unauthorized_services"] == 0
        assert k["cold_after_quarantine"] == 0 and k["not_home"] == 0
        assert k["handled"] + k["missed"] == k["N"]
    l2 = MultiprocessRuntime(sc, CFG, "HYBRID", mp).run()
    l1 = InProcessRuntime(sc, CFG, "HYBRID", mp).run()
    assert sha(stable_view(l1)) == sha(stable_view(l2))


def test_repeatability_inprocess():
    sc = ScenarioGenerator(CFG).build(ScenarioKey(7004, 8, 0.9, True, False, "N2"))
    mp = morning_plan(sc, CFG)
    a = InProcessRuntime(sc, CFG, "HYBRID", mp).run()
    b = InProcessRuntime(sc, CFG, "HYBRID", mp).run()
    assert sha(stable_view(a)) == sha(stable_view(b))
