"""Communicating actors: dispatch centres (cloud/hub) and vehicle agents (edge).

Actors exchange only serialized messages. Class hierarchy:

  Actor
   |- CenterBase ---------- StaticCenter      (morning plan + cheapest insertion)
   |                   |--- GlobalCenter      (event-driven or periodic global ALNS)
   |                   '--- MarketCenter      (contract-net auctions; coordinator only)
   '- VehicleBase --------- ThinExecutor      (follows centre plan; minimal guards)
                       |--- FallbackExecutor  (thin; local planning after link loss)
                       '--- AgenticVehicle    (always-on local planning, twin, bids)
"""
from __future__ import annotations

import json
import time

from .common import SWAP, keyed_rng
from .planners import GlobalALNS, LocalPlanner, PlanningContext, RegretInsertion
from .protocol import HELD_BY_CENTER, TERMINAL, TokenRegistry, TokenWallet
from .routing import RouteEvaluator, Snapshot
from .network import CENTER, vehicle_id
from .thermal import CarrierObserver
from .view import PublicView, request_public


def encode(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def decode(payload: bytes):
    return json.loads(payload.decode())


class Actor:
    def __init__(self, name: str, sc_public: dict, cfg: dict, bound):
        self.name = name
        self.cfg = cfg
        self.view = PublicView(sc_public, cfg)
        self.bound = bound
        self.seed = sc_public["seed"]
        self.outbox = []
        self.logs = []
        self.compute_s = 0.0
        self.decision_times = []

    def send(self, dst: str, typ: str, body: dict, coalesce: str | None = None):
        self.outbox.append((dst, encode(dict(typ=typ, src=self.name, body=body)), coalesce))

    def drain(self):
        out, self.outbox = self.outbox, []
        logs, self.logs = self.logs, []
        return out, logs

    def evaluator(self, incident_active: bool) -> RouteEvaluator:
        return RouteEvaluator(self.view, self.cfg, self.bound, self.view.matrix(incident_active))


# ============================================================================ centres
class CenterBase(Actor):
    kind = "base"

    def __init__(self, sc_public: dict, cfg: dict, bound, morning_plan: dict):
        super().__init__(CENTER, sc_public, cfg, bound)
        self.K = len(sc_public["teams"])
        self.reg = TokenRegistry()
        self.views = {}
        self.last_seen = {}
        self.inc_desc = None
        self.inc_active = False
        self.inc_obs_t = -1
        self.morning = {int(k): v for k, v in morning_plan.items()}
        self.planned_end = {}
        self.triggers = set()
        self.reach_age = cfg["network"]["reachability_age_min"]
        self.dev = cfg["control"]["deviation_trigger_min"]
        self.plan_version = 0
        self.q_state = {}
        self.sent_ver = {}
        self.ref_end = {}
        self.alert_state = {}
        self.infra = {}

    # ------------------------------------------------------------ helpers
    def reachable(self, k: int, t: int) -> bool:
        return k in self.last_seen and t - self.last_seen[k] <= self.reach_age

    def alive(self, r: int, t: int) -> bool:
        return self.reg.holder.get(r) not in (None, TERMINAL) and self.view.R[r]["b"] >= t

    def snapshot(self, k: int, t: int) -> Snapshot:
        v = self.views[k]
        s = Snapshot.from_dict(v["snap"])
        if s.t_free < t:
            if s.node != self.view.hub_node(k):
                s.e_rem -= self.bound.rate_w * 60.0 * (t - s.t_free)
            s.t_free = t
        return s

    def held_sequence(self, k: int):
        v = self.views.get(k, {})
        seq = [r for r in v.get("plan", []) if r != SWAP and self.reg.holder.get(r) == k
               and r not in self.reg.pending and r != v.get("committed")]
        for r in sorted(r for r, h in self.reg.holder.items() if h == k):
            if r not in seq and r not in self.reg.pending and r != v.get("committed") and r not in v.get("done", []):
                seq.append(r)
        return seq

    def assign(self, t, r, k):
        ver = self.reg.assign(r, k, t)
        self.send(vehicle_id(k), "ASG", dict(r=r, v=ver, req=request_public(self.view.R[r])))

    def revoke(self, t, r, target):
        k, ver = self.reg.request_revoke(r, target, t)
        self.send(vehicle_id(k), "REV", dict(r=r, v=ver))

    def send_plan(self, t, k, plan, end):
        self.plan_version += 1
        self.planned_end[k] = end
        self.sent_ver[k] = self.plan_version
        self.ref_end[k] = None
        self.send(vehicle_id(k), "PLN", dict(ver=self.plan_version, plan=plan, end=end, t=t))

    # ------------------------------------------------------------ main step
    def step(self, t: int, inbox, new_requests, infra=None):
        c0 = time.process_time()
        self.infra = infra or {}
        for req in new_requests:
            self.view.reveal(req)
            self.reg.create(req["id"])
            self.on_new_request(t, req["id"])
        for payload in inbox:
            self.handle(t, decode(payload))
        if t == 0:
            self.apply_morning(t)
        self.decide(t)
        self.compute_s += time.process_time() - c0
        return self.drain()

    def apply_morning(self, t):
        for k in range(self.K):
            seq = self.morning.get(k, [])
            for r in seq:
                if r != SWAP:
                    self.assign(t, r, k)
            self.send_plan(t, k, seq, None)

    def handle(self, t, msg):
        typ, b = msg["typ"], msg["body"]
        k = int(msg["src"][1:]) if msg["src"] != CENTER else None
        if typ == "TEL":
            if b["t"] < self.views.get(k, {}).get("t", -1):
                return  # out-of-order older telemetry
            was = self.reachable(k, t) if k in self.last_seen else True
            self.views[k] = b
            self.last_seen[k] = t
            if not was:
                self.triggers.add(("reconnect", k))
            inc = b.get("inc")
            if inc and inc["obs_t"] > self.inc_obs_t:
                self.inc_obs_t = inc["obs_t"]
                if inc["desc"]:
                    self.inc_desc = inc["desc"]
                    self.view.set_incident(inc["desc"])
                if inc["active"] != self.inc_active:
                    self.inc_active = inc["active"]
                    self.triggers.add(("incident", k))
            if b.get("pver") == self.sent_ver.get(k) and b.get("end") is not None:
                ref = self.ref_end.get(k)
                if ref is None:
                    self.ref_end[k] = b["end"]
                elif abs(b["end"] - ref) > self.dev:
                    self.triggers.add(("deviation", k))
                    self.ref_end[k] = b["end"]
            if b.get("q") and not self.q_state.get(k, False):
                self.triggers.add(("quarantine", k))
            self.q_state[k] = bool(b.get("q"))
            if b.get("alert") and not self.alert_state.get(k, False):
                self.triggers.add(("thermal_alert", k))
            self.alert_state[k] = bool(b.get("alert"))
        elif typ == "DONE":
            if self.reg.on_done(b["r"], b["v"], k, t) and b["outcome"] == "missed":
                self.triggers.add(("missed", k))
        elif typ == "REL":
            ok, target = self.reg.on_released(b["r"], b["v"], k, t)
            if ok:
                self.on_released(t, b["r"], target, k)
        elif typ == "STA":
            self.reg.on_started(b["r"], b["v"], k)
        elif typ == "BID":
            self.on_bid(t, k, b)

    # ------------------------------------------------------------ hooks
    def on_new_request(self, t, r):
        pass

    def on_released(self, t, r, target, k):
        if target is not None and self.reachable(target, t) and self.alive(r, t):
            self.assign(t, r, target)
        else:
            self.triggers.add(("released", k))

    def on_bid(self, t, k, b):
        pass

    def decide(self, t):
        pass


class StaticCenter(CenterBase):
    """Current-practice reference: morning plan, dynamic requests by cheapest insertion."""

    kind = "static"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.queue = {}
        self.retry = self.cfg["control"]["periodic_interval_min"]

    def on_new_request(self, t, r):
        if t > 0:
            self.queue[r] = t

    def on_released(self, t, r, target, k):
        if self.alive(r, t):
            self.queue[r] = t

    def decide(self, t):
        if not self.queue or t == 0:
            return
        c0 = time.perf_counter()
        ev = self.evaluator(self.inc_active)
        reach = [k for k in range(self.K) if self.reachable(k, t)]
        for r in sorted(self.queue):
            since = self.queue[r]
            if not self.alive(r, t) or self.reg.holder.get(r) != HELD_BY_CENTER:
                del self.queue[r]
                continue
            if since != t and (t - since) % self.retry != 0:
                continue
            best = None
            for k in reach:
                snap = self.snapshot(k, t)
                seq = self.held_sequence(k)
                delta, pos = LocalPlanner.best_insertion(ev, snap, seq, r)
                if delta is not None and delta > 0 and (best is None or delta > best[0]):
                    base = ev.evaluate(snap, seq, strict=False)
                    new = base.served[:pos] + [r] + base.served[pos:]
                    best = (delta, k, new, snap)
            if best is not None:
                _, k, new, snap = best
                self.assign(t, r, k)
                res = ev.evaluate(snap, new, strict=False)
                self.send_plan(t, k, res.plan, res.end)
                self.views[k]["plan"] = res.plan
                del self.queue[r]
        self.decision_times.append(time.perf_counter() - c0)


class GlobalCenter(CenterBase):
    """Global ALNS re-optimisation of all reachable vehicles (event-driven or periodic)."""

    kind = "global"

    def __init__(self, sc_public, cfg, bound, morning_plan, trigger: str = "event"):
        super().__init__(sc_public, cfg, bound, morning_plan)
        self.trigger = trigger
        self.alns = GlobalALNS(cfg)
        self.iters = cfg["control"]["alns_iterations_event"]
        self.period = cfg["control"]["periodic_interval_min"]
        self.deadline = cfg["control"]["center_deadline_s"]

    def on_new_request(self, t, r):
        if t > 0:
            self.triggers.add(("request", r))

    def decide(self, t):
        if t == 0:
            self.triggers.clear()
            return
        if self.trigger == "periodic":
            run = t % self.period == 0
        else:
            run = bool(self.triggers)
        if not run:
            return
        reasons = sorted(str(x[0]) for x in self.triggers)
        self.triggers.clear()
        self.optimize(t, reasons)

    def optimize(self, t, reasons):
        c0 = time.perf_counter()
        reach = [k for k in range(self.K) if self.reachable(k, t)]
        if not reach:
            return
        snaps = {k: self.snapshot(k, t) for k in reach}
        init = {k: self.held_sequence(k) for k in reach}
        pool = sorted(r for r in self.reg.holder if self.reg.holder[r] == HELD_BY_CENTER
                      and r not in self.reg.pending and self.alive(r, t))
        ev = self.evaluator(self.inc_active)
        ctx = PlanningContext(ev, snaps)
        rng = keyed_rng(self.seed, "center", t)
        routes, left, diag = self.alns.solve(ctx, init, pool, self.iters, rng, self.deadline)
        planned = {r: k for k, seq in routes.items() for r in seq}
        for k in reach:
            for r in routes[k]:
                h = self.reg.holder[r]
                if h == HELD_BY_CENTER:
                    self.assign(t, r, k)
                elif h != k and r not in self.reg.pending:
                    self.revoke(t, r, k)
            for r in init[k]:
                if r not in planned and r not in self.reg.pending:
                    self.revoke(t, r, None)
            res = ctx.value(k, routes[k]) or ev.evaluate(snaps[k], routes[k], strict=False)
            self.send_plan(t, k, res.plan, res.end)
            self.views[k]["plan"] = res.plan
        dt = time.perf_counter() - c0
        self.decision_times.append(dt)
        self.logs.append(dict(ev="center_decision", t=t, reasons=reasons, reach=len(reach), pool=len(pool),
                              unassigned=len(left), iterations=diag["iterations"], timed_out=diag["timed_out"],
                              evaluations=diag["evaluations"], seconds=round(dt, 6)))


class MarketCoordination:
    """Contract-net logic (announce, bid, award) shared by the fog coordinator roles."""

    def market_init(self):
        self.window = self.cfg["control"]["auction_window_min"]
        self.reauction = self.cfg["control"]["reauction_interval_min"]
        self.auctions = {}
        self.next_try = {}
        self.cfp_id = 0

    def market_on_new(self, t, r):
        self.next_try[r] = t

    def market_on_bid(self, t, k, b):
        a = self.auctions.get(b["r"])
        if a and a["id"] == b["cfp"] and t <= a["deadline"]:
            a["bids"][k] = b["val"]

    def market_decide(self, t):
        c0 = time.perf_counter()
        acted = False
        for r in sorted(self.auctions):
            a = self.auctions[r]
            if t < a["deadline"]:
                continue
            del self.auctions[r]
            acted = True
            if self.reg.holder.get(r) != HELD_BY_CENTER or not self.alive(r, t):
                continue
            bids = sorted(((-v, k) for k, v in a["bids"].items() if v > 0))
            if bids:
                self.assign(t, r, bids[0][1])
                self.logs.append(dict(ev="award", t=t, r=r, team=bids[0][1], bids=len(bids)))
            else:
                self.next_try[r] = t + self.reauction
        for r in sorted(self.next_try):
            if self.next_try[r] > t:
                continue
            del self.next_try[r]
            if self.reg.holder.get(r) != HELD_BY_CENTER or not self.alive(r, t):
                continue
            reach = [k for k in range(self.K) if self.reachable(k, t)]
            if not reach:
                self.next_try[r] = t + self.reauction
                continue
            self.cfp_id += 1
            self.auctions[r] = dict(id=self.cfp_id, deadline=t + self.window, bids={})
            acted = True
            for k in reach:
                self.send(vehicle_id(k), "CFP", dict(r=r, cfp=self.cfp_id, deadline=t + self.window,
                                                      req=request_public(self.view.R[r])))
        if acted:
            self.decision_times.append(time.perf_counter() - c0)


class MarketCenter(CenterBase, MarketCoordination):
    """Contract-net coordinator at the fog node: announces, collects bids, awards tokens. No routing."""

    kind = "market"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.market_init()

    def on_new_request(self, t, r):
        if t > 0:
            self.market_on_new(t, r)

    def on_released(self, t, r, target, k):
        if self.alive(r, t):
            self.market_on_new(t, r)

    def on_bid(self, t, k, b):
        self.market_on_bid(t, k, b)

    def decide(self, t):
        if t == 0:
            for k in range(self.K):
                self.send(vehicle_id(k), "MODE", {"global": False})
        self.market_decide(t)


class HybridCenter(GlobalCenter, MarketCoordination):
    """Fog coordinator with a cloud optimiser: global ALNS while the cloud is reachable,
    contract-net auctions among agents while it is not (graceful degradation)."""

    kind = "hybrid"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.market_init()
        self.cloud_was_up = True

    def cloud_up(self) -> bool:
        return self.infra.get("cloud_up", True)

    def on_new_request(self, t, r):
        if t == 0:
            return
        if self.cloud_up():
            self.triggers.add(("request", r))
        else:
            self.market_on_new(t, r)

    def on_released(self, t, r, target, k):
        if self.cloud_up():
            super().on_released(t, r, target, k)
        elif self.alive(r, t):
            self.market_on_new(t, r)

    def on_bid(self, t, k, b):
        self.market_on_bid(t, k, b)

    def decide(self, t):
        up = self.cloud_up()
        if not up:
            if self.cloud_was_up:
                self.logs.append(dict(ev="degrade_to_market", t=t))
                for k in range(self.K):
                    self.send(vehicle_id(k), "MODE", {"global": False})
            self.cloud_was_up = False
            self.market_decide(t)
            return
        if not self.cloud_was_up:
            self.logs.append(dict(ev="restore_global", t=t))
            for k in range(self.K):
                self.send(vehicle_id(k), "MODE", {"global": True})
            self.auctions.clear()
            self.next_try.clear()
            self.triggers.add(("cloud_restored", -1))
            self.cloud_was_up = True
        super().decide(t)


# ============================================================================ vehicles
class GreedyFallbackCenter(GlobalCenter):
    """Cloud ALNS in normal operation; fog insertion only while cloud is down.

    The registry and current plans persist across transitions. No existing
    assignment is revoked merely because the mode changes. On recovery the
    event-driven global optimiser repairs the full reachable state.
    """

    kind = "greedy_fallback"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.queue = {}
        self.retry = self.cfg["control"]["reauction_interval_min"]
        self.cloud_was_up = True

    def cloud_up(self):
        return self.infra.get("cloud_up", True)

    def on_new_request(self, t, r):
        if t > 0:
            if self.cloud_up():
                super().on_new_request(t, r)
            else:
                self.queue[r] = t

    def on_released(self, t, r, target, k):
        if self.cloud_up():
            super().on_released(t, r, target, k)
        elif self.alive(r, t):
            self.queue[r] = t

    def decide(self, t):
        up = self.cloud_up()
        if not up:
            if self.cloud_was_up:
                self.logs.append(dict(ev="degrade_to_greedy", t=t))
                self.queue.update({r: t for r, h in self.reg.holder.items()
                                   if h == HELD_BY_CENTER and self.alive(r, t)})
            self.cloud_was_up = False
            StaticCenter.decide(self, t)
            return
        if not self.cloud_was_up:
            self.logs.append(dict(ev="restore_global", t=t))
            self.queue.clear()
            self.triggers.add(("cloud_restored", -1))
            self.cloud_was_up = True
        super().decide(t)


class VehicleBase(Actor):
    kind = "base"

    def __init__(self, k: int, sc_public: dict, cfg: dict, bound):
        super().__init__(vehicle_id(k), sc_public, cfg, bound)
        self.k = k
        self.hub = self.view.hub_node(k)
        self.wallet = TokenWallet()
        self.plan = []
        self.plan_ver = -1
        self.committed = None
        self.activity = None     # (kind, target, t_start, expected_end)
        self.observer = CarrierObserver(bound)
        self.inc_active = False
        self.inc_obs_t = -1
        self.done = []
        self.limit = self.view.shift + self.view.max_ot
        self.link_down_since = None
        self.obs = None
        self.local = LocalPlanner(cfg)
        self.dirty = True
        self.last_plan_t = -10 ** 6
        self.hint = []
        self.alert = False
        self.alert_theta = cfg["carrier"]["alert_theta_c"]
        self.local_event = False
        self.thermal_event = False

    # ------------------------------------------------------------ perception
    def perceive(self, t, obs):
        self.obs = obs
        for e in obs["events"]:
            if e[0] == "arrive":
                _, to, frm, t_dep, dur, seen, desc = e
                if seen and t_dep > self.inc_obs_t:
                    self.inc_obs_t = t_dep
                    self.view.set_incident(desc)
                    new_state = seen == "active"
                    if new_state != self.inc_active:
                        self.inc_active = new_state
                        self.dirty = True
                        self.local_event = True
                self.activity = None
            elif e[0] == "service_end":
                _, r, outcome = e
                v = self.wallet.finish(r, t)
                self.done.append(r)
                self.send(CENTER, "DONE", dict(r=r, v=v, outcome=outcome, t=t))
                self.committed = None
                self.activity = None
                self.dirty = True
            elif e[0] == "swap_end":
                self.observer.reset()
                self.alert = False
                self.committed = None
                self.activity = None
                self.dirty = True
            elif e[0] == "wait_end":
                self.activity = None
        lid = bool(self.activity and self.activity[0] == "service_cold" and self.activity[2] == t - 1)
        if obs["phase"] != "swap" and not (obs["node"] == self.hub and obs["phase"] == "idle"):
            self.observer.update(obs["theta"], lid)
            if obs["theta"] >= self.alert_theta and not self.alert:
                # coolant exhaustion detected from the local sensor: the latent estimate is invalid
                self.alert = True
                self.observer.e_est = min(self.observer.e_est, 0.0)
                self.dirty = True
                self.local_event = True
                self.thermal_event = True
                self.logs.append(dict(ev="thermal_alert", t=t, team=self.k, theta=obs["theta"]))
            if self.alert:
                self.observer.e_est = min(self.observer.e_est, 0.0)
        link = obs.get("link", True)
        if link:
            self.link_down_since = None
        elif self.link_down_since is None:
            self.link_down_since = t

    # ------------------------------------------------------------ messages
    def handle(self, t, msg):
        typ, b = msg["typ"], msg["body"]
        if typ == "ASG":
            self.view.reveal(b["req"])
            if self.wallet.on_assign(b["r"], b["v"], t):
                self.dirty = True
                self.on_assigned(t, b["r"])
        elif typ == "REV":
            reply = self.wallet.on_revoke(b["r"], b["v"], t, committed=(b["r"] == self.committed))
            if reply == "REL":
                self.plan = [x for x in self.plan if x != b["r"]]
                self.dirty = True
                self.send(CENTER, "REL", dict(r=b["r"], v=b["v"]))
            elif reply == "STA":
                self.send(CENTER, "STA", dict(r=b["r"], v=b["v"]))
            elif b["r"] in self.done:
                pass
        elif typ == "PLN":
            if b["ver"] > self.plan_ver:
                self.plan_ver = b["ver"]
                self.on_plan(t, b["plan"])
        elif typ == "CFP":
            self.view.reveal(b["req"])
            self.on_cfp(t, b)

    def on_assigned(self, t, r):
        if r not in self.plan:
            self.plan.append(r)

    def on_plan(self, t, plan):
        self.plan = list(plan)

    def on_cfp(self, t, b):
        pass

    # ------------------------------------------------------------ state
    def snapshot(self, t) -> Snapshot:
        o = self.obs
        e = self.observer.e_est
        doses, q = o["doses"], o["quarantined"]
        rate = self.bound.rate_w * 60.0
        R = self.view.R
        T = self.view.matrix(self.inc_active)
        if o["phase"] == "idle" and self.committed is None:
            return Snapshot(self.k, o["node"], t, e, doses, q)
        if self.committed == SWAP or o["phase"] == "swap":
            if o["phase"] == "swap":
                end = max(t + 1, o["until"])
            else:
                eta = self.activity[3] if self.activity else t + T[o["node"]][self.hub]
                end = max(t + 1, eta) + self.cfg["carrier"]["swap_min"]
            return Snapshot(self.k, self.hub, end, self.bound.full_j, self.cfg["carrier"]["capacity_doses"], False)
        if self.committed is None:
            # travelling home
            eta = self.activity[3] if self.activity else t
            return Snapshot(self.k, self.hub, max(t + 1, eta), e, doses, q)
        r = self.committed
        req = R[r]
        if o["phase"] == "service":
            fin = max(t + 1, self.activity[2] + req["service_expected"])
            return Snapshot(self.k, r, fin, e - rate * (fin - t), doses, q)
        if o["phase"] == "travel":
            arr = max(t + 1, self.activity[3])
        else:
            arr = t
        st = max(arr, req["a"])
        fin = st + req["service_expected"]
        e2 = e - rate * (fin - t) - (self.bound.impulse_j if req["cold"] else 0.0)
        d2 = doses - (req["doses"] if req["cold"] else 0)
        return Snapshot(self.k, r, fin, e2, d2, q)

    def telemetry(self, t):
        snap = self.snapshot(t)
        ev = self.evaluator(self.inc_active)
        seq = [x for x in self.plan if x == SWAP or self.wallet.holds(x)]
        res = ev.evaluate(snap, seq, strict=False)
        inc = None
        if self.inc_obs_t >= 0:
            inc = dict(obs_t=self.inc_obs_t, active=self.inc_active, desc=self.view.incident_desc)
        body = dict(k=self.k, t=t, snap=snap.as_dict(), plan=self.plan, committed=self.committed, end=res.end,
                    pver=self.plan_ver,
                    held=sorted(self.wallet.held), q=self.obs["quarantined"], alert=self.alert, inc=inc,
                    done=self.done[-20:])
        self.send(CENTER, "TEL", body, coalesce="TEL")

    # ------------------------------------------------------------ acting
    def expected_travel(self, frm, to):
        return self.view.matrix(self.inc_active)[frm][to]

    def release(self, t, r, reason):
        if not self.wallet.holds(r):
            return
        v = self.wallet.release(r, t)
        self.plan = [x for x in self.plan if x != r]
        self.send(CENTER, "REL", dict(r=r, v=v))
        self.logs.append(dict(ev="release", t=t, team=self.k, r=r, reason=reason))

    def missed(self, t, r):
        if not self.wallet.holds(r):
            return
        v = self.wallet.finish(r, t)
        self.done.append(r)
        self.plan = [x for x in self.plan if x != r]
        self.send(CENTER, "DONE", dict(r=r, v=v, outcome="missed", t=t))
        self.logs.append(dict(ev="missed_by_vehicle", t=t, team=self.k, r=r))

    def guard_ok(self, t, r) -> bool:
        """Minimal execution guard shared by all vehicles."""
        o = self.obs
        req = self.view.R[r]
        arr = t + self.expected_travel(o["node"], r)
        st = max(arr, req["a"])
        if st > req["b"]:
            return False
        if st + req["service_expected"] + self.expected_travel(r, self.hub) > self.limit:
            return False
        return True

    def pre_decision(self, t):
        pass

    def act(self, t):
        o = self.obs
        if o["phase"] != "idle":
            return None
        node = o["node"]
        R = self.view.R
        # committed target reached
        if self.committed is not None and self.committed != SWAP:
            r = self.committed
            req = R[r]
            if node == r:
                if not self.wallet.holds(r):
                    self.committed = None
                elif t > req["b"]:
                    self.committed = None
                    self.missed(t, r)
                elif req["cold"] and (o["quarantined"] or o["doses"] < req["doses"]):
                    self.committed = None
                    self.release(t, r, "carrier_unavailable")
                elif t < req["a"]:
                    self.activity = ("wait", r, t, req["a"])
                    return ("wait", req["a"])
                else:
                    kind = "service_cold" if req["cold"] else "service"
                    self.activity = (kind, r, t, t + req["service_expected"])
                    return ("serve", r, self.wallet.held[r])
            else:
                self.activity = ("travel", r, t, t + self.expected_travel(node, r))
                return ("goto", r)
        if self.committed == SWAP:
            if node == self.hub:
                self.activity = ("swap", SWAP, t, t + self.cfg["carrier"]["swap_min"])
                return ("swap",)
            self.activity = ("travel", self.hub, t, t + self.expected_travel(node, self.hub))
            return ("goto", self.hub)
        self.pre_decision(t)
        while self.plan:
            x = self.plan[0]
            if x == SWAP:
                self.plan.pop(0)
                self.committed = SWAP
                return self.act(t)
            if not self.wallet.holds(x):
                self.plan.pop(0)  # token not (yet) held; re-appended if it arrives later
                continue
            if not self.guard_ok(t, x):
                self.plan.pop(0)
                if R[x]["b"] < t + 1:
                    self.missed(t, x)
                else:
                    self.release(t, x, "guard")
                continue
            if R[x]["cold"] and (o["quarantined"] or o["doses"] < R[x]["doses"]):
                self.plan.pop(0)
                self.release(t, x, "carrier_unavailable")
                continue
            self.plan.pop(0)
            self.committed = x
            return self.act(t)
        extra = sorted(r for r in self.wallet.held if r not in self.plan and r != self.committed)
        if extra:
            self.plan.extend(extra)
            if any(self.wallet.holds(y) for y in self.plan):
                return self.act_after_extra(t)
        if node != self.hub:
            self.activity = ("travel", self.hub, t, t + self.expected_travel(node, self.hub))
            return ("goto", self.hub)
        return None

    def act_after_extra(self, t):
        return self.act(t)

    def step(self, t, inbox, obs):
        c0 = time.process_time()
        self.perceive(t, obs)
        for payload in inbox:
            self.handle(t, decode(payload))
        action = self.act(t)
        self.telemetry(t)
        self.compute_s += time.process_time() - c0
        out, logs = self.drain()
        return out, logs, action


class ThinExecutor(VehicleBase):
    kind = "thin"


class AgenticVehicle(VehicleBase):
    """Bounded autonomy. With a fresh global plan and a working link the agent defers to the
    centre, but re-plans locally as soon as local evidence invalidates that plan (thermal alert,
    perceived incident, locally infeasible visit). Without a global planner, or while
    disconnected, it plans autonomously, bids in auctions and releases what others may serve."""

    kind = "agentic"

    eager = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.replan_every = self.cfg["control"]["agent_periodic_replan_min"]
        self.global_mode = True
        self.autonomy_after = 0 if self.eager else self.cfg["control"]["agent_autonomy_after_min"]

    def handle(self, t, msg):
        if msg["typ"] == "MODE":
            self.global_mode = bool(msg["body"]["global"])
            self.dirty = True
            return
        super().handle(t, msg)

    def autonomous(self, t) -> bool:
        if not self.global_mode:
            return True
        return self.link_down_since is not None and t - self.link_down_since >= self.autonomy_after

    def on_plan(self, t, plan):
        if self.obs is not None and self.autonomous(t):
            self.hint = [x for x in plan if x != SWAP]
            self.dirty = True
        else:
            self.plan = list(plan)
            self.hint = []

    def on_assigned(self, t, r):
        if self.obs is None or not self.autonomous(t):
            if r not in self.plan:
                self.plan.append(r)
            return
        seq = [x for x in self.plan if x != SWAP and self.wallet.holds(x) and x != r]
        ev = self.evaluator(self.inc_active)
        snap = self.snapshot(t)
        val, pos = LocalPlanner.best_insertion(ev, snap, seq, r)
        if pos is not None:
            base = ev.evaluate(snap, seq, strict=False).served
            seq = base[:pos] + [r] + base[pos:]
        else:
            seq = seq + [r]
        self.plan = seq
        self.dirty = True

    def plan_invalid(self, t) -> bool:
        """Imminent failure only: the next held visit, or an urgent visit, is no longer servable."""
        snap = self.snapshot(t)
        ev = self.evaluator(self.inc_active)
        seq = [x for x in self.plan if x == SWAP or self.wallet.holds(x)]
        res = ev.evaluate(snap, seq, strict=False)
        if not res.skipped:
            return False
        first = next((x for x in seq if x != SWAP), None)
        return first in res.skipped or any(self.view.R[x]["urgent"] for x in res.skipped)

    def local_replan(self, t, reason):
        c0 = time.perf_counter()
        snap = self.snapshot(t)
        ev = self.evaluator(self.inc_active)
        owned = [r for r in self.wallet.held if r != self.committed]
        hint = self.hint if self.hint else [x for x in self.plan if x != SWAP]
        self.hint = []
        res, evals = self.local.plan(ev, snap, owned, hint)
        served = list(res.served)
        for r in list(res.skipped):
            val, pos = LocalPlanner.best_insertion(ev, snap, served, r)
            evals += len(served) + 1
            if pos is not None:
                served = served[:pos] + [r] + served[pos:]
        res = ev.evaluate(snap, served, strict=False)
        tail = []
        T = self.view.matrix(self.inc_active)
        link = self.obs.get("link", True)
        for r in sorted(set(owned) - set(res.served)):
            if not self.wallet.holds(r):
                continue
            req = self.view.R[r]
            if req["b"] < t + 1:
                self.missed(t, r)
            elif self.certainly_infeasible(snap, r, T):
                self.release(t, r, "local_infeasible")
            elif link and self.may_release():
                self.release(t, r, "dropped")
            else:
                tail.append(r)
        self.plan = list(res.plan) + tail
        self.dirty = False
        self.local_event = False
        self.thermal_event = False
        self.last_plan_t = t
        dt = time.perf_counter() - c0
        self.decision_times.append(dt)
        self.logs.append(dict(ev="agent_decision", t=t, team=self.k, reason=reason, owned=len(owned),
                              evaluations=evals, seconds=round(dt, 6)))

    def may_release(self) -> bool:
        """Release optimisation-dropped visits only to a market coordinator; a global planner
        reallocates them itself from the reported plan."""
        return not self.global_mode

    def certainly_infeasible(self, snap, r, T) -> bool:
        """Optimistic test: infeasible even with a direct trip and the shortest plausible service."""
        req = self.view.R[r]
        slack = 10
        st = max(snap.t_free + T[snap.node][r], req["a"])
        if st > req["b"] + slack:
            return True
        if st + 0.7 * req["service_expected"] + T[r][self.hub] > self.limit + slack:
            return True
        if req["skill"] == 1 and not self.view.teams[self.k]["advanced"]:
            return True
        return False

    def pre_decision(self, t):
        if self.autonomous(t):
            if self.dirty or self.local_event or t - self.last_plan_t >= self.replan_every:
                self.local_replan(t, "autonomous")
        elif self.eager:
            if self.local_event or self.plan_invalid(t):
                self.local_replan(t, "invalidated")
        elif self.thermal_event:
            self.local_replan(t, "thermal_reflex")

    def on_cfp(self, t, b):
        """Bid = value of the best local plan with the request minus the current plan value
        (displacement of lower-value visits allowed)."""
        c0 = time.perf_counter()
        r = b["r"]
        ev = self.evaluator(self.inc_active)
        snap = self.snapshot(t)
        seq = [x for x in self.plan if x != SWAP and self.wallet.holds(x)]
        cur = ev.evaluate(snap, seq, strict=False)
        owned = sorted(set(x for x in self.wallet.held if x != self.committed) | {r})
        new, _ = self.local.plan(ev, snap, owned, cur.served + [r], exact_max=5)
        if r in new.served and new.score - cur.score > 0:
            self.send(CENTER, "BID", dict(r=r, cfp=b["cfp"], val=round(new.score - cur.score, 6)))
        self.decision_times.append(time.perf_counter() - c0)


class EagerAgenticVehicle(AgenticVehicle):
    """Ablation: overrides a fresh global plan on any local invalidation and becomes
    autonomous immediately after link loss."""

    kind = "agentic_eager"
    eager = True


class FallbackExecutor(AgenticVehicle):
    """Thin while connected; local planning only after the link has been down long enough."""

    kind = "fallback"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.after = self.cfg["network"]["fallback_after_min"]

    def autonomous(self, t) -> bool:
        return self.link_down_since is not None and t - self.link_down_since >= self.after

    def may_release(self) -> bool:
        return False

    def pre_decision(self, t):
        if self.autonomous(t) and (self.dirty or self.local_event or t - self.last_plan_t >= self.replan_every):
            self.local_replan(t, "fallback")

    def on_plan(self, t, plan):
        self.plan = list(plan)
        self.hint = []

    def on_assigned(self, t, r):
        if r not in self.plan:
            self.plan.append(r)
        self.dirty = True

    def on_cfp(self, t, b):
        pass
