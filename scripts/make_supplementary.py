"""Generate complete supplementary tables and the outage figure.

Every numerical cell is read from a distributed table/analysis. Figures are
generated from contrasts, never from manually transcribed coordinates.
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aedge.analysis import boot_ci

LABEL={"CENTRAL":"Central","CENTRAL_FB":"Central+FB","CENTRAL_FOG":"Central@fog",
       "CENTRAL_FOG_FB":"Central@fog+FB","HYBRID":"Hybrid","HYBRID_EAGER":"Hybrid-eager",
       "EDGE_MARKET":"Edge-market","STATIC":"Static","PERIODIC":"Periodic",
       "GREEDY_FOG":"Greedy@fog","GREEDY_FB":"Greedy-FB"}
# Match the controller order in the manuscript; never use JSON insertion order.
CONTROLLER_ORDER = {method: rank for rank, method in enumerate((
    "STATIC", "PERIODIC", "CENTRAL", "CENTRAL_FB", "CENTRAL_FOG",
    "CENTRAL_FOG_FB", "HYBRID", "HYBRID_EAGER", "EDGE_MARKET",
    "GREEDY_FOG", "GREEDY_FB",
))}
DISPLAY_ORDER = {LABEL[method]: rank for method, rank in CONTROLLER_ORDER.items()}

def network_controller_key(item):
    network, method = item[0].split("|")
    return network, CONTROLLER_ORDER[method]

def scale_key(item):
    fleet, load, network, method = item[0].split("|")
    return int(fleet), float(load), network, CONTROLLER_ORDER[method]

def thermal_key(item):
    factor, network, method = item[0].split("|")
    return float(factor), network, CONTROLLER_ORDER[method]

def outage_row_key(row):
    controller = row[2].split("$", 1)[0]
    return int(row[0]), int(row[1]), DISPLAY_ORDER[controller]

def budget_key(item):
    iterations, network, method = item[0].split("|")
    return int(iterations), network, CONTROLLER_ORDER[method]

def num(x): return f"{x:.2f}".replace("-",r"$-$")
def ci(x,lo="lo",hi="hi"): return f"{num(x['mean'])} [{num(x[lo])}, {num(x[hi])}]"
def table(caption,headers,rows,spec):
    head=" & ".join(headers)+r"\\\midrule"+"\n"
    return (r"\begin{longtable}{"+spec+"}\n"+r"\caption{"+caption+r"}\\"+"\n"+
            r"\toprule "+head+r"\endfirsthead"+"\n"+r"\toprule "+head+r"\endhead"+"\n"+
            "\n".join(" & ".join(map(str,r))+r"\\" for r in rows)+"\n"+r"\bottomrule\end{longtable}"+"\n")

def build():
    s=json.loads((ROOT/"campaigns/screening/results/analysis/analysis.json").read_text())
    c=json.loads((ROOT/"campaigns/confirmatory/results/analysis.json").read_text())
    tex=[r"\section{Screening evidence}",
         "PWH is priority-weighted on-time request handling, including absent-patient attempts. Urgent handling has the same inclusion rule. S1 descriptive cells contain 160 paired evaluations (40 seeds across four environments); inference is at seed level. S2 and served-only analyses are exploratory."]
    rows=[[key.split('|')[0],LABEL[key.split('|')[1]],num(v['priority_handled_pct'][0])+r" $\pm$ "+num(v['priority_handled_pct'][1])] for key,v in sorted(s['S1_table'].items(), key=network_controller_key)]
    tex.append(table(r"S1 PWH (\%), mean and SD; all network/controller cells.",["Network","Controller","PWH"],rows,"lp{4.5cm}l"))
    rows=[]
    for v in s['confirmatory']:
        rows.append([v['id'],num(v['mean'])+f" [{num(v['ci_lo'])}, {num(v['ci_hi'])}]",num(v['p_holm']) if v['p_holm']>=.001 else "$<0.001$"])
    for v in s['equivalence']:
        rows.append([{'Q1':'E1','Q2':'E2'}[v['id']],num(v['mean'])+f" [{num(v['ci90_lo'])}, {num(v['ci90_hi'])}]","within margin: "+str(v['equivalent']).lower()])
    tex.append(table(r"Screening contrasts. H1--H4: 95\% bootstrap intervals and Holm-adjusted sign-flip tests. E1/E2: 90\% bootstrap intervals and $\pm1.5$ pp interval criterion. Definitions are in the main label table.",["Label","Effect [interval]","Inference"],rows,"lp{7cm}p{4cm}"))
    rows=[];cost=[]
    for key,v in sorted(s['S1_table'].items(), key=network_controller_key):
        net,m=key.split('|')
        rows.append([net,LABEL[m],num(v['urgent_handled_pct'][0]),num(v['travel_min'][0]),num(v['overtime_min_total'][0]),num(v['swaps'][0])])
        cost.append([net,LABEL[m],num(v['bytes_sent'][0]/48000),num(v['center_p95_ms'][0]),num(v['agent_p95_ms'][0])])
    tex.append(table(r"S1 secondary means: urgent handling (\%), travel and overtime (minutes per shift), exchanges per shift.",["Net","Controller","Urgent","Travel","OT","Exch."],rows,"lp{4cm}rrrr"))
    tex.append(table("S1 payload and reference-host decision diagnostics. kB per vehicle-hour divides shift bytes by 8 vehicles and 6 hours; p95 values (ms) are averaged across runs. Protocol/security overhead is excluded.",["Net","Controller","kB/veh-h","Coord. p95","Agent p95"],cost,"lp{4cm}rrr"))
    rows=[[LABEL[v['method']]]+[num(v[k])+f" [{num(v[k+'_ci'][0])}, {num(v[k+'_ci'][1])}]" for k in ('traffic','thermal','interaction')] for v in sorted(s['factorial'], key=lambda v: CONTROLLER_ORDER[v['method']]) if v['metric']=='priority_handled_pct']
    tex.append(table(r"S1 environmental effects on PWH (pp); 95\% seed-bootstrap intervals, pooling network profiles. Main effects are on minus off; interaction is difference of differences.",["Controller","Traffic","Thermal","Interaction"],rows,"p{3.5cm}p{4cm}p{4cm}p{4cm}"))
    rows=[]
    for key,v in sorted(s['S2_table'].items(), key=scale_key):
        k,load,net,m=key.split('|')
        rows.append([f"{k}/{load}/{net}",LABEL[m],num(v['pwc'][0])+r" $\pm$ "+num(v['pwc'][1]),num(v['urgent'][0])])
    tex.append(table(r"S2 scale/load: PWH mean $\pm$ SD and urgent handling (\%), 20 seeds per cell. All network-profile comparisons are exploratory.",["K/load/net","Controller","PWH","Urgent"],rows,"lp{4cm}lr"))
    rows=[[num(v['availability']),int(v['mean_outage']),LABEL[v['method']],num(v['mean'])+f" [{num(v['ci_lo'])}, {num(v['ci_hi'])}]"] for v in sorted(s['S3_map'], key=lambda v: (float(v['availability']), float(v['mean_outage']), CONTROLLER_ORDER[v['method']]))]
    tex.append(table(r"S3 connectivity sweep: PWH differences from Central (pp), 95\% paired seed-bootstrap intervals, 20 seeds. Each repeated condition shares seeds.",["Availability","Outage min","Controller",r"Effect [95\% CI]"],rows,"llp{4cm}l"))
    rows=[]
    for key,v in sorted(s['S5_table'].items(), key=thermal_key):
        f,net,m=key.split('|')
        rows.append([f,net,LABEL[m],num(v['pwc'][0]),num(v['quarantines'][0]),num(v['wasted'][0])])
    tex.append(table(r"S5 thermal misspecification: PWH (\%), quarantines and wasted doses per shift; means on 20 seeds. Assumption A1 is violated at factors 1.6 and 2.0.",["Factor","Net","Controller","PWH","Quar.","Waste"],rows,"llp{4cm}rrr"))
    raw=list(csv.DictReader((ROOT/"campaigns/screening/audit/screening_served_sensitivity.csv").open()))
    idx={(int(r['seed']),int(r['traffic']),int(r['thermal']),r['network'],r['method']):float(r['served_weighted_pct']) for r in raw}
    rows=[]
    for label,net,a,b in [('H1','N4','HYBRID','CENTRAL'),('H2','N3','HYBRID','CENTRAL')]:
        d=np.array([np.mean([idx[seed,t,h,net,a]-idx[seed,t,h,net,b] for t in (0,1) for h in (0,1)]) for seed in range(8001,8041)])
        lo,hi=boot_ci(d,seed=31337)
        rows.append([label,net,num(d.mean())+f" [{num(lo)}, {num(hi)}]"])
    tex.append(table(r"Exploratory served-only screening sensitivity. Priority-weighted on-time served starts, absent patients excluded from numerator but retained in denominator; 40 seed averages over four environments, 95\% bootstrap intervals. This is not the completed-service completion-matched PWS definition.",["Contrast","Network",r"Served-only effect [95\% CI]"],rows,"llp{9cm}"))
    tex.append(r"\section{Completed-service campaign}")
    rows=[["Pooled",ci(c['equivalence']['Q5_hybrid_vs_fog_N4'],'lo90','hi90'),"yes"]]
    rows.extend([[bundle,ci(v,'lo90','hi90'),"yes" if v['equivalent'] else "no"] for bundle,v in c['equivalence_by_bundle'].items()])
    tex.append(table(r"Hybrid minus Central@fog under N4, E3. 90\% paired t intervals, margin $\pm1.5$ pp; 40 seeds. Pooled comparison is primary; bundle analyses are exploratory.",["Bundle",r"PWS effect [90\% CI]","Within margin?"],rows,"lp{8cm}l"))
    rows=[];cost=[]
    for key,v in sorted(c['descriptive'].items(), key=network_controller_key):
        net,m=key.split('|');label=LABEL[m]+("$^*$" if m in ('STATIC','GREEDY_FOG','GREEDY_FB') else '')
        rows.append([net,label,num(v['priority_handled_pct']['mean']),num(v['urgent_served_pct']['mean']),num(v['travel_min']['mean']),num(v['quarantines']['mean'])])
        cost.append([net,label,num(v['bytes_sent']['mean']/48000),num(v['center_p95_ms']['mean']),num(v['agent_p95_ms']['mean'])])
    tex.append(table("C1 secondary means over 40 seed-level bundle averages. PWH includes absences; urgent PWS requires completed present-patient service. Travel is minutes per shift; quarantines per shift. $^*$Exploratory comparator.",["Net","Controller","PWH","Urgent PWS","Travel","Quar."],rows,"lp{4cm}rrrr"))
    tex.append(table("C1 payload and reference-host diagnostics, same averaging; kB/vehicle-hour and mean run-level p95 times (ms). $^*$Exploratory comparator. Host-dependent timings are descriptive, not paired causal effects.",["Net","Controller","kB/veh-h","Coord. p95","Agent p95"],cost,"lp{4cm}rrr"))
    rows=[]
    for key,v in c['outage_sensitivity'].items():
        _,start,duration,m=key.split('|');rows.append([start,duration,LABEL[m]+("$^*$" if m=='GREEDY_FOG' else ''),ci(v)])
    for key,v in c['exploratory_fallback_contrasts'].items():
        start,duration,name=key.split('|')
        if name=='greedy_fb_minus_cloud': rows.append([start,duration,"Greedy-FB$^*$",ci(v)])
    rows.sort(key=outage_row_key)
    tex.append(table(r"All C2 onset/duration conditions plus C1 reference: PWS differences from Central (pp), unadjusted 95\% paired t intervals, first 20 seeds in stress. $^*$Exploratory comparator; all-day Greedy@fog is not an outage-only intervention.",["Onset","Duration","Comparator",r"Effect [95\% CI]"],rows,"llp{4.5cm}l"))
    rows=[]
    names={'hybrid_minus_greedy_fb':'Hybrid--Greedy-FB','hybrid_minus_fog':'Hybrid--Central@fog','greedy_fb_minus_fog':'Greedy-FB--Central@fog'}
    for key,v in c['exploratory_fallback_contrasts'].items():
        start,duration,name=key.split('|')
        if name in names: rows.append([start,duration,names[name],ci(v)])
    rows.sort(key=lambda row: (int(row[0]), int(row[1]), row[2]))
    tex.append(table(r"Exploratory direct fallback contrasts (PWS pp), same 20 stress seeds and unadjusted 95\% t intervals. Policies differ in connected outage coordination; these do not isolate auction messaging from local market planning.",["Onset","Duration","Contrast",r"Effect [95\% CI]"],rows,"llp{5.7cm}l"))
    rows=[[it,net,LABEL[m],ci(v)] for key,v in sorted(c['budget_sensitivity'].items(), key=budget_key) for it,net,m in [key.split('|')]]
    tex.append(table(r"C3 optimiser-budget sensitivity (PWS pp vs Central). 5/100 iterations are C3; 30 is the C1 reference; 20 stress seeds and unadjusted 95\% t intervals.",["Iterations","Net","Comparator",r"Effect [95\% CI]"],rows,"llp{4cm}l"))
    diag=list(csv.DictReader((ROOT/'campaigns/sensitivity/hybrid_diagnostics.csv').open()))
    rows=[]
    for duration in (30,60,120,180):
        group=[r for r in diag if r['thermal']=='1' and int(r['seed'])<=9020 and r['outage_start']=='120' and int(r['outage_duration'])==duration]
        rows.append([duration]+[num(statistics.mean(int(r[k]) for r in group)) for k in ('releases_before','releases_during','releases_after','market_dropped','recovery_revokes_first_10min')])
    tex.append(table("Exploratory Hybrid switching diagnostics: means per shift, 20 stress seeds, onset 120. Release phases are before/during/after outage; dropped includes all market-drop releases; recovery counts revoke attempts in ten minutes, not unique transfers. No causal mediation is claimed.",["Duration","Before","During","After","Dropped","Revokes"],rows,"rrrrrr"))
    return '\n\n'.join(tex)

def figure():
    c=json.loads((ROOT/'campaigns/confirmatory/results/analysis.json').read_text())
    out=[r'\begin{figure}[htbp]\centering\begin{tikzpicture}',
         r'\begin{groupplot}[group style={group size=1 by 2,vertical sep=1.8cm},width=0.85\textwidth,height=5cm,ymajorgrids,legend style={font=\scriptsize,at={(0.02,0.98)},anchor=north west},tick label style={font=\small}]',
         r'\nextgroupplot[xlabel={Outage duration (min)},ylabel={PWS vs Central (pp)},xtick={30,60,120,180},xmin=15,xmax=195]']
    for method,color,mark,offset in [('CENTRAL_FOG','blue!70!black','*',-3),('HYBRID','red!70!black','triangle*',0),('GREEDY_FB','black','square*',3)]:
        coords=[]
        for d in (30,60,120,180):
            v=c['exploratory_fallback_contrasts'][f'120|{d}|greedy_fb_minus_cloud'] if method=='GREEDY_FB' else c['outage_sensitivity'][f'outage|120|{d}|{method}']
            coords.append(f"({d+offset},{v['mean']:.5f}) += (0,{v['hi']-v['mean']:.5f}) -= (0,{v['mean']-v['lo']:.5f})")
        out.append(r'\addplot+[only marks,'+color+',mark='+mark+r',error bars/.cd,y dir=both,y explicit] coordinates {'+' '.join(coords)+r'};\addlegendentry{'+LABEL[method]+(' (exploratory)' if method=='GREEDY_FB' else '')+'}')
    out.append(r'\nextgroupplot[xlabel={Outage duration (min)},ylabel={Hybrid vs fog ALNS (pp)},xtick={30,60,120,180},xmin=15,xmax=195,ymin=-5,ymax=1.8]')
    out.append(r'\fill[gray!15] (axis cs:15,-1.5) rectangle (axis cs:195,1.5);\draw[dashed] (axis cs:15,-1.5)--(axis cs:195,-1.5);\draw[dashed] (axis cs:15,1.5)--(axis cs:195,1.5);\draw[gray] (axis cs:15,0)--(axis cs:195,0);')
    coords=[]
    for d in (30,60,120,180):
        v=c['exploratory_fallback_contrasts'][f'120|{d}|hybrid_minus_fog']
        coords.append(f"({d},{v['mean']:.5f}) += (0,{v['hi']-v['mean']:.5f}) -= (0,{v['mean']-v['lo']:.5f})")
    out.append(r'\addplot+[only marks,black,mark=diamond*,error bars/.cd,y dir=both,y explicit] coordinates {'+' '.join(coords)+'};')
    out.append(r'\end{groupplot}\end{tikzpicture}\caption{Stress-bundle outage contrasts at onset minute 120 (20 paired seeds, marginal 95\% t intervals). Top: fog-ALNS, Hybrid and outage-only Greedy-FB versus cloud control. Bottom: Hybrid minus Central@fog; dashed limits are the $\pm1.5$ pp engineering margin. The displayed 95\% intervals are not TOST intervals. Full onset sensitivities are in the Supplementary Material.}\label{fig:outage}\end{figure}')
    return '\n'.join(out)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'supplementary_tables.tex').write_text(build()+'\n')
    (args.out/'outage_figure.tex').write_text(figure()+'\n')
    print('Generated complete supplementary tables and outage figure')
