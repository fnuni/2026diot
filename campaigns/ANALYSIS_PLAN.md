# Analysis structure

This document describes the analyses reported in the manuscript. It is not an external preregistration or an independently timestamped protocol.

## Screening campaign

S1 uses 40 seeds, each averaged over four traffic–thermal environments before inference.

The primary superiority family is H1 (Hybrid–Central, N4), H2 (Hybrid–Central, N3), H3 (Central–Periodic, N1) and H4 (Hybrid–Edge-market, N1), with paired sign-flip tests and Holm correction within this family. E1 and E2 are interval-based equivalence comparisons (machine keys Q1 and Q2).

S2 scale/load, S3 connectivity and S5 thermal misspecification are exploratory. S4 is a reproducibility check. Served-only screening sensitivity is exploratory and differs from completion-matched PWS.

## Completed-service campaign

C1 has 1,920 evaluations: 40 seeds × two thermal bundles × three network profiles × eight controllers. The first four factorial controllers identify placement, fallback and their interaction. Hybrid, all-day Greedy@fog, Static and outage-only Greedy-FB are policy comparators.

The primary superiority family is F1 (placement, N4), F2 (fallback, N3) and F3 (interaction, N4), with paired sign-flip tests and Holm correction. The pooled E3 equivalence comparison (machine key Q5) uses a paired t-based TOST and a ±1.5-point engineering margin.

Additional policy contrasts, weak-policy placement, thermal-bundle equivalence, C2 outage timing, C3 optimiser budget and switching/reflex diagnostics are exploratory. They lie outside the primary hypothesis families, reuse campaign seeds and are not independent prospective confirmation. Their intervals are unadjusted.

C2 has 500 evaluations (20 stress seeds × five outage windows × five controllers); C3 has 240 (20 stress seeds × two network profiles × two alternative budgets × three controllers). Total C1–C3: 2,660.

## Interpretation

Seed-level averages precede inference. Controller–scenario evaluations are not independent samples. The sample size is computationally fixed, not power-justified. Campaigns use distinct seeds, endpoints and environmental averaging and are not pooled. A non-significant superiority test is not evidence of equivalence.
