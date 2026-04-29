# Session Handoff State

## Timestamp
2026-04-29T09:30:00Z

## Work Context
- **Goal**: Improve the Global Functional Resynthesis solver's avg_reduction_ratio
- **Starting point**: avg ratio 0.7355 (334 gates / 454 baseline), 100% correctness
- **Approach**: Adding structural templates, improving ABC polish scripts, fixing SOP blowup

## Progress
- [x] Killed stuck SOP process (PID 122012) from previous session
- [x] Created GitHub repo and pushed: https://github.com/themoddedcube/global-functional-resynthesis
- [x] Analyzed per-benchmark performance and method winners
- [x] Added comparator templates (_build_ripple_comparator_gt, _build_ripple_comparator_lt)
- [x] Tightened SOP guard from n<=12 to n<=8 to prevent exponential blowup
- [x] Made structural templates work for single-output functions (comparators)
- [x] Added new ABC scripts: resyn2rs_x2, dc2, dch_resyn
- [x] CLA adder template was already present (from old session)
- [ ] Evaluation running with all improvements
- [ ] Need to commit and push improvements
- [ ] Consider further improvements: Dadda multiplier, e-graph multi-output extraction

## Key Decisions
- **Comparator input ordering**: Benchmark uses MSB-first within each operand (a[MSB..LSB], b[MSB..LSB])
- **SOP limit**: Reduced to 8 inputs (was 12) — prime implicant computation is O(n * 2^n choose 2) and blows up past 8
- **ABC scripts**: `resyn2rs_x2` (running resyn2rs twice) gives significant improvement on multipliers (mul4x4: 87→83)
- **parity8**: 21 gates is provably optimal — even ABC can't improve it

## Active Processes
- None (killed stuck process)

## Git State
- Branch: master
- Remote: origin -> https://github.com/themoddedcube/global-functional-resynthesis
- Working tree: modified (solver.py, theories/abc_polish.py)

## Next Steps
1. Check evaluation results
2. Run autoresearch.py to log the improvement
3. Commit and push the changes
4. Try Dadda/Wallace tree multiplier for mul4x4
5. Improve e-graph multi-output extraction
6. Try generating multiple diverse starting points for ABC
