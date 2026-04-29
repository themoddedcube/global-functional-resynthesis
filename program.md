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

## Known Floors (confirmed via SAT proofs and exhaustive testing)
### Proven optimal via multi-output SAT exact synthesis:
- and3: 2 gates (trivial)
- or3: 2 gates (trivial)
- mul2x1: 2 gates (trivial)
- half_adder: 3 gates (XOR + AND sharing)
- mux2: 3 gates
- maj3: 4 gates (SAT-proved, no 3-gate solution exists)
- cmp2: 5 gates (SAT-proved)
- priority4: 5 gates (SAT-proved, 4-gate UNSAT)
- xor3: 6 gates (SAT-proved, not 4 as previously assumed)
- full_adder: 7 gates (SAT-proved multi-output, 6-gate UNSAT)
- mul2x2: 8 gates (SAT-proved multi-output, 7-gate UNSAT)
- add2: 10 gates (SAT-proved multi-output, 9-gate UNSAT)
- decode3to8: 12 gates (structural argument: 4 shared pairs + 8 outputs)
- parity8: 21 gates (balanced XOR tree, ABC confirms)

### ABC optimization floors (not proven optimal, but no algorithm finds better):
- add4: 24 gates (CLA + ABC, multiple starting topologies converge)
- add8: 52 gates (CLA + ABC, Brent-Kung also converges to 52)
- mul3x3: 37 gates (array multiplier + ABC)
- mul4x4: 83 gates (array multiplier + resyn2rs, 50 random topologies tested)
- cmp4: 13 gates (ABC from multiple starting points)
- cmp8: 29 gates (ABC from multiple starting points)

## Priorities
1. Multi-output SAT-based exact synthesis (shared gates across outputs)
2. Deeper e-graph integration with better multi-output extraction
3. Find alternative circuit topologies that give ABC different optimization paths
4. Consider LUT-based decomposition for 8+ input functions

## Constraints
- benchmark.py and prepare.py are immutable
- Every solver output must pass exhaustive truth table verification
- Only keep changes that improve avg_reduction_ratio
