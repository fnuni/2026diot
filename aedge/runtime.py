"""Lock-step runtimes (polymorphic): in-process actors or one OS process per actor.

Both backends exchange the same serialized messages in the same order, so the
trajectory hash must be identical; the multiprocess backend additionally
measures per-process CPU time and peak memory.
"""
from __future__ import annotations

import multiprocessing as mp
import resource
import time

from .actors import (AgenticVehicle, EagerAgenticVehicle, FallbackExecutor, GlobalCenter, GreedyFallbackCenter, HybridCenter, MarketCenter, StaticCenter,
                     ThinExecutor)
from .common import sha, SWAP
from .network import CENTER, Network, vehicle_id
from .planners import GlobalALNS, PlanningContext
from .routing import RouteEvaluator, Snapshot
from .scenario import ScenarioModel
from .thermal import ThermalBound, calibrate_class
from .climate import AmbientModel
from .view import PublicView, public_scenario, request_public
from .world import World
from .common import keyed_rng

# method -> (centre class, vehicle class, centre kwargs, centre endpoint host)
METHODS = {
    "STATIC": (StaticCenter, ThinExecutor, {}, "cloud"),
    "GREEDY_FOG": (StaticCenter, ThinExecutor, {}, "fog"),
    "GREEDY_FB": (GreedyFallbackCenter, AgenticVehicle, {"trigger": "event"}, "fog"),
    "PERIODIC": (GlobalCenter, ThinExecutor, {"trigger": "periodic"}, "cloud"),
    "CENTRAL": (GlobalCenter, ThinExecutor, {"trigger": "event"}, "cloud"),
    "CENTRAL_FB": (GlobalCenter, FallbackExecutor, {"trigger": "event"}, "cloud"),
    "CENTRAL_FOG": (GlobalCenter, ThinExecutor, {"trigger": "event"}, "fog"),
    "HYBRID": (HybridCenter, AgenticVehicle, {"trigger": "event"}, "fog"),
    "HYBRID_EAGER": (HybridCenter, EagerAgenticVehicle, {"trigger": "event"}, "fog"),
    "EDGE_MARKET": (MarketCenter, AgenticVehicle, {}, "fog"),
}

_CLASS_CACHE = {}


def carrier_and_bound(sc: dict, cfg: dict):
    name = sc["thermal"]["carrier"]
    if name not in _CLASS_CACHE:
        _CLASS_CACHE[name] = calibrate_class(cfg, name)
    cls = _CLASS_CACHE[name]
    amb = AmbientModel(cfg, sc["thermal"]["day"], sc["horizon"])
    return cls, ThermalBound(cls, cfg, amb.envelope())


def morning_plan(sc: dict, cfg: dict) -> dict:
    """Common initial plan (identical for all methods): global ALNS on static requests."""
    cls, bound = carrier_and_bound(sc, cfg)
    pub = public_scenario(sc)
    view = PublicView(pub, cfg)
    ev = RouteEvaluator(view, cfg, bound, view.exp0)
    snaps = {k: Snapshot(k, view.hub_node(k), 0, bound.full_j, cfg["carrier"]["capacity_doses"], False)
             for k in range(view.K)}
    ctx = PlanningContext(ev, snaps)
    pool = [r["id"] for r in sc["requests"] if r["static"]]
    rng = keyed_rng(sc["seed"], "morning", sc["static_sha256"])
    routes, left, diag = GlobalALNS(cfg).solve(ctx, {k: [] for k in snaps}, pool,
                                               cfg["control"]["alns_iterations_morning"], rng, 1e9)
    plans = {}
    for k, seq in routes.items():
        plans[k] = ctx.value(k, seq).plan
    return dict(plans={str(k): v for k, v in plans.items()}, unassigned=left, score=diag["score"],
                iterations=diag["iterations"])


class Runtime:
    """Base lock-step orchestrator; subclasses provide actor hosting."""

    backend = "base"

    def __init__(self, sc: dict, cfg: dict, method: str, morning: dict):
        self.sc, self.cfg, self.method = sc, cfg, method
        self.truth = ScenarioModel(sc, cfg)
        self.cls, self.bound = carrier_and_bound(sc, cfg)
        self.pub = public_scenario(sc)
        self.morning = {int(k): v for k, v in morning["plans"].items()}
        self.log = []
        self.world = World(self.truth, cfg, self.cls, self.log)
        self.host = METHODS[method][3]
        self.net = Network(sc, self.host)
        self.metrics_extra = {}

    # hooks
    def start(self):
        raise NotImplementedError

    def vehicle_step(self, k, t, inbox, obs):
        raise NotImplementedError

    def center_step(self, t, inbox, new):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def run(self):
        self.log.append(dict(ev="run_config", t=0, method=self.method, backend=self.backend, label=self.sc["label"],
                             scenario_sha256=sha({k: v for k, v in self.sc.items() if k != "links"}),
                             links_sha256=sha(self.sc["links"])))
        self.start()
        K = self.truth.K
        inbox_v = {k: [] for k in range(K)}
        buffered = []
        releases = {}
        for r in self.sc["requests"]:
            releases.setdefault(r["release"], []).append(request_public(r))
        for t in range(self.truth.horizon + 1):
            for k in range(K):
                obs = self.world.observe(t, k)
                obs["link"] = self.net.links[k].is_up(t) and self.net.center_reachable(t)
                out, logs, action = self.vehicle_step(k, t, inbox_v[k], obs)
                for dst, payload, co in out:
                    self.net.send(t, vehicle_id(k), dst, payload, co)
                for e in logs:
                    self.log.append(e)
                if action is not None:
                    self.log.append(dict(ev="command", t=t, team=k, action=list(action)))
                self.world.command(t, k, action)
            up = self.net.deliver(t, "up")
            buffered.extend(releases.get(t, []))
            cloud_up = not (self.net.cloud_outage and self.net.cloud_outage[0] <= t < self.net.cloud_outage[1])
            if self.net.center_reachable(t):
                out, logs = self.center_step(t, up.get(CENTER, []), buffered, dict(cloud_up=cloud_up))
                buffered = []
                for dst, payload, co in out:
                    self.net.send(t, CENTER, dst, payload, co)
                for e in logs:
                    self.log.append(e)
            down = self.net.deliver(t, "down")
            inbox_v = {k: down.get(vehicle_id(k), []) for k in range(K)}
            self.world.advance(t)
        self.world.finish(self.truth.horizon + 1)
        self.stop()
        self.log.append(dict(ev="network_stats", t=self.truth.horizon + 1, pending=self.net.pending(), **self.net.stats))
        return self.log


