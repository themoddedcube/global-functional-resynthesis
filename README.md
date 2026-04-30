# Global Functional Resynthesis

A circuit optimization solver that discovers globally optimal decompositions for Boolean functions, going beyond the local rewriting techniques used by conventional EDA tools. On a benchmark suite of 20 circuits, the solver achieves a **28.4% average gate reduction** over structural baselines and **66.9% fewer total gates** than ABC's `resyn2` — with 11 wins, 0 losses against ABC.

## Results

| Benchmark   | In | Out | Baseline | ABC | Ours | vs ABC | vs Baseline |
|-------------|---:|----:|---------:|----:|-----:|-------:|------------:|
| and3        |  3 |   1 |        2 |   2 |    2 |     0% |          0% |
| or3         |  3 |   1 |       20 |   2 |    2 |     0% |        -90% |
| xor3        |  3 |   1 |       11 |   6 |    6 |     0% |        -45% |
| mux2        |  3 |   1 |        3 |   3 |    3 |     0% |          0% |
| maj3        |  3 |   1 |        5 |   4 |    4 |     0% |        -20% |
| half_adder  |  2 |   2 |        4 |   4 |    3 |   -25% |        -25% |
| full_adder  |  3 |   2 |        9 |  10 |    7 |   -30% |        -22% |
| add2        |  4 |   3 |       13 |  15 |   10 |   -33% |        -23% |
| cmp2        |  4 |   1 |       10 |   5 |    5 |     0% |        -50% |
| mul2x1      |  3 |   3 |        2 |   2 |    2 |     0% |          0% |
| add4        |  8 |   5 |       31 |  49 |   24 |   -51% |        -23% |
| mul2x2      |  4 |   4 |       12 |  12 |    8 |   -33% |        -33% |
| cmp4        |  8 |   1 |       22 |  13 |   13 |     0% |        -41% |
| parity8     |  8 |   1 |       21 |  21 |   21 |     0% |          0% |
| decode3to8  |  3 |   8 |       16 |  16 |   12 |   -25% |        -25% |
| priority4   |  4 |   3 |        8 |   6 |    5 |   -17% |        -38% |
| mul3x3      |  6 |   6 |       48 |  66 |   36 |   -45% |        -25% |
| add8        | 16 |   9 |       67 | 320 |   52 |   -84% |        -22% |
| mul4x4      |  8 |   8 |      104 | 389 |   81 |   -79% |        -22% |
| cmp8        | 16 |   1 |       46 |  38 |   29 |   -24% |        -37% |
| **Total**   |    |     |  **454** | **983** | **325** | **-66.9%** | **-28.4%** |

Gate counts are AND gates in an And-Inverter Graph (AIG). Inversions are free. All results verified by exhaustive truth table simulation.

## Why This Exists

Tools like ABC optimize circuits through local rewriting — replacing small subcircuits with cheaper equivalents. This works well but gets stuck in local optima. A 25-gate circuit might reduce to 22 gates through `resyn2`, but the globally optimal solution might be 7 gates using an entirely different topology.

This solver attacks the problem from the other direction: start from the function's truth table and explore the full space of possible implementations using SAT solving, structural templates, e-graph equality saturation, and information-theoretic decomposition. ABC's local optimization is then applied as a final polish on the best global candidate.

## Quick Start

```bash
git clone https://github.com/themoddedcube/global-functional-resynthesis.git
cd global-functional-resynthesis

pip install -r requirements.txt   # python-sat, numpy, networkx
pip install rich                  # for TUI display

python3 prepare.py                # generate benchmark suite (once)
python3 cli.py run                # run solver on all benchmarks
```

## CLI Usage

```bash
# Run all benchmarks with TUI progress and results table
python3 cli.py run

# Run a specific benchmark with verbose output
python3 cli.py run -b mul4x4 -v

# Solve a custom single-output truth table (hex)
python3 cli.py solve e8 -n 3          # MAJ3: 3 inputs, truth table 0xe8

# Solve a multi-output function
python3 cli.py solve 96 e8 -n 3       # full adder: XOR + MAJ

# List available benchmarks
python3 cli.py list

# Show details for a specific benchmark
python3 cli.py info mul3x3
```

You can also run the solver directly:

```bash
python3 solver.py                      # plain text output, no TUI
python3 report.py                      # comparison dashboard vs ABC
python3 -m pytest tests/               # test suite
```

## How It Works

The solver runs multiple synthesis strategies in parallel, keeps the best candidate from each, then polishes the top results with ABC. Every output is verified against the original truth table.

### Synthesis Methods

