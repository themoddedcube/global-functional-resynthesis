# Global Functional Resynthesis Solver

## What This Is
A solver that rediscovers optimal circuit decompositions globally, going beyond local AIG optimization. Combines e-graphs, SAT-based exact synthesis, information-theoretic decomposition, and simulation-guided search.

## Architecture
- `benchmark.py` - Test circuits, truth tables, correctness checks, metrics (DO NOT MODIFY)
- `prepare.py` - Generate benchmark suite, precompute optimal solutions (DO NOT MODIFY)
- `solver.py` - The resynthesis solver (THIS IS WHAT GETS ITERATED ON)
- `autoresearch.py` - Autoresearch ratchet loop
- `report.py` - Benchmark comparison dashboard
- `tests/` - Test suite

## Key Data Structures
- `TruthTable` - Python big-int bitmasks (bit i of table[j] = output j when input = i)
- `Circuit` / `AIGNode` - AIGER convention (positive IDs, negation via sign, node 0 = const-false)

## Running
```bash
python3 prepare.py          # Generate benchmarks (once)
python3 solver.py            # Run solver on all benchmarks
python3 report.py            # Generate comparison table
python3 -m pytest tests/     # Run tests
```

## Evaluation Metric
Primary: `reduction_ratio = solver_gates / baseline_gates` (lower = better)
Hard constraint: 100% correctness (exhaustive truth table verification)
