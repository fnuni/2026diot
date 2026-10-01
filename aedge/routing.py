"""Route evaluation shared by every controller (identical feasibility semantics).

A route is evaluated from a vehicle snapshot. Swaps are inserted just in time:
before moving to the next element, the vehicle checks that it could serve that
element and still reach its hub within the conditional thermal budget and dose
stock; otherwise it first returns to the hub and exchanges the carrier. This
keeps the invariant "the carrier can always be returned within budget".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .common import SWAP


@dataclass
class Snapshot:
    team: int
    node: int
    t_free: int
    e_rem: float
    doses: int
    quarantined: bool

    def as_dict(self):
        return dict(team=self.team, node=self.node, t_free=self.t_free, e_rem=round(self.e_rem, 3),
                    doses=self.doses, quarantined=self.quarantined)

    @staticmethod
    def from_dict(d):
        return Snapshot(d["team"], d["node"], d["t_free"], d["e_rem"], d["doses"], d["quarantined"])


@dataclass
class RouteResult:
    feasible: bool
    score: float
    plan: list            # executable sequence including SWAP markers
    served: list
    skipped: list
    starts: dict = field(default_factory=dict)
    end: int = 0
    travel: int = 0
    overtime: int = 0
    swaps: int = 0


class TravelBelief:
    """Expected travel times given an actor's belief about the incident."""

    def __init__(self, model):
        self.m = model
        n = model.n_nodes
        self.exp0 = [[int(math.ceil(model.base[i][j])) if i != j else 0 for j in range(n)] for i in range(n)]
        inc = model.incident
        if inc:
            f = inc["factor"]
            z = model.in_zone
            self.exp1 = [[int(math.ceil(model.base[i][j] * f)) if (i != j and (z[i] or z[j])) else self.exp0[i][j]
                          for j in range(n)] for i in range(n)]
        else:
            self.exp1 = self.exp0

    def matrix(self, incident_active: bool):
        return self.exp1 if incident_active else self.exp0


class RouteEvaluator:
    def __init__(self, model, cfg: dict, bound, travel_matrix, allow_swaps: bool = True):
        self.m = model
        self.allow_swaps = allow_swaps
        self.R = model.R
        self.bound = bound
        self.T = travel_matrix
        o = cfg["objective"]
        self.wp, self.wt, self.wo, self.ws = o["w_priority"], o["w_travel_per_min"], o["w_overtime_per_min"], o["w_swap"]
        self.limit = model.shift + model.max_ot
        self.shift = model.shift
        self.cap = cfg["carrier"]["capacity_doses"]
        self.swap_min = cfg["carrier"]["swap_min"]
        self.rate_min = bound.rate_w * 60.0
        self.imp = bound.impulse_j
        self.reserve = bound.reserve_j
        self.full = bound.full_j
        self.cold_ok = bound.temperature_safe
        self.advanced = [t["advanced"] for t in model.teams]
        self.hub = [model.hub_node(k) for k in range(model.K)]

    def evaluate(self, snap: Snapshot, seq, strict: bool = True):
        """Return RouteResult; in strict mode None if any visit cannot be served."""
        R, T = self.R, self.T
        hub = self.hub[snap.team]
        adv = self.advanced[snap.team]
        limit = self.limit
        t = snap.t_free
        node = snap.node
        e = snap.e_rem
        doses = snap.doses
        q = snap.quarantined
        plan, served, skipped, starts = [], [], [], {}
        travel = 0
        swaps = 0
        prize = 0
        rate = self.rate_min
        imp = self.imp
        reserve = self.reserve
        for j in seq:
            if j == SWAP:
                continue  # swaps are re-derived just in time
            r = R[j]
            # static admissibility
            if (r["skill"] == 1 and not adv) or (r["cold"] and not self.cold_ok):
                if strict:
                    return None
                skipped.append(j)
                continue
            # decide whether a swap is needed before j
            need_swap = False
            if r["cold"] and (q or doses < r["doses"]):
                need_swap = True
            else:
                tt = T[node][j]
                arr = t + tt
                st = arr if arr >= r["a"] else r["a"]
                fin = st + r["service_expected"]
                back = fin + T[j][hub]
                exposed = (back - t) if node != hub else (back - t)
                ncold = 1 if r["cold"] else 0
                if e - rate * exposed - imp * ncold < reserve - 1e-9:
                    need_swap = True
            if need_swap and not self.allow_swaps:
                if strict:
                    return None
                skipped.append(j)
                continue
            if need_swap and node != hub:
                # go to hub and swap first
                leg = T[node][hub]
                travel += leg
                t += leg
                t += self.swap_min
                node = hub
                e = self.full
                doses = self.cap
                q = False
                swaps += 1
                plan.append(SWAP)
            elif need_swap and node == hub:
                t += self.swap_min
                e = self.full
                doses = self.cap
                q = False
                swaps += 1
                plan.append(SWAP)
            # attempt j
            leg = T[node][j]
            arr = t + leg
            st = arr if arr >= r["a"] else r["a"]
            fin = st + r["service_expected"]
            back = fin + T[j][hub]
            ok = st <= r["b"] and back <= limit
            if ok and r["cold"]:
                if q or doses < r["doses"] or e - rate * (back - t) - imp < reserve - 1e-9:
                    ok = False
            if not ok:
                if strict:
                    return None
                skipped.append(j)
                continue
            e -= rate * (fin - t) + (imp if r["cold"] else 0.0)
            travel += leg
            t = fin
            node = j
            if r["cold"]:
                doses -= r["doses"]
            prize += r["priority"]
            served.append(j)
            plan.append(j)
            starts[j] = st
        leg = T[node][hub]
        end = t + leg
        travel += leg
        if end > limit and served:
            if strict:
                return None
        overtime = end - self.shift if end > self.shift else 0
        score = self.wp * prize - self.wt * travel - self.wo * overtime - self.ws * swaps
        return RouteResult(True, score, plan, served, skipped, starts, end, travel, overtime, swaps)

    def empty_score(self, snap: Snapshot) -> float:
        return self.evaluate(snap, [], strict=False).score
