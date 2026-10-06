"""Planning algorithms (polymorphic): regret insertion, global ALNS, local exact/LS planner."""
from __future__ import annotations

import itertools
import math
import time

from .routing import RouteEvaluator, Snapshot


class PlanningContext:
    """Memoised strict route values for a set of vehicle snapshots."""

    def __init__(self, evaluator: RouteEvaluator, snaps: dict):
        self.ev = evaluator
        self.snaps = snaps
        self.cache = {}
        self.evaluations = 0

    def value(self, team: int, seq):
        key = (team, tuple(seq))
        res = self.cache.get(key)
        if res is None and key not in self.cache:
            self.evaluations += 1
            res = self.ev.evaluate(self.snaps[team], seq, strict=True)
            self.cache[key] = res
        return res

    def eligible(self, team: int, r: int) -> bool:
        req = self.ev.R[r]
        if req["skill"] == 1 and not self.ev.advanced[team]:
            return False
        return True


class Planner:
    """Base class; subclasses implement solve()."""

    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg


class RegretInsertion(Planner):
    name = "regret2"

    def _best_insertions(self, ctx: PlanningContext, team: int, seq: list, base_res, r: int):
        """All feasible (delta, pos) for inserting r into seq (strict)."""
        ev = ctx.ev
        R = ev.R
        T = ev.T
        req = R[r]
        snap = ctx.snaps[team]
        out = []
        starts = base_res.starts
        for pos in range(len(seq) + 1):
            if pos == 0:
                pred_node, pred_fin = snap.node, snap.t_free
            else:
                p = seq[pos - 1]
                pred_node, pred_fin = p, starts.get(p, 0) + R[p]["service_expected"]
            if pred_fin + T[pred_node][r] > req["b"]:
                continue  # arrival lower bound already violates the latest start
            cand = seq[:pos] + [r] + seq[pos:]
            res = ctx.value(team, cand)
            if res is not None:
                out.append((res.score - base_res.score, pos))
        return out

    def insert(self, ctx: PlanningContext, routes: dict, pool, rng=None, max_time=None, t0=None):
        routes = {k: list(v) for k, v in routes.items()}
        base = {k: ctx.value(k, v) for k, v in routes.items()}
        dropped = []
        for k, res in base.items():
            if res is None:  # infeasible incumbent: rebuild by skip semantics
                rr = ctx.ev.evaluate(ctx.snaps[k], routes[k], strict=False)
                routes[k] = list(rr.served)
                dropped.extend(rr.skipped)
                base[k] = ctx.value(k, routes[k])
                while base[k] is None and routes[k]:
                    dropped.append(routes[k].pop())
                    base[k] = ctx.value(k, routes[k])
        left = sorted(set(pool) | set(dropped))
        best = {}
        dirty = set(routes)
        while left:
            if max_time is not None and time.perf_counter() - t0 > max_time:
                break
            choice = None
            for r in left:
                opts = []
                for k in routes:
                    if not ctx.eligible(k, r):
                        continue
                    key = (r, k)
                    if k in dirty or key not in best:
                        ins = self._best_insertions(ctx, k, routes[k], base[k], r)
                        best[key] = max(ins) if ins else None
                    b = best[key]
                    if b is not None:
                        opts.append((b[0], k, b[1]))
                if not opts:
                    continue
                opts.sort(key=lambda x: (-x[0], x[1], x[2]))
                top = opts[0]
                regret = (top[0] - opts[1][0]) if len(opts) > 1 else 1e6 + top[0]
                cand = (regret, top[0], -r, r, top[1], top[2])
                if choice is None or cand[:3] > choice[:3]:
                    choice = cand
            dirty = set()
            if choice is None or choice[1] <= 0.0:
                break
            _, _, _, r, k, pos = choice
            routes[k] = routes[k][:pos] + [r] + routes[k][pos:]
            base[k] = ctx.value(k, routes[k])
            left.remove(r)
            dirty = {k}
            for rr in left:
                best.pop((rr, k), None)
        return routes, left


