"""Numerical checks of the theoretical statements (separate from the dynamic campaign).

T1: complete route-column set packing == arc MILP (swap-free specialization), HiGHS via SciPy.
T2: conditional thermal bound vs. true two-node physics under random admissible ambient traces.
T3: token protocol under adversarial delay, loss, duplication, reordering and partitions.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from .common import load_config
from .protocol import HELD_BY_CENTER, TokenRegistry, TokenWallet
from .routing import RouteEvaluator, Snapshot, TravelBelief
from .runtime import carrier_and_bound
from .scenario import ScenarioGenerator, ScenarioKey, ScenarioModel
from .thermal import CarrierPhysics, ThermalBound, calibrate_class


# ============================================================================ T1
def small_instance(seed: int, n: int, thermal: bool, budget_scale: float):
    cfg = load_config()
    key = ScenarioKey(seed, 2, 0.5, False, thermal, "N0")
    sc = ScenarioGenerator(cfg).build(key)
    rng = random.Random(seed)
    reqs = [r for r in sc["requests"]][:n]
    for i, r in enumerate(reqs):
        r["id"] = i
        r["static"] = True
        r["release"] = 0
        if not r["static"]:
            pass
    sc["requests"] = reqs
    model = ScenarioModel(sc, cfg)
    cls, bound = carrier_and_bound(sc, cfg)
    # scale the available latent heat to make the thermal budget bind in some instances
    bound.full_j *= budget_scale
    bound.reserve_j = cfg["carrier"]["reserve_fraction"] * bound.full_j
    T = TravelBelief(model).exp0
    ev = RouteEvaluator(model, cfg, bound, T, allow_swaps=False)
    return cfg, sc, model, bound, ev, T


def column_value(ev, model, bound, K):
    """Complete enumeration of elementary routes, earliest-start schedules; set packing MILP."""
    N = model.N
    cols = []
    for k in range(K):
        snap = Snapshot(k, model.hub_node(k), 0, bound.full_j, 12, False)
        for L in range(0, N + 1):
            for seq in itertools.permutations(range(N), L):
                res = ev.evaluate(snap, list(seq), strict=True)
                if res is not None:
                    cols.append((k, seq, res.score))
    m = len(cols)
    A = lil_matrix((N + K, m))
    for j, (k, seq, _) in enumerate(cols):
        A[N + k, j] = 1
        for i in seq:
            A[i, j] = 1
    c = -np.array([s for _, _, s in cols])
    res = milp(c, integrality=np.ones(m), bounds=Bounds(0, 1),
               constraints=LinearConstraint(A.tocsr(), -np.inf, np.ones(N + K)),
               options=dict(mip_rel_gap=1e-12, time_limit=120))
    return -res.fun, m


def arc_value(cfg, model, bound, T, K):
    """Arc formulation with explicit clocks; waiting allowed; linear thermal budget."""
    N = model.N
    o = cfg["objective"]
    R = model.R
    shift, limit = model.shift, model.shift + model.max_ot
    rate = bound.rate_w * 60.0
    budget = bound.full_j - bound.reserve_j
    imp = bound.impulse_j
    M = limit + max(max(row) for row in T) + 100
    var = {}

    def v(name):
        if name not in var:
            var[name] = len(var)
        return var[name]

    S, E = "s", "e"
    arcs = {}
    for k in range(K):
        h = model.hub_node(k)
        nodes = [S] + list(range(N)) + [E]
        for i in nodes:
            for j in nodes:
                if i == j or i == E or j == S or (i == S and j == E and False):
                    continue
                if i == S and j == E:
                    tt = 0
                elif i == S:
                    tt = T[h][j]
                elif j == E:
                    tt = T[i][h]
                else:
                    tt = T[i][j]
                arcs[(i, j, k)] = tt
                v(("x", i, j, k))
        for i in range(N):
            v(("y", i, k))
            v(("T", i, k))
        v(("E", k))
        v(("O", k))
    nv = len(var)
    cost = np.zeros(nv)
    integ = np.zeros(nv)
    lb = np.zeros(nv)
    ub = np.full(nv, np.inf)
    for key, idx in var.items():
        if key[0] in ("x", "y"):
            integ[idx] = 1
            ub[idx] = 1
    for (i, j, k), tt in arcs.items():
        cost[var[("x", i, j, k)]] += o["w_travel_per_min"] * tt
    for k in range(K):
        for i in range(N):
            cost[var[("y", i, k)]] -= o["w_priority"] * R[i]["priority"]
            if R[i]["skill"] == 1 and not model.teams[k]["advanced"]:
                ub[var[("y", i, k)]] = 0
            ub[var[("T", i, k)]] = R[i]["b"]
        cost[var[("O", k)]] = o["w_overtime_per_min"]
        ub[var[("E", k)]] = limit
    rows, lo, hi = [], [], []

    def add(coeffs, l, u):
        rows.append(coeffs)
        lo.append(l)
        hi.append(u)

    for k in range(K):
        add({var[("x", S, j, k)]: 1 for j in list(range(N)) + [E]}, 1, 1)
        for i in range(N):
            out = {var[("x", i, j, k)]: 1 for j in list(range(N)) + [E] if j != i}
            out[var[("y", i, k)]] = -1
            add(out, 0, 0)
            inn = {var[("x", j, i, k)]: 1 for j in [S] + list(range(N)) if j != i}
            inn[var[("y", i, k)]] = -1
            add(inn, 0, 0)
            add({var[("T", i, k)]: 1, var[("y", i, k)]: -R[i]["a"]}, 0, np.inf)
            add({var[("T", i, k)]: 1, var[("y", i, k)]: -R[i]["b"]}, -np.inf, 0)
            # from depot: T_i >= tau_0i - M(1-x)
            add({var[("T", i, k)]: 1, var[("x", S, i, k)]: -(arcs[(S, i, k)] + M)}, -M, np.inf)
            for j in range(N):
                if i != j:
                    add({var[("T", j, k)]: 1, var[("T", i, k)]: -1,
                         var[("x", i, j, k)]: -(R[i]["service_expected"] + arcs[(i, j, k)] + M)}, -M, np.inf)
            add({var[("E", k)]: 1, var[("T", i, k)]: -1,
                 var[("x", i, E, k)]: -(R[i]["service_expected"] + arcs[(i, E, k)] + M)}, -M, np.inf)
        add({var[("O", k)]: 1, var[("E", k)]: -1}, -shift, np.inf)
        th = {var[("E", k)]: rate}
        for i in range(N):
            if R[i]["cold"]:
                th[var[("y", i, k)]] = imp
        add(th, -np.inf, budget)
    for i in range(N):
        add({var[("y", i, k)]: 1 for k in range(K)}, 0, 1)
    A = lil_matrix((len(rows), nv))
    for r, coeffs in enumerate(rows):
        for j, a in coeffs.items():
            A[r, j] = a
    res = milp(cost, integrality=integ, bounds=Bounds(lb, ub),
               constraints=LinearConstraint(A.tocsr(), np.array(lo), np.array(hi)),
               options=dict(mip_rel_gap=1e-12, time_limit=120))
    return -res.fun, res.status


def check_t1(n_cases=48):
    out = []
    cases = 0
    for seed in range(9301, 9301 + 200):
        if cases >= n_cases:
            break
        n = 3 + (seed % 5)            # 3..7 requests
        thermal = seed % 2 == 0
        scale = 1.0 if seed % 3 else 0.35
        cfg, sc, model, bound, ev, T = small_instance(seed, n, thermal, scale)
        if model.N < 3:
            continue
        cv, ncols = column_value(ev, model, bound, 2)
        av, st = arc_value(cfg, model, bound, T, 2)
        out.append(dict(seed=seed, n=model.N, thermal=thermal, budget_scale=scale, columns=ncols,
                        column_value=round(cv, 9), arc_value=round(av, 9), abs_diff=abs(cv - av), status=st))
        cases += 1
    return out


# ============================================================================ T2
def check_t2(trials=2000, seed=424242):
    """Random piecewise-constant ambient traces below the envelope, random lid openings with
    separation >= vaccination time, true UA factor <= planning factor. Verify theta <= theta_ub
    while latent heat remains and consumed energy <= planned consumption."""
    cfg = load_config()
    rng = random.Random(seed)
    out = dict(trials=0, theta_violations=0, energy_violations=0, low_violations=0, max_theta_margin=-1e9,
               max_energy_ratio=0.0, min_low_margin=1e9)
    cc = cfg["carrier"]
    for name in ("PQS_SR", "NQ_BAG"):
        cls = calibrate_class(cfg, name)
        for envelope in (21.0, 27.15, 32.0):
            bound = ThermalBound(cls, cfg, envelope)
            for _ in range(trials // 6):
                f = rng.uniform(cc["true_ua_factor_range"][0], cc["planning_ua_factor"])
                phys = CarrierPhysics(cls.scaled(f), cc["theta_init_c"], cc["initial_melt_fraction"] * cls.L)
                minutes = rng.randint(60, 400)
                sep = int(cfg["demand"]["vaccination_fixed_min"]) + rng.randint(0, 40)
                next_open = rng.randint(1, sep)
                opens = 0
                amb = rng.uniform(15.0, envelope)
                e0 = phys.latent_remaining_j()
                ok_region = True
                for m in range(minutes):
                    if rng.random() < 0.05:
                        amb = rng.uniform(15.0, envelope)
                    lid = 0.0
                    if m == next_open:
                        lid = cc["lid_open_s"]
                        opens += 1
                        next_open = m + sep + rng.randint(0, 30)
                    phys.step_minute(amb, lid)
                    if phys.latent_remaining_j() <= 0.0:
                        ok_region = False
                        break
                    margin = phys.theta - bound.theta_ub
                    out["max_theta_margin"] = max(out["max_theta_margin"], margin)
                    if margin > 1e-9:
                        out["theta_violations"] += 1
                    low = phys.theta - min(cc["theta_init_c"], bound.ratio * 15.0)
                    out["min_low_margin"] = min(out["min_low_margin"], low)
                    if low < -1e-9:
                        out["low_violations"] += 1
                used = e0 - phys.latent_remaining_j()
                planned = bound.consumption(m + 1, opens) / bound.s_hi * bound.s_hi
                if ok_region:
                    out["max_energy_ratio"] = max(out["max_energy_ratio"], used / planned)
                    if used > planned + 1e-6:
                        out["energy_violations"] += 1
                out["trials"] += 1
    return out


# ============================================================================ T3
def check_t3(trials=3000, seed=99):
    """Adversarial scheduler: random delays, loss with retry, duplicates, reordering, partitions;
    random centre decisions (assign/revoke) and random vehicle actions (serve/release).
    Invariant: at most one live holder per request; service only by a holder; no double service."""
    rng = random.Random(seed)
    viol = dict(trials=0, multi_holder=0, double_service=0, unauthorized=0, events=0)
    for tr in range(trials):
        K = rng.randint(2, 5)
        R = rng.randint(1, 4)
        reg = TokenRegistry()
        wallets = [TokenWallet() for _ in range(K)]
        committed = [None] * K
        served = {}
        for r in range(R):
            reg.create(r)
        inflight = []   # (deliver_at, dst, msg)
        for t in range(120):
            # centre actions
            for r in range(R):
                if rng.random() < 0.08 and reg.holder[r] == HELD_BY_CENTER:
                    k = rng.randrange(K)
                    ver = reg.assign(r, k, t)
                    inflight.append((t + rng.randint(0, 6), ("V", k), ("ASG", r, ver)))
                elif rng.random() < 0.08 and isinstance(reg.holder[r], int) and r not in reg.pending:
                    k, ver = reg.request_revoke(r, rng.choice([None, rng.randrange(K)]), t)
                    inflight.append((t + rng.randint(0, 6), ("V", k), ("REV", r, ver)))
            # vehicle actions
            for k in range(K):
                w = wallets[k]
                if committed[k] is not None and rng.random() < 0.3:
                    r = committed[k]
                    v = w.finish(r, t)
                    committed[k] = None
                    inflight.append((t + rng.randint(0, 6), ("C", k), ("DONE", r, v)))
                    continue
                if committed[k] is None and w.held and rng.random() < 0.2:
                    r = rng.choice(sorted(w.held))
                    if r in served:
                        viol["double_service"] += 1
                    served[r] = k
                    committed[k] = r
                elif w.held and rng.random() < 0.05:
                    r = rng.choice(sorted(w.held))
                    if r != committed[k]:
                        v = w.release(r, t)
                        inflight.append((t + rng.randint(0, 6), ("C", k), ("REL", r, v)))
            # adversarial delivery: partitions block, duplicates and reordering allowed
            partitioned = {k for k in range(K) if rng.random() < 0.2}
            rng.shuffle(inflight)
            keep = []
            for (due, dst, msg) in inflight:
                k = dst[1]
                if due > t or k in partitioned or rng.random() < 0.1:
                    keep.append((due, dst, msg))
                    continue
                viol["events"] += 1
                if rng.random() < 0.1:
                    keep.append((t + rng.randint(1, 5), dst, msg))   # duplicate
                typ, r, v = msg
                if dst[0] == "V":
                    w = wallets[k]
                    if typ == "ASG":
                        w.on_assign(r, v, t)
                    else:
                        rep = w.on_revoke(r, v, t, committed=(committed[k] == r))
                        if rep == "REL":
                            keep.append((t + rng.randint(0, 6), ("C", k), ("REL", r, v)))
                        elif rep == "STA":
                            keep.append((t + rng.randint(0, 6), ("C", k), ("STA", r, v)))
                else:
                    if typ == "REL":
                        reg.on_released(r, v, k, t)
                    elif typ == "STA":
                        reg.on_started(r, v, k)
                    elif typ == "DONE":
                        reg.on_done(r, v, k, t)
            inflight = keep
            for r in range(R):
                holders = [k for k in range(K) if wallets[k].holds(r)]
                if len(holders) > 1:
                    viol["multi_holder"] += 1
                if holders and reg.holder[r] == HELD_BY_CENTER:
                    viol["multi_holder"] += 1
            for k in range(K):
                if committed[k] is not None and not wallets[k].holds(committed[k]):
                    viol["unauthorized"] += 1
        viol["trials"] += 1
    return viol


def main(out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t1 = check_t1()
    (out / "t1_column_arc.json").write_text(json.dumps(t1, indent=1))
    t2 = check_t2()
    (out / "t2_thermal_bound.json").write_text(json.dumps(t2, indent=1))
    t3 = check_t3()
    (out / "t3_token_protocol.json").write_text(json.dumps(t3, indent=1))
    print("T1 cases", len(t1), "max |diff|", max(r["abs_diff"] for r in t1), "statuses", {r["status"] for r in t1})
    print("T2", t2)
    print("T3", t3)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "verification")
