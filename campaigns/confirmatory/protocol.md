# Completed-service experiment

The campaign uses seeds 9001–9040, distinct from screening. The primary endpoint PWS counts priority-weighted present-patient services started on time and matched to a completion event. Absent, missed and unfinished requests remain in the denominator.

| Stage | Design | Evaluations |
|---|---|---:|
| C1 | 40 seeds × benign/stress bundles × N1/N3/N4 × eight controllers | 1,920 |
| C2 | 20 stress seeds × five outage onset/duration windows × five controllers | 500 |
| C3 | 20 stress seeds × N1/N4 × 5/100 event-ALNS iterations × three controllers | 240 |
| Total | | 2,660 |

C1 controllers: CENTRAL, CENTRAL_FB, CENTRAL_FOG, CENTRAL_FOG_FB, HYBRID, GREEDY_FOG, STATIC, GREEDY_FB. C2 omits vehicle-factorial and Static controllers; C3 uses Central, Central@fog and Hybrid.

The first four C1 controllers form the placement × fallback factorial. F1–F3 are the primary superiority family and pooled E3 (machine key Q5) is the primary TOST comparison. Additional policy comparisons, thermal-bundle equivalence, C2/C3 and replay diagnostics are exploratory, reuse these seeds and are not independent replications.

GREEDY_FB uses global cloud ALNS while reachable, greedy insertion at the fog only during outages and global repair on recovery. GREEDY_FOG instead uses insertion throughout the day; Greedy@fog–Static isolates its placement effect.

See ../ANALYSIS_PLAN.md for estimands and multiplicity. This document describes the reported experiment and does not claim external preregistration.