class GlobalALNS(Planner):
    """Adaptive large neighbourhood search over all reachable vehicles (Ropke-Pisinger style)."""

    name = "alns"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        c = cfg["control"]
        self.share = c["alns_removal_share"]
        self.qmax = c["alns_removal_max"]
        self.repair = RegretInsertion(cfg)

    @staticmethod
    def _score(ctx, routes):
        return sum(ctx.value(k, v).score for k, v in routes.items())

    def _remove(self, op, ctx, routes, q, rng):
        assigned = [(k, r) for k, v in routes.items() for r in v]
        if not assigned:
            return routes, []
        q = min(q, len(assigned))
        if op == 0:  # random
            chosen = rng.sample(assigned, q)
        elif op == 1:  # worst marginal contribution
            marg = []
            for k, r in assigned:
                full = ctx.value(k, routes[k]).score
                short = [x for x in routes[k] if x != r]
                rs = ctx.value(k, short)
                val = full - (rs.score if rs is not None else -1e9)
                marg.append((val + 1e-6 * rng.random(), k, r))
            marg.sort()
            chosen = [(k, r) for _, k, r in marg[:q]]
        else:  # related (Shaw): distance and window proximity to a random seed request
            R = ctx.ev.R
            T = ctx.ev.T
            k0, r0 = rng.choice(assigned)
            rel = sorted(assigned, key=lambda kr: (T[r0][kr[1]] + abs(R[r0]["a"] - R[kr[1]]["a"]) / 10.0, kr[1]))
            chosen = rel[:q]
        removed = [r for _, r in chosen]
        rs = set(removed)
        new = {k: [x for x in v if x not in rs] for k, v in routes.items()}
        return new, removed

    def solve(self, ctx: PlanningContext, init_routes: dict, pool, iterations: int, rng, max_time: float):
        t0 = time.perf_counter()
        cur, left = self.repair.insert(ctx, init_routes, pool, rng, max_time, t0)
        cur_score = self._score(ctx, cur)
        best, best_score, best_left = cur, cur_score, left
        weights = [1.0, 1.0, 1.0]
        scores = [0.0, 0.0, 0.0]
        uses = [0, 0, 0]
        temp = 0.05 * abs(cur_score) / math.log(2.0) + 1e-6
        cooling = 0.9
        done = 0
        timed_out = False
        n_assigned = sum(len(v) for v in cur.values()) + len(left)
        for it in range(iterations):
            if time.perf_counter() - t0 > max_time:
                timed_out = True
                break
            if n_assigned == 0:
                break
            op = rng.choices([0, 1, 2], weights=weights)[0]
            uses[op] += 1
            lo = max(2, int(self.share[0] * n_assigned))
            hi = max(lo, min(self.qmax, int(math.ceil(self.share[1] * n_assigned))))
            q = rng.randint(lo, hi)
            partial, removed = self._remove(op, ctx, cur, q, rng)
            cand, cand_left = self.repair.insert(ctx, partial, list(left) + removed, rng, max_time, t0)
            cand_score = self._score(ctx, cand)
            reward = 0.0
            if cand_score > best_score + 1e-9:
                best, best_score, best_left = cand, cand_score, cand_left
                reward = 33.0
            elif cand_score > cur_score + 1e-9:
                reward = 9.0
            accept = cand_score >= cur_score or rng.random() < math.exp(max(-700.0, (cand_score - cur_score) / temp))
            if accept:
                if reward == 0.0:
                    reward = 13.0
                cur, cur_score, left = cand, cand_score, cand_left
            scores[op] += reward
            if (it + 1) % 10 == 0:
                for i in range(3):
                    if uses[i]:
                        weights[i] = 0.9 * weights[i] + 0.1 * scores[i] / uses[i]
                    weights[i] = max(weights[i], 0.05)
                scores = [0.0, 0.0, 0.0]
                uses = [0, 0, 0]
            temp *= cooling
            done += 1
        return best, best_left, dict(iterations=done, timed_out=timed_out, evaluations=ctx.evaluations,
                                     score=best_score, seconds=time.perf_counter() - t0)


class LocalPlanner(Planner):
    """Single-vehicle planner: exact enumeration up to local_exact_max owned visits,
    otherwise relocate/2-opt local search; skip semantics drop unservable visits."""

    name = "local"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.exact_max = cfg["control"]["local_exact_max"]
        self.passes = cfg["control"]["local_ls_passes"]

    @staticmethod
    def _key(res, seq):
        return (res.score, -len(res.skipped), [-x for x in seq])

    def plan(self, ev: RouteEvaluator, snap: Snapshot, owned, hint, exact_max=None):
        owned = sorted(set(owned))
        if not owned:
            return ev.evaluate(snap, [], strict=False), 1
        evals = 0
        if len(owned) <= (self.exact_max if exact_max is None else exact_max):
            best, bkey = None, None
            for perm in itertools.permutations(owned):
                res = ev.evaluate(snap, perm, strict=False)
                evals += 1
                k = (res.score, -len(res.skipped))
                if bkey is None or k > bkey or (k == bkey and list(perm) < best[1]):
                    best, bkey = (res, list(perm)), k
            return best[0], evals
        seq = [x for x in hint if x in set(owned)]
        for r in owned:
            if r not in seq:
                # cheapest insertion by skip-mode score
                cands = []
                for pos in range(len(seq) + 1):
                    s2 = seq[:pos] + [r] + seq[pos:]
                    cands.append((ev.evaluate(snap, s2, strict=False).score, -pos, s2))
                    evals += 1
                seq = max(cands)[2]
        cur = ev.evaluate(snap, seq, strict=False)
        evals += 1
        for _ in range(self.passes):
            improved = False
            n = len(seq)
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    s2 = list(seq)
                    x = s2.pop(i)
                    s2.insert(j, x)
                    res = ev.evaluate(snap, s2, strict=False)
                    evals += 1
                    if res.score > cur.score + 1e-9:
                        seq, cur, improved = s2, res, True
                        break
                if improved:
                    break
            if not improved:
                for i in range(n - 1):
                    for j in range(i + 2, n + 1):
                        s2 = seq[:i] + seq[i:j][::-1] + seq[j:]
                        res = ev.evaluate(snap, s2, strict=False)
                        evals += 1
                        if res.score > cur.score + 1e-9:
                            seq, cur, improved = s2, res, True
                            break
                    if improved:
                        break
            if not improved:
                break
        return cur, evals

    @staticmethod
    def best_insertion(ev: RouteEvaluator, snap: Snapshot, served, r):
        """Marginal value of adding r to a strictly feasible sequence (for bids)."""
        base = ev.evaluate(snap, served, strict=True)
        if base is None:
            base = ev.evaluate(snap, served, strict=False)
            served = base.served
        best = None
        for pos in range(len(served) + 1):
            res = ev.evaluate(snap, served[:pos] + [r] + served[pos:], strict=True)
            if res is not None and (best is None or res.score > best[0]):
                best = (res.score, pos)
        if best is None:
            return None, None
        return best[0] - base.score, best[1]
