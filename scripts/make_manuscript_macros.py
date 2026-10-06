"""Regenerate the LaTeX macros of the confirmatory phase (\\nval{...}) used in the manuscript
from campaigns/confirmatory/results/analysis.json. Screening macros (\\val{...}) are produced by
aedge/make_tex.py and saved in campaigns/screening/results/analysis/generated_macros.tex.

Usage (from the repository root):
    python scripts/make_manuscript_macros.py --out confirmatory_macros.tex
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("CENTRAL", "CENTRAL_FB", "CENTRAL_FOG", "CENTRAL_FOG_FB", "HYBRID", "GREEDY_FOG", "STATIC", "GREEDY_FB")


class Number:
    """Formats a value as it appears in the manuscript (true minus sign, fixed decimals)."""

    def __init__(self, decimals: int = 2):
        self.decimals = decimals

    def __call__(self, x: float) -> str:
        s = f"{x:.{self.decimals}f}"
        if s.startswith("-"):
            return s[1:] if float(s) == 0 else "$-$" + s[1:]
        return s


class MacroWriter:
    def __init__(self, analysis: dict):
        self.a = analysis
        self.lines = ["% BEGIN GENERATED MACROS (confirmatory phase C1--C3; values from campaigns/confirmatory/results/analysis.json)"]

    def put(self, key: str, value: str) -> None:
        self.lines.append(r"\expandafter\def\csname n@" + key + r"\endcsname{" + value + "}")

    def summary(self, prefix: str, s: dict, fields=("mean", "lo", "hi"), fmt=Number()) -> None:
        for f in fields:
            self.put(f"{prefix}-{f}", fmt(s[f]))

    def build(self) -> str:
        two, three, one = Number(2), Number(3), Number(1)
        self.put("runs-total", f"{self.a['runs']:,}".replace(",", "{,}"))
        for stage, count in self.a["stages"].items():
            self.put(f"runs-{stage}", f"{count:,}".replace(",", "{,}"))
        conf = self.a["confirmatory"]
        for name, key in (("F1", "F1_placement_N4"), ("F2", "F2_autonomy_N3"), ("F3", "F3_interaction_N4")):
            s = conf[key]
            self.summary(name, s, ("mean", "lo", "hi", "bootstrap_lo", "bootstrap_hi"))
            self.put(f"{name}-pholm", "$<$0.001" if s["p_holm"] < 0.001 else f"{s['p_holm']:.3f}")
            self.put(f"{name}-wtl", f"{s['wins']}/{s['ties']}/{s['losses']}")
        q = self.a["equivalence"]["Q5_hybrid_vs_fog_N4"]
        self.summary("Q5", q)
        self.summary("Q5", q, ("lo90", "hi90"), three)
        self.put("Q5-ptost", f"{q['p_tost']:.3f}")
        self.put("Q5-wtl", f"{q['wins']}/{q['ties']}/{q['losses']}")
        for bundle, s in self.a["equivalence_by_bundle"].items():
            self.summary(f"Q5-{bundle}", s)
            self.summary(f"Q5-{bundle}", s, ("lo90", "hi90"), two)
            self.put(f"Q5-{bundle}-eq", "yes" if s["equivalent"] else "no")
        exp = self.a["exploratory"]
        for key, name in (("N4|hybrid_minus_cloud", "HC4"), ("N4|autonomy", "A4"), ("N4|interaction", "I4"),
                          ("N3|hybrid_minus_cloud", "HC3"), ("N1|autonomy", "A1")):
            self.summary(name, exp[key])
        hand = self.a["handling_sensitivity"]
        for name, key in (("F1", "F1_placement_N4"), ("F2", "F2_autonomy_N3"), ("F3", "F3_interaction_N4")):
            self.summary(f"pwh{name}", hand[key])
        desc = self.a["descriptive"]
        for net in ("N1", "N3", "N4"):
            for m in METHODS:
                d = desc[f"{net}|{m}"]
                self.put(f"pws-{m}-{net}", two(d["priority_served_pct"]["mean"]))
                self.put(f"pwssd-{m}-{net}", two(d["priority_served_pct"]["sd"]))
                self.put(f"pwh-{m}-{net}", two(d["priority_handled_pct"]["mean"]))
                self.put(f"trav-{m}-{net}", one(d["travel_min"]["mean"]))
                self.put(f"cp95-{m}-{net}", one(d["center_p95_ms"]["mean"]))
                self.put(f"ap95-{m}-{net}", two(d["agent_p95_ms"]["mean"]))
                self.put(f"kb-{m}-{net}", one(d["bytes_sent"]["mean"] / 1000))
        for key, s in self.a["outage_sensitivity"].items():
            _, start, duration, m = key.split("|")
            self.summary(f"out-{start}-{duration}-{m}", s)
        for key, s in self.a["budget_sensitivity"].items():
            it, net, m = key.split("|")
            self.summary(f"bud-{it}-{net}-{m}", s)
        for key, s in self.a["exploratory_fallback_contrasts"].items():
            self.summary("fallback-" + key.replace("|", "-"), s)
        for net, s in self.a["exploratory_weak_policy_placement"].items():
            self.summary(f"weak-placement-{net}", s)
        self.lines.append("% END GENERATED MACROS (confirmatory phase)")
        return "\n".join(self.lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, default=ROOT / "campaigns/confirmatory/results" / "analysis.json")
    parser.add_argument("--out", type=Path, default=Path("confirmatory_macros.tex"))
    args = parser.parse_args()
    args.out.write_text(MacroWriter(json.loads(args.analysis.read_text())).build())
    print(f"Wrote {args.out}")
