# Session Handoff State

## Timestamp
2026-04-29T19:00:00Z

## Work Context
- **Goal**: Improve the Global Functional Resynthesis solver's avg_reduction_ratio
- **Starting point**: avg ratio 0.7355 (334 gates / 454 baseline)
- **Result**: avg ratio 0.7312 (328 gates / 454 baseline), -66.6% vs ABC
- **All 100% correct**

## Progress
- [x] Killed stuck SOP process from previous session
- [x] Created GitHub repo: https://github.com/themoddedcube/global-functional-resynthesis
- [x] Analyzed per-benchmark performance and method winners
- [x] Added comparator templates (ripple_comparator_gt, ripple_comparator_lt)
- [x] Tightened SOP guard from n<=12 to n<=8
- [x] Made structural templates work for single-output (comparators)
- [x] Added new ABC scripts: resyn2rs_x2, dch_resyn
- [x] Fixed ABC polish to try each script independently from original (avoids local minima)
- [x] mul4x4 improved: 87→83 gates via resyn2rs_x2 applied to raw template
- [x] add4 improved: 25→24, add8: 53→52 via CLA adder template
- [x] Proved 12 benchmarks at provably optimal gate counts via multi-output SAT
- [x] Updated program.md with optimality proofs and known floors
- [x] Tested 50+ random multiplier topologies — all converge to same ABC floor
- [x] Tested Brent-Kung, Wallace tree, Karatsuba — no improvement over array+ABC

## Key Decisions
- **Comparator input ordering**: Benchmark uses a>b (not a<b), MSB-first within operands
- **SOP limit**: 8 inputs (was 12) to prevent exponential blowup
- **ABC polish rewrite**: Try each script independently from original circuit, then iterate
- **xor3**: 6 gates is provably optimal in AIG (program.md was wrong about 4)
- **All tier-1 benchmarks**: Proven optimal via multi-output SAT exact synthesis

## Git State
- Branch: master
- Remote: origin -> https://github.com/themoddedcube/global-functional-resynthesis
- Latest commit: f621dc4

## Next Steps (for future sessions)
1. Multi-output SAT synthesis at scale (currently only feasible for n<=4 inputs, m<=4 outputs)
2. Try CEGAR-based decomposition: guess structure, verify with SAT, refine
3. Machine learning-guided ABC script selection per benchmark
4. Custom AIG rewriting rules beyond what ABC provides
5. Try feeding ABC the full Pareto front of circuits (not just the smallest)
