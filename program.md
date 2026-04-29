# Research Program: Global Functional Resynthesis

## Current Status
- Average reduction ratio: 0.7312 (26.9% improvement over structural baselines)
- vs ABC resyn2: -66.6% (11 wins, 0 losses)
- Total gates: 328 / 454 baseline
- 100% correctness across all 20 benchmarks
- Runtime: ~800s for full benchmark suite

## Active Methods
1. Shannon decomposition with variable order search
2. PPRM (XOR-based) decomposition
3. SOP synthesis with prime implicants (n <= 8)
4. SAT-based exact synthesis (n <= 5)
5. Functional decomposition (dependency-aware multi-output)
6. Iterative improvement (multiple strategies)
7. Structural templates (CLA adder, ripple-carry adder, array multiplier, comparator)
8. ABC read_truth synthesis (per-output)
9. ABC polish (resyn2/resyn2rs/resyn2rs_x2/compress2/dc2/dch_resyn, independent + iterative)

## Known Floors (confirmed via exhaustive testing)
- xor3: 6 gates is optimal in AIG (not 4 as previously assumed)
- parity8: 21 gates is optimal (balanced XOR tree, ABC confirms)
- full_adder: 7 gates is optimal
- add2: 10 gates is optimal (CLA structure)
- add4: 24 gates (CLA + ABC floor)
- add8: 52 gates (CLA + ABC floor)
- mul4x4: 83 gates (array multiplier + resyn2rs floor)
- mul3x3: 37 gates (array multiplier + ABC floor)

## Priorities
1. Multi-output SAT-based exact synthesis (shared gates across outputs)
2. Deeper e-graph integration with better multi-output extraction
3. Find alternative circuit topologies that give ABC different optimization paths
4. Consider LUT-based decomposition for 8+ input functions

## Constraints
- benchmark.py and prepare.py are immutable
- Every solver output must pass exhaustive truth table verification
- Only keep changes that improve avg_reduction_ratio
