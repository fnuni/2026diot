# Screening experiment

The campaign uses seeds 8001–8040, with development seeds 7001–7020 excluded from inference. All controllers share scenario streams, morning plans, information rules, ownership tokens and execution guards.

| Stage | Design | Evaluations |
|---|---|---:|
| S1 | 40 seeds × four traffic–thermal cells × N0–N4 × eight controllers | 6,400 |
| S2 | 20 seeds × four load/fleet cells × N1/N3/N4 × eight controllers | 1,920 |
| S3 | 20 seeds × five availability levels × five outage means × three controllers | 1,500 |
| S4 | Six seeds × two controllers, multiprocess replay | 12 |
| S5 | 20 seeds × three true-conductance factors × N1/N3 × three controllers | 360 |
| Total | | 10,192 |

PWH counts priority-weighted on-time visit starts, including absent-patient attempts. It is dispatch handling, not completed treatment. Seed-level paired contrasts are averaged over environmental cells before inference.

H1–H4 are the primary superiority family; E1/E2 are the screening equivalence comparisons. Scale, connectivity, thermal misspecification and served-only sensitivities are exploratory. Full definitions and multiplicity rules are in ../ANALYSIS_PLAN.md.

This is a description of the reported design, not an externally registered protocol. No outcome-based exclusions or optional stopping are used. SHA-256 checks identify the distributed evidence and are not trusted timestamps.
