# Methodology

Hard gates have four states: `PASS`, `FAIL`, `UNKNOWN`, and `WAIVED`. Only `PASS` and a waiver with an explicit reason proceed. Missing evidence, insufficient gate confidence, and prohibited source quality produce a blocking `UNKNOWN` rather than an estimate.

Eligible towns are compared per metric. Values are winsorized at the fifth and ninety-fifth percentiles, normalized to 0–10 with declared directionality, averaged within criteria, penalized for missing non-critical metrics, and weighted with a configuration that must total 100. Ties are resolved by stable place ID ordering.

Sensitivity uses at least 1,000 seeded simulations. Each weight is independently varied from 75% to 125% of its configured value, then all weights are renormalized to 100. Reports include top-three frequency, mean rank, rank variance, and a visible fragility label.

