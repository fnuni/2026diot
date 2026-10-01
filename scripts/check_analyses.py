"""Recompute both prespecified analyses from the archived run tables and compare them
with the archived JSON outputs, without modifying any file of the repository.

Usage (from the repository root):
    python scripts/check_analyses.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class AnalysisCheck(ABC):
    """Recompute one analysis in a scratch directory and compare it with the archive."""

    name: str = ""
    archived: Path

    def __init__(self, tol: float = 1e-9):
        self.tol = tol

    @abstractmethod
    def recompute(self, scratch: Path) -> dict:
        """Return the recomputed analysis as a JSON-compatible dictionary."""

    def compare(self, new, old, path: str = "") -> list[tuple[str, object, object]]:
        if isinstance(new, dict):
            if not isinstance(old, dict) or set(new) != set(old):
                return [(path or "/", "keys", "differ")]
            out = []
            for key in new:
                out += self.compare(new[key], old[key], f"{path}/{key}")
            return out
        if isinstance(new, list):
            if not isinstance(old, list) or len(new) != len(old):
                return [(path, "length", "differ")]
            out = []
            for i, (a, b) in enumerate(zip(new, old)):
                out += self.compare(a, b, f"{path}[{i}]")
            return out
        if isinstance(new, float) or isinstance(old, float):
            ok = new is not None and old is not None and abs(float(new) - float(old)) <= self.tol
            return [] if ok else [(path, new, old)]
        return [] if new == old else [(path, new, old)]

    def run(self) -> bool:
        with tempfile.TemporaryDirectory(prefix="aedge-check-") as tmp:
            new = json.loads(json.dumps(self.recompute(Path(tmp))))
        old = json.loads(self.archived.read_text())
        bad = self.compare(new, old)
        status = "OK" if not bad else f"{len(bad)} mismatches, first: {bad[:3]}"
        print(f"[{self.name}] {status}")
        return not bad


class ScreeningPhaseCheck(AnalysisCheck):
    """Screening phase S1--S5."""

    name = "screening S1-S5"
    archived = ROOT / "campaigns/screening/results/analysis/analysis.json"

    def recompute(self, scratch: Path) -> dict:
        from aedge.analysis import analyse
        return analyse(ROOT / "campaigns/screening/results", scratch)


class ConfirmatoryPhaseCheck(AnalysisCheck):
    """Confirmatory phase C1--C3."""

    name = "confirmatory C1-C3"
    archived = ROOT / "campaigns/confirmatory/results" / "analysis.json"

    def recompute(self, scratch: Path) -> dict:
        from confirmatory.analyse import analyse
        shutil.copy2(ROOT / "campaigns/confirmatory/results" / "runs.csv", scratch / "runs.csv")
        return analyse(scratch)


class CheckSuite:
    def __init__(self, checks: list[AnalysisCheck]):
        self.checks = checks

    def run(self) -> int:
        results = [check.run() for check in self.checks]
        return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(CheckSuite([ScreeningPhaseCheck(), ConfirmatoryPhaseCheck()]).run())
