"""Shared utilities: configuration, canonical hashing, keyed deterministic randomness."""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWAP = -1  # route marker: return to own hub and exchange the carrier


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or ROOT / "config.json").read_text())


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def keyed_uniform(*key) -> float:
    """Uniform(0,1) from a hash of the key; independent of call order."""
    h = hashlib.sha256(":".join(str(k) for k in key).encode()).digest()
    return (int.from_bytes(h[:8], "big") + 0.5) / 2.0 ** 64


def keyed_normal(*key) -> float:
    u1 = keyed_uniform(*key, "n1")
    u2 = keyed_uniform(*key, "n2")
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def keyed_rng(*key) -> random.Random:
    h = hashlib.sha256(":".join(str(k) for k in key).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def lognormal_mean_one(sigma_log: float, z: float) -> float:
    """Lognormal multiplier with unit mean."""
    return math.exp(sigma_log * z - 0.5 * sigma_log ** 2)