class InProcessRuntime(Runtime):
    backend = "inprocess"

    def start(self):
        Ccls, Vcls, kw, _ = METHODS[self.method]
        self.center = Ccls(self.pub, self.cfg, self.bound, self.morning, **kw)
        self.vehicles = [Vcls(k, self.pub, self.cfg, self.bound) for k in range(self.truth.K)]

    def vehicle_step(self, k, t, inbox, obs):
        return self.vehicles[k].step(t, inbox, obs)

    def center_step(self, t, inbox, new, infra):
        return self.center.step(t, inbox, new, infra)

    def stop(self):
        c = self.center
        self.log.append(dict(ev="actor_summary", t=self.truth.horizon + 1, actor=CENTER, cpu_s=round(c.compute_s, 6),
                             decisions=[round(x, 6) for x in c.decision_times], token_log=c.reg.log))
        for v in self.vehicles:
            self.log.append(dict(ev="actor_summary", t=self.truth.horizon + 1, actor=v.name, cpu_s=round(v.compute_s, 6),
                                 decisions=[round(x, 6) for x in v.decision_times],
                                 wallet=[list(e) for e in v.wallet.events]))


def _actor_process(conn, role, args):
    Ccls_name, Vcls_name, kw, pub, cfg, morning, k = args
    from . import actors as A
    from .runtime import carrier_and_bound
    sc_like = dict(pub)
    cls, bound = carrier_and_bound(dict(thermal=pub["thermal"], horizon=pub["horizon"]), cfg)
    if role == "center":
        actor = getattr(A, Ccls_name)(pub, cfg, bound, morning, **kw)
    else:
        actor = getattr(A, Vcls_name)(k, pub, cfg, bound)
    while True:
        msg = conn.recv()
        if msg[0] == "stop":
            summary = dict(cpu_s=actor.compute_s, decisions=actor.decision_times,
                           peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if role == "center":
                summary["token_log"] = actor.reg.log
            else:
                summary["wallet"] = [list(e) for e in actor.wallet.events]
            conn.send(summary)
            break
        if role == "center":
            _, t, inbox, new, infra = msg
            c0 = time.process_time()
            res = actor.step(t, inbox, new, infra)
            conn.send((res, time.process_time() - c0))
        else:
            _, t, inbox, obs = msg
            c0 = time.process_time()
            res = actor.step(t, inbox, obs)
            conn.send((res, time.process_time() - c0))
    conn.close()


class MultiprocessRuntime(Runtime):
    """One spawned OS process per actor; the orchestrator hosts only world and network."""

    backend = "multiprocess"

    def start(self):
        Ccls, Vcls, kw, _ = METHODS[self.method]
        ctx = mp.get_context("spawn")
        self.pipes, self.procs = [], []
        roles = [("center", None)] + [("vehicle", k) for k in range(self.truth.K)]
        for role, k in roles:
            a, b = ctx.Pipe()
            args = (Ccls.__name__, Vcls.__name__, kw, self.pub, self.cfg, {str(x): y for x, y in self.morning.items()}, k)
            p = ctx.Process(target=_actor_process, args=(b, role, args), daemon=True)
            p.start()
            b.close()
            self.pipes.append(a)
            self.procs.append(p)
        self.ipc_bytes = 0
        self.ipc_msgs = 0

    def _call(self, idx, msg):
        self.pipes[idx].send(msg)
        self.ipc_msgs += 2
        res, cpu = self.pipes[idx].recv()
        return res

    def vehicle_step(self, k, t, inbox, obs):
        out, logs, action = self._call(1 + k, ("step", t, inbox, obs))
        return out, logs, (tuple(action) if action is not None else None)

    def center_step(self, t, inbox, new, infra):
        return self._call(0, ("step", t, inbox, new, infra))

    def stop(self):
        for i, p in enumerate(self.pipes):
            p.send(("stop",))
            s = p.recv()
            actor = CENTER if i == 0 else vehicle_id(i - 1)
            e = dict(ev="actor_summary", t=self.truth.horizon + 1, actor=actor, cpu_s=round(s["cpu_s"], 6),
                     decisions=[round(x, 6) for x in s["decisions"]], peak_rss=s["peak_rss"])
            if "token_log" in s:
                e["token_log"] = s["token_log"]
            if "wallet" in s:
                e["wallet"] = s["wallet"]
            self.log.append(e)
        for p in self.procs:
            p.join(10)


BACKENDS = {"inprocess": InProcessRuntime, "multiprocess": MultiprocessRuntime}