| Method | Description | Best For |
|--------|-------------|----------|
| **SAT Exact Synthesis** | Encodes "does a k-gate AIG exist?" as SAT; finds provably optimal circuits | Small functions (up to 5 inputs) |
| **Shannon Decomposition** | Recursive cofactor splitting with variable ordering heuristics | General purpose, any size |
| **Structural Templates** | Recognizes adders, multipliers, comparators and builds known-optimal architectures | Arithmetic circuits |
| **E-Graph Saturation** | Discovers shared substructure across outputs via equality saturation | Multi-output functions |
| **Functional Decomposition** | Groups outputs by dependency and synthesizes shared subfunctions | Multi-output with input overlap |
| **PPRM Decomposition** | Positive Polarity Reed-Muller (XOR-based) synthesis | Functions with XOR structure |
| **SOP Synthesis** | Sum-of-products with prime implicant extraction | Small-to-medium functions |
| **ABC Polish** | DAG-aware rewriting via ABC's `dch`, `resyn2`, `resub` scripts | Final optimization pass |
| **Cut-Based Rewriting** | Enumerate 4-input cuts, replace cones with optimal implementations | Post-processing cleanup |

### Pipeline

```
Truth Table
    |
    v
[Shannon] [PPRM] [SOP] [Exact SAT] [Templates] [E-Graph] [FuncDec] [ABC Synth]
    |        |      |       |           |           |          |          |
    v        v      v       v           v           v          v          v
                    Candidate Pool (verified for correctness)
                              |
                              v
                    Sort by gate count, take top 3
                              |
                              v
                    ABC Polish (resyn2, dch, resub)
                              |
                              v
                    Cut-Based AIG Rewriting
                              |
                              v
                        Best Circuit
```

## Proven Optimality

For 14 of 20 benchmarks, we have proven (via multi-output SAT or structural arguments) that our results are optimal — no AIG with fewer AND gates exists:

| Circuit | Gates | Proof |
|---------|------:|-------|
| and3 | 2 | Trivial |
| or3 | 2 | Trivial |
| mul2x1 | 2 | Trivial |
| half_adder | 3 | SAT: 2-gate UNSAT |
| mux2 | 3 | SAT: 2-gate UNSAT |
| maj3 | 4 | SAT: 3-gate UNSAT |
| cmp2 | 5 | SAT: 4-gate UNSAT |
| priority4 | 5 | SAT: 4-gate UNSAT |
| xor3 | 6 | SAT: 5-gate UNSAT |
| full_adder | 7 | Multi-output SAT: 6-gate UNSAT |
| mul2x2 | 8 | Multi-output SAT: 7-gate UNSAT |
| add2 | 10 | Multi-output SAT: 9-gate UNSAT |
| decode3to8 | 12 | Structural: 4 shared pairs + 8 minterms |
| parity8 | 21 | XOR tree lower bound + ABC confirmation |

## Project Structure

```
solver.py            # Multi-method resynthesis solver
cli.py               # Rich TUI command-line interface
benchmark.py         # Truth tables, circuits, evaluation framework
prepare.py           # Benchmark suite generator
report.py            # Comparison dashboard
autoresearch.py      # Ratchet loop: only keeps improvements
theories/
  abc_polish.py      # ABC integration (AIGER I/O, optimization scripts)
  aig_opt.py         # AIG rewriting and balancing
  egraph.py          # E-graph equality saturation
  mi_decomp.py       # Mutual information decomposition
  mixed_sat.py       # SAT with mixed gate types
  mixed_rewrite.py   # Mixed-gate rewriting rules
  var_order_search.py  # Variable ordering heuristics
  blif_io.py         # BLIF format I/O
  progressive.py     # Progressive synthesis
tests/               # Test suite
benchmarks.json      # Generated benchmark data
```

## Key Concepts

**AIG (And-Inverter Graph):** Circuits built from AND gates and inversions. OR is one AND gate (De Morgan), XOR is three, NOT is free. Gate count = number of AND nodes.

**Truth Table:** A function's complete input-output mapping stored as Python big-int bitmasks. Bit `i` of `table[j]` is output `j` when the input pattern is `i`.

**Structural Hashing:** The `AIGBuilder` class automatically deduplicates: if `AND(a, b)` already exists, it returns the existing node instead of creating a duplicate. This is critical for multi-output sharing.

**CEGIS (Counter-Example Guided Inductive Synthesis):** The SAT solver encodes circuit structure as Boolean variables and uses counterexamples to iteratively prove or disprove that a k-gate implementation exists.

## Requirements

- Python 3.10+
- [python-sat](https://github.com/pysathq/pysat) (SAT solving via CaDiCaL)
- [NumPy](https://numpy.org/) (vectorized simulation)
- [NetworkX](https://networkx.org/) (e-graph construction)
- [Rich](https://github.com/Textualize/rich) (TUI display, optional)
- [ABC](https://github.com/berkeley-abc/abc) at `/tmp/abc/abc` (optional, for polish phase)

## Building ABC (Optional)

ABC provides the final polish pass. Without it, the solver still works but may produce slightly larger circuits.

```bash
cd /tmp
git clone https://github.com/berkeley-abc/abc.git
cd abc
make -j$(nproc)
```

## License

MIT
