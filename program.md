# Research Program: Global Functional Resynthesis

## Current Status
- Average reduction ratio: 0.736 (26.4% improvement over structural baselines)
- vs ABC resyn2: -66.0% (11 wins, 0 losses)
- 100% correctness across all 20 benchmarks

## Active Methods
1. Shannon decomposition with variable order search
2. PPRM (XOR-based) decomposition
3. SOP synthesis with prime implicants
4. SAT-based exact synthesis (n <= 5)
5. Functional decomposition (dependency-aware multi-output)
6. Iterative improvement (multiple strategies)
7. Structural templates (ripple-carry adder, array multiplier)
8. ABC read_truth synthesis (per-output)
9. ABC polish (iterative resyn2/resyn2rs/compress2)

## Priorities
1. Improve multi-output sharing for large circuits (add8, mul4x4)
2. Reduce xor3 from 6 to 4 gates (known optimal)
3. Better SOP factoring for medium-size single-output functions
4. E-graph integration for discovering shared sub-expressions

## Constraints
- benchmark.py and prepare.py are immutable
- Every solver output must pass exhaustive truth table verification
- Only keep changes that improve avg_reduction_ratio
