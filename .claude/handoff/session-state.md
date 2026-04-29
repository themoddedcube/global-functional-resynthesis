# Session Handoff State

## Timestamp
2026-04-29T21:30:00Z

## Work Context
- **Goal**: Improve the Global Functional Resynthesis solver's avg_reduction_ratio AND optimize user's 63-gate FP4 multiplier
- **Benchmark result**: avg ratio 0.7312 (328 gates / 454 baseline), -66.6% vs ABC
- **All 100% correct**

## Progress This Session
- [x] Fixed e-graph implementation (3 bugs: cost propagation, cycle detection, multi-output sharing)
- [x] Added BLIF I/O support (theories/blif_io.py)
- [x] Added CLI optimizer (optimize.py) for external circuits
- [x] Integrated e-graph as Method 9 in solver
- [x] Ingested user's 63-gate FP4 multiplier circuit
- [x] Analyzed FP4 multiplier mathematically (circuits/fp4_analysis.md)
- [x] ABC polish on FP4: 102 AIG → 85 AIG gates (but 87 mixed gates, worse than 63)
- [ ] SAT-based mixed-gate exact synthesis (agent running)
- [ ] Mixed-gate cut rewriting engine (agent running)

## Key Findings - FP4 Multiplier
- Computes sigma(a) * sigma(b) where sigma is FP4 E2M1 Gray-coded decode
- Commutative: f(a,b) = f(b,a)
- Sign = a0 XOR b0 (1 gate), separable from magnitude
- Every unsigned product has AT MOST 2 bits set (key structural property)
- Architecture: 41 gates (unsigned magnitude) + 1 gate (sign) + 21 gates (conditional negate)
- y0 depends on only 4 inputs, y7 XOR y8 has only 4 minterms
- Theoretical lower bound: ~50-55 mixed gates
- ABC cannot beat 63 mixed gates (best: 77 with tech mapping, 87 with restricted lib)

## Git State
- Branch: master
- Remote: origin -> https://github.com/themoddedcube/global-functional-resynthesis
- Latest commit: ed92cc3

## Next Steps
1. Collect results from SAT and cut-rewriting agents
2. Try "joint sign-magnitude synthesis" (Approach C from analysis)
3. Try SAT-based resynthesis of the magnitude sub-circuit (6 inputs, ~10 outputs)
4. Implement mixed-gate-aware cost model in e-graph
5. Push results to GitHub
