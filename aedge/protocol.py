"""Ownership-token protocol guaranteeing at-most-once service (Theorem 3).

Centre side (TokenRegistry): a request token (r, v) is issued only while the
centre holds r; the centre regains r only on RELEASED(r, v) for the current
version, after which the version is incremented. Vehicle side (TokenWallet):
a vehicle accepts ASSIGN(r, v) only if v exceeds every version of r it has
seen, drops the token before replying RELEASED, and serves r only while
holding it. No consensus or shared registry is required.
"""
from __future__ import annotations

HELD_BY_CENTER = "C"
TERMINAL = "T"


class TokenRegistry:
    def __init__(self):
        self.holder = {}
        self.version = {}
        self.pending = {}   # r -> target team (or None) awaiting RELEASED
        self.log = []

    def create(self, r: int):
        self.holder[r] = HELD_BY_CENTER
        self.version[r] = 0

    def can_assign(self, r: int) -> bool:
        return self.holder.get(r) == HELD_BY_CENTER

    def assign(self, r: int, k: int, t: int):
        assert self.holder[r] == HELD_BY_CENTER
        self.holder[r] = k
        self.log.append(("assign", t, r, self.version[r], k))
        return self.version[r]

    def request_revoke(self, r: int, target, t: int):
        k = self.holder[r]
        assert isinstance(k, int)
        self.pending[r] = target
        self.log.append(("revoke", t, r, self.version[r], k))
        return k, self.version[r]

    def on_released(self, r: int, v: int, k: int, t: int):
        """Returns (accepted, pending_target)."""
        if self.holder.get(r) == k and self.version.get(r) == v:
            self.holder[r] = HELD_BY_CENTER
            self.version[r] = v + 1
            self.log.append(("released", t, r, v, k))
            return True, self.pending.pop(r, None)
        return False, None

    def on_started(self, r: int, v: int, k: int):
        if self.holder.get(r) == k and self.version.get(r) == v:
            self.pending.pop(r, None)

    def on_done(self, r: int, v: int, k: int, t: int):
        if self.holder.get(r) == k and self.version.get(r) == v:
            self.holder[r] = TERMINAL
            self.pending.pop(r, None)
            self.log.append(("done", t, r, v, k))
            return True
        return False


class TokenWallet:
    def __init__(self):
        self.held = {}
        self.known = {}
        self.events = []

    def on_assign(self, r: int, v: int, t: int) -> bool:
        if v > self.known.get(r, -1):
            self.known[r] = v
            self.held[r] = v
            self.events.append(("acquire", t, r, v))
            return True
        return False

    def on_revoke(self, r: int, v: int, t: int, committed: bool):
        """Returns reply type: 'REL', 'STA' or None (stale)."""
        if self.held.get(r) == v:
            if committed:
                return "STA"
            del self.held[r]
            self.events.append(("release", t, r, v))
            return "REL"
        if v > self.known.get(r, -1):
            # revoke overtook its assign: fence version v so the late assign is ignored
            self.known[r] = v
            self.events.append(("fence", t, r, v))
            return "REL"
        return None

    def release(self, r: int, t: int):
        v = self.held.pop(r)
        self.events.append(("release", t, r, v))
        return v

    def finish(self, r: int, t: int):
        v = self.held.pop(r)
        self.events.append(("finish", t, r, v))
        return v

    def holds(self, r: int) -> bool:
        return r in self.held
