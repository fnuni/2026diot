"""Generate LaTeX macros, tables and figure data from analysis.json and verification outputs.

The manuscript contains a block delimited by '% BEGIN GENERATED MACROS' / '% END GENERATED MACROS';
this script replaces it. Every value quoted in the text is a \\val{key} macro defined here.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from .analysis import LABEL, ORDER, NETS

SC = lambda m: "\\textsc{" + LABEL[m].replace("+", "+") + "}"


def f(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "--"
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return s.replace("-", "$-$") if not sign else s.replace("-", "$-$").replace("+", "$+$")


def pfmt(p):
    return "$<$0.001" if p < 0.001 else f"{p:.3f}"


def build(res: dict, ver: dict, counts: dict, extra: dict | None = None) -> str:
    V = {}
    V["runs-total"] = f"{sum(counts.values()):,}".replace(",", "{,}")
    for k, v in counts.items():
        V[f"runs-{k}"] = f"{v:,}".replace(",", "{,}")
    for t in res["confirmatory"]:
        i = t["id"]
        V[f"{i}-mean"] = f"{t['mean']:.2f}".replace("-", "$-$")
        V[f"{i}-lo"] = f"{t['ci_lo']:.2f}".replace("-", "$-$")
        V[f"{i}-hi"] = f"{t['ci_hi']:.2f}".replace("-", "$-$")
        V[f"{i}-p"] = pfmt(t["p"])
        V[f"{i}-pholm"] = pfmt(t["p_holm"])
    for q in res["equivalence"]:
        i = q["id"]
        V[f"{i}-mean"] = f"{q['mean']:.2f}".replace("-", "$-$")
        V[f"{i}-lo"] = f"{q['ci90_lo']:.2f}".replace("-", "$-$")
        V[f"{i}-hi"] = f"{q['ci90_hi']:.2f}".replace("-", "$-$")
        V[f"{i}-eq"] = "yes" if q["equivalent"] else "no"
    tab = res["S1_table"]
    for key, t in tab.items():
        net, m = key.split("|")
        V[f"pwc-{m}-{net}"] = f"{t['priority_handled_pct'][0]:.2f}"
        V[f"urg-{m}-{net}"] = f"{t['urgent_handled_pct'][0]:.1f}"
        V[f"hand-{m}-{net}"] = f"{t['handled_pct'][0]:.1f}"
        V[f"dyn-{m}-{net}"] = f"{t['dynamic_handled_pct'][0]:.1f}"
        V[f"trav-{m}-{net}"] = f"{t['travel_min'][0]:.0f}"
        V[f"msgs-{m}-{net}"] = f"{t['msgs_sent'][0]:.0f}"
        V[f"kb-{m}-{net}"] = f"{t['bytes_sent'][0] / 1000:.0f}"
        V[f"cp95-{m}-{net}"] = f"{t['center_p95_ms'][0]:.0f}"
        V[f"ap95-{m}-{net}"] = f"{t['agent_p95_ms'][0]:.1f}"
        V[f"quar-{m}-{net}"] = f"{t['quarantines'][0]:.2f}"
        V[f"swaps-{m}-{net}"] = f"{t['swaps'][0]:.1f}"
    for e in res["S1_vs_central"]:
        tag = {"priority_handled_pct": "d", "urgent_handled_pct": "du", "travel_min": "dt"}[e["metric"]]
        V[f"{tag}-{e['method']}-{e['net']}"] = f"{e['mean']:.2f}".replace("-", "$-$")
        V[f"{tag}lo-{e['method']}-{e['net']}"] = f"{e['ci_lo']:.2f}".replace("-", "$-$")
        V[f"{tag}hi-{e['method']}-{e['net']}"] = f"{e['ci_hi']:.2f}".replace("-", "$-$")
    for r in res["factorial"]:
        tag = {"priority_handled_pct": "fac", "travel_min": "facT", "swaps": "facS"}[r["metric"]]
        for name in ("traffic", "thermal", "interaction"):
            V[f"{tag}-{r['method']}-{name}"] = f"{r[name]:.2f}".replace("-", "$-$")
            V[f"{tag}lo-{r['method']}-{name}"] = f"{r[name + '_ci'][0]:.2f}".replace("-", "$-$")
            V[f"{tag}hi-{r['method']}-{name}"] = f"{r[name + '_ci'][1]:.2f}".replace("-", "$-$")
    ints = {k: v for k, v in res.items() if k.endswith("_integrity")}
    V["viol-total"] = str(sum(v["violations"] + v["token_overlaps"] + v["unauthorized_services"] +
                              v["cold_after_quarantine"] + v["not_home"] for v in ints.values()))
    V["alns-timeouts"] = str(sum(v["alns_timeouts"] for v in ints.values()))
    V["late-returns"] = str(sum(v["late_returns"] for v in ints.values()))
    # verification
    t1 = ver["t1"]
    V["t1-cases"] = str(len(t1))
    V["t1-maxdiff"] = f"{max(r['abs_diff'] for r in t1):.1e}".replace("e-", "\\times10^{-") + "}"
    V["t1-nmin"] = str(min(r["n"] for r in t1))
    V["t1-nmax"] = str(max(r["n"] for r in t1))
    V["t1-colmax"] = f"{max(r['columns'] for r in t1):,}".replace(",", "{,}")
    t2 = ver["t2"]
    V["t2-trials"] = f"{t2['trials']:,}".replace(",", "{,}")
    V["t2-margin"] = f"{-t2['max_theta_margin']:.2f}"
    V["t2-ratio"] = f"{t2['max_energy_ratio']:.2f}"
    V["t2-viol"] = str(t2["theta_violations"] + t2["energy_violations"] + t2["low_violations"])
    t3 = ver["t3"]
    V["t3-trials"] = f"{t3['trials']:,}".replace(",", "{,}")
    V["t3-events"] = f"{t3['events']:,}".replace(",", "{,}")
    V["t3-viol"] = str(t3["multi_holder"] + t3["double_service"] + t3["unauthorized"])
    # S4
    if "S4" in res:
        e4 = res["S4"]
        V["e4-equal"] = f"{sum(r['equal'] for r in e4)}/{len(e4)}"
        V["e4-wallratio"] = f"{sum(r['wall_mp'] for r in e4) / max(1e-9, sum(r['wall_in'] or 0 for r in e4)):.1f}"
        V["e4-ccpu"] = f"{sum(r['center_cpu'] for r in e4) / len(e4):.2f}"
        V["e4-vcpu"] = f"{1000 * sum(r['vehicle_cpu'] for r in e4) / len(e4) / 8:.0f}"
    for k, v in (extra or {}).items():
        V[k] = v
    # ---------------------------------------------------------------- tables
    out = []
    for k, v in sorted(V.items()):
        out.append("\\expandafter\\def\\csname v@%s\\endcsname{%s}" % (k, v))
    # main PWC table
    rows = []
    for m in ORDER:
        cells = []
        for n in NETS:
            t = tab.get(f"{n}|{m}")
            cells.append(f"{t['priority_handled_pct'][0]:.2f} $\\pm$ {t['priority_handled_pct'][1]:.2f}" if t else "--")
        rows.append(SC(m) + " & " + " & ".join(cells) + " \\\\")
    out.append("\\newcommand{\\TabMain}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{3pt}"
               "\\caption{S1: priority-weighted on-time completion (PWC, \\%), mean $\\pm$ SD over 160 runs per cell "
               "(40 seeds $\\times$ 4 environment cells), $K=8$, $\\rho=0.9$.}\\label{tab:main}"
               "\\begin{tabular}{lccccc}\\toprule Controller & N0 & N1 & N2 & N3 & N4 \\\\\\midrule "
               + " ".join(rows) + " \\bottomrule\\end{tabular}\\end{table}}")
    # secondary table at N1 and N4
    rows = []
    for m in ORDER:
        r = []
        for n in ("N1", "N3", "N4"):
            t = tab[f"{n}|{m}"]
            r.append(f"{t['urgent_handled_pct'][0]:.1f}")
        t1_ = tab[f"N1|{m}"]
        r += [f"{t1_['travel_min'][0]:.0f}", f"{t1_['overtime_min_total'][0]:.1f}", f"{t1_['swaps'][0]:.1f}",
              f"{t1_['msgs_sent'][0] / 8 / 7.75:.0f}", f"{t1_['bytes_sent'][0] / 8 / 7.75 / 1000:.1f}",
              f"{t1_['center_p95_ms'][0]:.0f}", f"{t1_['agent_p95_ms'][0]:.1f}"]
        rows.append(SC(m) + " & " + " & ".join(r) + " \\\\")
    out.append("\\newcommand{\\TabSecondary}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{2.5pt}"
               "\\caption{S1 secondary outcomes (means). Urgent completion under N1, N3 and N4; the remaining "
               "columns refer to N1: travel and total overtime (min per shift, fleet), carrier exchanges per shift, "
               "messages and kB per vehicle-hour, 95th-percentile decision time of the coordinator and of the "
               "agents (ms, measured on the reference host).}\\label{tab:secondary}"
               "\\begin{tabular}{lrrrrrrrrrr}\\toprule & \\multicolumn{3}{c}{Urgent (\\%)} & & & & "
               "\\multicolumn{2}{c}{Per vehicle-hour} & \\multicolumn{2}{c}{p95 (ms)}\\\\ "
               "\\cmidrule(lr){2-4}\\cmidrule(lr){8-9}\\cmidrule(lr){10-11} Controller & N1 & N3 & N4 & Travel & "
               "Overtime & Exch. & Msgs & kB & Coord. & Agent \\\\\\midrule " + " ".join(rows) +
               " \\bottomrule\\end{tabular}\\end{table}}")
    # confirmatory table
    rows = []
    for t in res["confirmatory"]:
        rows.append(f"{t['id']} & {t['net']} & {SC(t['a'])} -- {SC(t['b'])} & {f(t['mean'])} "
                    f"[{f(t['ci_lo'])}, {f(t['ci_hi'])}] & {pfmt(t['p'])} & {pfmt(t['p_holm'])} \\\\")
    for q in res["equivalence"]:
        rows.append(f"{q['id']} & {q['net']} & {SC(q['a'])} -- {SC(q['b'])} & {f(q['mean'])} "
                    f"[{f(q['ci90_lo'])}, {f(q['ci90_hi'])}]$^{{\\dagger}}$ & \\multicolumn{{2}}{{c}}"
                    f"{{equivalent within $\\pm${q['margin']:.1f}: {'yes' if q['equivalent'] else 'no'}}} \\\\")
    out.append("\\newcommand{\\TabConfirm}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{3pt}"
               "\\caption{Prespecified comparisons on PWC (percentage points; 40 paired seeds, each the mean over "
               "the four environment cells). Superiority: 95\\% paired bootstrap interval, two-sided sign-flip "
               "p-value and Holm-adjusted p-value. $^{\\dagger}$90\\% interval for the equivalence statements."
               "}\\label{tab:confirm}\\begin{tabular}{llllll}\\toprule Id & Net & Comparison & Difference [interval] & "
               "p & Holm p \\\\\\midrule " + " ".join(rows) + " \\bottomrule\\end{tabular}\\end{table}}")
    # factorial table
    rows = []
    for r in res["factorial"]:
        if r["metric"] != "priority_handled_pct":
            continue
        cells = []
        for name in ("traffic", "thermal", "interaction"):
            cells.append(f"{f(r[name])} [{f(r[name + '_ci'][0])}, {f(r[name + '_ci'][1])}]")
        rows.append(SC(r["method"]) + " & " + " & ".join(cells) + " \\\\")
    out.append("\\newcommand{\\TabFactorial}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{3pt}"
               "\\caption{S1 environmental effects on PWC (percentage points, pooled over N0--N4; 95\\% bootstrap "
               "intervals over seeds). Main effect = mean(on) $-$ mean(off); interaction = difference of "
               "differences.}\\label{tab:factorial}\\begin{tabular}{llll}\\toprule Controller & Traffic incident & "
               "Thermal stress & Interaction \\\\\\midrule " + " ".join(rows) + " \\bottomrule\\end{tabular}\\end{table}}")
    # S2
    if "S2_table" in res:
        t2_ = res["S2_table"]
        cfgs = [(8, 0.75), (8, 1.05), (4, 0.9), (16, 0.9)]
        rows = []
        for m in ORDER:
            r = []
            for net in ("N1", "N3"):
                for teams, load in cfgs:
                    v = t2_.get(f"{teams}|{load}|{net}|{m}")
                    r.append(f"{v['pwc'][0]:.1f}" if v else "--")
            rows.append(SC(m) + " & " + " & ".join(r) + " \\\\")
        lat = []
        for teams, load in cfgs:
            v = t2_.get(f"{teams}|{load}|N1|CENTRAL")
            lat.append(f"{v['center_p95'][0]:.0f}/{v['center_max'][0]:.0f}" if v else "--")
        nreq = [f"{t2_[f'{teams}|{load}|N1|CENTRAL']['N'][0]:.0f}" for teams, load in cfgs]
        out.append("\\newcommand{\\TabLoadSize}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{2.5pt}"
                   "\\caption{S2: PWC (\\%) when load $\\rho$ and fleet size $K$ vary separately (20 seeds, traffic "
                   "and thermal stress on). Area per team is constant. Last rows: requests per shift and "
                   "\\textsc{Central} coordinator decision time, p95/max (ms).}\\label{tab:loadsize}"
                   "\\begin{tabular}{lrrrrrrrr}\\toprule & \\multicolumn{4}{c}{N1} & \\multicolumn{4}{c}{N3}\\\\"
                   "\\cmidrule(lr){2-5}\\cmidrule(lr){6-9} Controller & $\\rho$=0.75 & $\\rho$=1.05 & $K$=4 & $K$=16 & "
                   "$\\rho$=0.75 & $\\rho$=1.05 & $K$=4 & $K$=16 \\\\\\midrule " + " ".join(rows) +
                   " \\midrule Requests & " + " & ".join(nreq) + " & " + " & ".join(nreq) + " \\\\ "
                   "Latency & " + " & ".join(lat) + " & & & & \\\\ \\bottomrule\\end{tabular}\\end{table}}")
        for key, v in t2_.items():
            teams, load, net, m = key.split("|")
            out.append("\\expandafter\\def\\csname v@e2pwc-%s-%s-%s-%s\\endcsname{%.1f}" % (m, teams, load, net, v["pwc"][0]))
            out.append("\\expandafter\\def\\csname v@e2urg-%s-%s-%s-%s\\endcsname{%.1f}" % (m, teams, load, net, v["urgent"][0]))
            if m == "CENTRAL" and net == "N1":
                out.append("\\expandafter\\def\\csname v@e2lat95-%s-%s\\endcsname{%.0f}" % (teams, load, v["center_p95"][0]))
                out.append("\\expandafter\\def\\csname v@e2latmax-%s-%s\\endcsname{%.0f}" % (teams, load, v["center_max"][0]))
                out.append("\\expandafter\\def\\csname v@e2N-%s-%s\\endcsname{%.0f}" % (teams, load, v["N"][0]))
        for p in res["S2_paired"]:
            C2 = f"e2-{p['method']}-{p['teams']}-{p['load']}-{p['net']}"
            out.append("\\expandafter\\def\\csname v@%s\\endcsname{%s}" % (C2, f(p["mean"])))
    # S3 figure
    if "S3_map" in res:
        plots = []
        colors = {5.0: "black", 15.0: "blue!70!black", 30.0: "red!70!black"}
        marks = {5.0: "*", 15.0: "square*", 30.0: "triangle*"}
        for mo in (5.0, 15.0, 30.0):
            coords = " ".join(f"({r['availability']},{r['mean']:.3f}) +- (0,{(r['ci_hi'] - r['ci_lo']) / 2:.3f})"
                              for r in res["S3_map"] if r["method"] == "HYBRID" and r["mean_outage"] == mo)
            plots.append(f"\\addplot+[{colors[mo]},mark={marks[mo]},error bars/.cd,y dir=both,y explicit] "
                         f"coordinates {{{coords}}}; \\addlegendentry{{$\\mu={mo:.0f}$ min}}")
        coords = " ".join(f"({r['availability']},{r['mean']:.3f})" for r in res["S3_map"]
                          if r["method"] == "EDGE_MARKET" and r["mean_outage"] == 15.0)
        plots.append(f"\\addplot+[gray,dashed,mark=o] coordinates {{{coords}}}; "
                     "\\addlegendentry{\\textsc{Edge-market}, $\\mu=15$}")
        out.append("\\newcommand{\\FigMap}{\\begin{figure}[htbp]\\centering\\begin{tikzpicture}\\begin{axis}["
                   "width=0.82\\textwidth,height=6.2cm,xlabel={Link availability $\\alpha$},ylabel={PWC difference "
                   "vs \\textsc{Central} (pp)},x dir=reverse,xtick={0.55,0.65,0.75,0.85,0.95},grid=major,"
                   "legend columns=4,legend style={font=\\scriptsize,at={(0.5,-0.22)},anchor=north}]" + " ".join(plots) +
                   "\\end{axis}\\end{tikzpicture}\\caption{S3 decision map. Paired difference in PWC between "
                   "\\textsc{Hybrid} and \\textsc{Central} (mean and 95\\% bootstrap interval, 20 seeds) as link "
                   "availability decreases, for three mean outage durations $\\mu$; the dashed line shows "
                   "\\textsc{Edge-market} for $\\mu=15$ min.}\\label{fig:map}\\end{figure}}")
        rows = []
        for m in ("HYBRID", "HYBRID_EAGER", "EDGE_MARKET"):
            for mo in (5.0, 15.0, 30.0):
                cells = [f"{r['mean']:+.2f}".replace("-", "$-$") for r in res["S3_map"]
                         if r["method"] == m and r["mean_outage"] == mo]
                rows.append(f"{SC(m) if mo == 5.0 else ''} & {mo:.0f} & " + " & ".join(cells) + " \\\\")
        cen = []
        for a in (0.95, 0.85, 0.75, 0.65, 0.55):
            vals = [r["central"] for r in res["S3_map"] if r["availability"] == a and r["method"] == "HYBRID"]
            cen.append(f"{sum(vals) / len(vals):.1f}")
        out.append("\\newcommand{\\TabMap}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{3pt}"
                   "\\caption{S3: mean paired PWC difference with respect to \\textsc{Central} (pp; 20 seeds per "
                   "cell). \\textsc{Central+FB} coincides with \\textsc{Hybrid} in every cell and is omitted. "
                   "The last row gives the \\textsc{Central} PWC averaged over outage durations.}"
                   "\\label{tab:map}\\begin{tabular}{llrrrrr}\\toprule Controller & $\\mu$ (min) & $\\alpha$=0.95 & "
                   "0.85 & 0.75 & 0.65 & 0.55 \\\\\\midrule " + " ".join(rows) +
                   " \\midrule \\textsc{Central} PWC & & " + " & ".join(cen) + " \\\\ \\bottomrule\\end{tabular}\\end{table}}")
        def rng(m):
            xs = [r["mean"] for r in res["S3_map"] if r["method"] == m]
            return f(min(xs)), f(max(xs))
        for m, tag in (("HYBRID", "hyb"), ("EDGE_MARKET", "mkt"), ("HYBRID_EAGER", "eag")):
            lo_, hi_ = rng(m)
            out.append("\\expandafter\\def\\csname v@e3-%s-min\\endcsname{%s}" % (tag, lo_))
            out.append("\\expandafter\\def\\csname v@e3-%s-max\\endcsname{%s}" % (tag, hi_))
        sig = sum(1 for r in res["S3_map"] if r["method"] == "HYBRID" and (r["ci_lo"] > 0 or r["ci_hi"] < 0))
        out.append("\\expandafter\\def\\csname v@e3-hyb-sig\\endcsname{%d}" % sig)
        for a in (0.95, 0.55):
            vals = [r["central"] for r in res["S3_map"] if r["availability"] == a and r["method"] == "HYBRID"]
            out.append("\\expandafter\\def\\csname v@e3-cen-%s\\endcsname{%.1f}" % (str(a).replace(".", ""), sum(vals) / len(vals)))
        for r in res["S3_map"]:
            out.append("\\expandafter\\def\\csname v@e3-%s-%s-%g\\endcsname{%s}" % (
                r["method"], r["availability"], r["mean_outage"], f(r["mean"])))
    # S5
    if "S5_table" in res:
        t5 = res["S5_table"]
        rows = []
        for mis in (1.0, 1.6, 2.0):
            for net in ("N1", "N3"):
                r = [f"{mis:.1f}" if net == "N1" else "", net]
                for m in ("CENTRAL", "CENTRAL_FB", "HYBRID"):
                    v = t5[f"{mis}|{net}|{m}"]
                    r += [f"{v['pwc'][0]:.1f}", f"{v['quarantines'][0]:.2f}", f"{v['wasted'][0]:.1f}"]
                rows.append(" & ".join(r) + " \\\\")
                for m in ("CENTRAL", "CENTRAL_FB", "HYBRID"):
                    v = t5[f"{mis}|{net}|{m}"]
                    out.append("\\expandafter\\def\\csname v@e5q-%s-%s-%s\\endcsname{%.2f}" % (m, mis, net, v["quarantines"][0]))
                    out.append("\\expandafter\\def\\csname v@e5w-%s-%s-%s\\endcsname{%.1f}" % (m, mis, net, v["wasted"][0]))
                    out.append("\\expandafter\\def\\csname v@e5p-%s-%s-%s\\endcsname{%.1f}" % (m, mis, net, v["pwc"][0]))
        out.append("\\newcommand{\\TabMisspec}{\\begin{table}[htbp]\\centering\\small\\setlength{\\tabcolsep}{2.5pt}"
                   "\\caption{S5: violation of assumption (A1). All carriers have true conductance factor $f$ while "
                   "planning assumes $f\\le1.15$ (thermal stress, no traffic, 20 seeds). PWC (\\%), carrier "
                   "quarantines and wasted doses per shift.}\\label{tab:misspec}\\begin{tabular}{llrrrrrrrrr}\\toprule "
                   "& & \\multicolumn{3}{c}{\\textsc{Central}} & \\multicolumn{3}{c}{\\textsc{Central+FB}} & "
                   "\\multicolumn{3}{c}{\\textsc{Hybrid}}\\\\\\cmidrule(lr){3-5}\\cmidrule(lr){6-8}\\cmidrule(lr){9-11}"
                   " $f$ & Net & PWC & Quar. & Wasted & PWC & Quar. & Wasted & PWC & Quar. & Wasted \\\\\\midrule "
                   + " ".join(rows) + " \\bottomrule\\end{tabular}\\end{table}}")
    # PWC-by-network figure
    plots = []
    styles = {"CENTRAL": "black,mark=*", "CENTRAL_FOG": "gray,mark=o", "CENTRAL_FB": "blue!60!black,mark=square",
              "HYBRID": "red!70!black,mark=triangle*,thick", "EDGE_MARKET": "orange!80!black,mark=diamond*",
              "PERIODIC": "teal,mark=x", "STATIC": "brown,mark=+"}
    for m, st in styles.items():
        coords = " ".join(f"({n},{tab[f'{n}|{m}']['priority_handled_pct'][0]:.3f})" for n in NETS)
        plots.append(f"\\addplot[{st}] coordinates {{{coords}}}; \\addlegendentry{{{LABEL[m]}}}")
    out.append("\\newcommand{\\FigPWC}{\\begin{figure}[htbp]\\centering\\begin{tikzpicture}\\begin{axis}["
               "width=0.82\\textwidth,height=6.2cm,symbolic x coords={N0,N1,N2,N3,N4},xtick=data,"
               "ylabel={PWC (\\%)},grid=major,legend columns=4,legend style={font=\\scriptsize,at={(0.5,-0.18)},"
               "anchor=north}]" + " ".join(plots) + "\\end{axis}\\end{tikzpicture}\\caption{S1: mean PWC by "
               "network profile (160 runs per point). N4 is a 120-min cloud outage on top of N1.}\\label{fig:pwc}"
               "\\end{figure}}")
    return "\n".join(out)


def totals(res_dir: Path) -> dict:
    """Stage-level totals read directly from the run tables (descriptive only)."""
    import csv
    out = {}
    for st in ("S1", "S2", "S3", "S4", "S5"):
        rows = []
        for p in res_dir.glob(f"runs_{st}_*.csv"):
            with p.open() as fh:
                rows += list(csv.DictReader(fh))
        if not rows:
            continue
        q = sum(float(r["quarantines"]) for r in rows)
        w = sum(float(r["wasted_doses"]) for r in rows)
        out[f"quar-total-{st}"] = f"{q:.0f}"
        out[f"waste-total-{st}"] = f"{w:.0f}"
        out[f"quar-runs-{st}"] = str(sum(1 for r in rows if float(r["quarantines"]) > 0))
        out[f"wall-mean-{st}"] = f"{sum(float(r['wall_s']) for r in rows) / len(rows):.2f}"
        out[f"theta-max-{st}"] = f"{max(float(r['theta_max']) for r in rows if r['theta_max'] not in ('', 'None')):.2f}"
        if st == "S1":
            nom = [r for r in rows if float(r["ua_misspec"] or 0) == 0 or r["ua_misspec"] in ("", "None")]
            out["e1-maxdelay"] = f"{max(float(r['max_msg_delay']) for r in rows):.0f}"
            out["e1-dups"] = f"{sum(float(r['duplicates']) for r in rows):.0f}"
            out["e1-retries"] = f"{sum(float(r['retries']) for r in rows):.0f}"
            out["e1-releases-market"] = f"{sum(float(r['releases']) for r in rows if r['method'] == 'EDGE_MARKET') / max(1, sum(1 for r in rows if r['method'] == 'EDGE_MARKET')):.1f}"
            out["e1-N"] = f"{sum(float(r['N']) for r in rows) / len(rows):.1f}"
            # Raw logs are intentionally represented by a small public sample.
            # The complete-log audit summary retains the two aggregate values
            # needed by the manuscript without pretending to redistribute logs.
            audit = json.loads((res_dir.parent / "audit" / "audit_results.json").read_text())
            exc = audit["S1_excursion_summary"]
            tot = exc["quarantines"]
            viol = exc["quarantines_with_true_ua_factor_above_planning_bound"]
            fmin = exc["minimum_quarantined_ua_factor"]
            out["e1-quar-a1"] = f"{viol}/{tot}"
            out["e1-quar-fmin"] = f"{fmin:.2f}"
    return out


def main(results: str, manuscript: str):
    res_dir = Path(results)
    res = json.loads((res_dir / "analysis" / "analysis.json").read_text())
    ver = dict(t1=json.loads((res_dir / "verification" / "t1_column_arc.json").read_text()),
               t2=json.loads((res_dir / "verification" / "t2_thermal_bound.json").read_text()),
               t3=json.loads((res_dir / "verification" / "t3_token_protocol.json").read_text()))
    counts = {}
    for st in ("S1", "S2", "S3", "S4", "S5"):
        n = 0
        for p in res_dir.glob(f"runs_{st}_*.csv"):
            n += sum(1 for _ in p.open()) - 1
        counts[st] = n
    extra = totals(res_dir)
    for tag, name in (("host", "repeat_check_host.json"), ("plat", "repeat_check_platform2.json")):
        pth = res_dir / name
        if pth.exists():
            rc = json.loads(pth.read_text())
            extra[f"rep-{tag}"] = f"{rc['equal']}/{rc['checked']}"
    block = build(res, ver, counts, extra)
    (res_dir / "analysis" / "generated_macros.tex").write_text(block + "\n")
    tex = Path(manuscript)
    s = tex.read_text()
    a = s.index("% BEGIN GENERATED MACROS")
    b = s.index("% END GENERATED MACROS")
    s = s[:a] + "% BEGIN GENERATED MACROS\n" + block + "\n" + s[b:]
    tex.write_text(s)
    used = set(re.findall(r"\\val\{([^}]+)\}", s))
    defined = set(re.findall(r"\\csname v@([^\\]+)\\endcsname", block))
    missing = sorted(used - defined)
    print("macros used", len(used), "missing", missing)
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
