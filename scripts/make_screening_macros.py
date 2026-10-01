"""Regenerate the current PWH-labelled screening macros from archived evidence."""
from __future__ import annotations
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aedge.make_tex import build, totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    results = ROOT / 'campaigns/screening/results'
    analysis = json.loads((results / 'analysis/analysis.json').read_text())
    ver = {key: json.loads((results / 'verification' / filename).read_text())
           for key, filename in [('t1', 't1_column_arc.json'),
                                 ('t2', 't2_thermal_bound.json'),
                                 ('t3', 't3_token_protocol.json')]}
    counts = {}
    for stage in ('S1', 'S2', 'S3', 'S4', 'S5'):
        counts[stage] = sum(sum(1 for _ in csv.DictReader(p.open()))
                            for p in results.glob(f'runs_{stage}_*.csv'))
    extra = totals(results)
    for tag, filename in [('host', 'repeat_check_host.json'),
                          ('plat', 'repeat_check_platform2.json')]:
        repeat = json.loads((results / filename).read_text())
        extra[f'rep-{tag}'] = f"{repeat['equal']}/{repeat['checked']}"
    block = build(analysis, ver, counts, extra) + '\n'
    (results / 'analysis/generated_macros.tex').write_text(block)
    args.out.write_text(block)
    print(f'Screening macros regenerated: {sum(counts.values())} rows')


if __name__ == '__main__':
    main()
