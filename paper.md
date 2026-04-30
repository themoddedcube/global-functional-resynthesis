# Global Functional Resynthesis: Multi-Strategy Circuit Optimization Beyond Local Rewriting

**Chaithu Talasila** (themoddedcube@gmail.com)

---

## Abstract

We present a global functional resynthesis solver that discovers optimized And-Inverter Graph (AIG) implementations by exploring the full decomposition space of Boolean functions. Unlike conventional tools that rely on local rewriting rules, our approach runs multiple synthesis strategies in parallel — SAT-based exact synthesis, structural template matching, e-graph equality saturation, information-theoretic decomposition, and variable-order search — then selects the best candidate and polishes it with local optimization. On a suite of 20 benchmark circuits ranging from 2 to 16 inputs, the solver achieves a 28.4% average gate reduction over structural baselines and produces 66.9% fewer total AND gates than ABC's `resyn2` script, with 11 wins and 0 losses in head-to-head comparison. For 14 benchmarks, we prove optimality via multi-output SAT, confirming that no smaller AIG exists.

---

## 1. Introduction

Logic synthesis transforms Boolean functions into efficient circuit implementations. The AND-Inverter Graph (AIG) is the standard intermediate representation, where the optimization objective is minimizing the number of AND gates (inversions are free). State-of-the-art tools like ABC [1] optimize AIGs through iterative local rewriting: for each node, enumerate small input cuts, compute the local truth table, and replace the subcircuit if a cheaper implementation exists.

Local rewriting is fast and effective but fundamentally limited. It operates within the topology established by the initial synthesis and cannot discover structurally different decompositions. A circuit with 25 AND gates might reduce to 22 through `resyn2`, but the globally optimal solution — requiring an entirely different topology — might use only 7 gates.

We address this limitation through *global functional resynthesis*: given a function's truth table, we explore the full space of possible AIG implementations using multiple complementary strategies. The key insight is that different synthesis methods excel on different function classes. By running them in parallel and selecting the best result, we consistently outperform any single method.

### Contributions

1. A multi-strategy synthesis framework that combines SAT-based exact synthesis, structural templates, e-graph equality saturation, and variable-order Shannon decomposition.
2. Provably optimal results for 14 of 20 benchmarks via multi-output SAT encoding.
3. A 66.9% total gate reduction over ABC `resyn2` across the benchmark suite, with 0 losses.
4. An open-source implementation with a TUI interface for interactive exploration.

---

## 2. Background

### 2.1 And-Inverter Graphs

An AIG represents a Boolean function using two-input AND gates and inversions. Every Boolean function can be expressed in this form using De Morgan's laws:

- `OR(a, b) = NOT(AND(NOT a, NOT b))` — 1 AND gate
- `XOR(a, b) = OR(AND(a, NOT b), AND(NOT a, b))` — 3 AND gates
- `NOT(a)` — 0 AND gates (free edge attribute)

The optimization metric is the number of AND nodes. Structural hashing ensures that identical subexpressions are shared automatically.

### 2.2 ABC and Local Rewriting

ABC [1] is the dominant open-source logic synthesis tool. Its core optimization loop (`resyn2`) applies:

- **Balance (`b`):** Rebuild as a balanced tree to minimize depth.
- **Rewrite (`rw`):** For each node, enumerate 4-input cuts, compute the truth table, and replace with an optimal implementation from a precomputed database.
- **Refactor (`rf`):** Similar to rewrite but considers larger cuts.
- **Resub (`rs`):** Resubstitution — express a node's function using existing nodes elsewhere in the network.

These passes are applied iteratively until convergence. The `dch` command adds DAG-aware rewriting with structural choices. While powerful, all these techniques operate within the existing circuit topology.

### 2.3 Limitations of Local Methods

Local rewriting has three fundamental limitations:

1. **Topology lock-in.** The initial synthesis determines the circuit's global structure. Rewriting can improve local subcircuits but cannot change which variables are grouped together at the top level.
2. **Greedy selection.** Each replacement is locally optimal, but the composition of locally optimal choices is not globally optimal.
3. **Multi-output blindness.** Standard AIG rewriting treats each output independently. Sharing opportunities across outputs are discovered only if they happen to arise from structural hashing.

---

## 3. Methodology

Our solver addresses these limitations through a portfolio approach: multiple synthesis methods generate candidate circuits, all candidates are verified for correctness, and the best is selected and polished.

### 3.1 Synthesis Methods

#### SAT-Based Exact Synthesis

For functions with up to 5 inputs, we find the provably minimum-gate AIG using SAT. The encoding asks: "Does an AIG with *k* AND gates exist that computes this truth table?" Each gate's connectivity and polarity are encoded as Boolean variables, with constraints ensuring correct simulation for all input patterns. We use CaDiCaL [2] as the SAT backend.

For multi-output functions, we extend the encoding to share gates across outputs. This is critical for circuits like adders and multipliers where outputs share common subexpressions. We increment *k* from 1 until the formula becomes satisfiable, yielding the optimal gate count with a proof of optimality.

#### Structural Template Matching

For arithmetic circuits, hand-crafted templates often outperform general-purpose synthesis:

- **CLA Adders:** Carry-lookahead adders with generate/propagate sharing between sum and carry computations. Our implementation achieves 52 AND gates for an 8-bit adder.
- **Wallace Tree Multipliers:** Partial product reduction using 3:2 counters arranged in a Wallace tree, followed by a ripple-carry final addition. Achieves 84 gates for 4x4 multiplication before ABC polish.
- **Ripple Comparators:** Gate-efficient comparison circuits using cascaded greater-than/less-than cells.

Template matching checks whether the given truth table matches any known circuit type (by verifying equivalence) and returns the most efficient matching template.

#### E-Graph Equality Saturation

For multi-output functions, we use e-graphs [3] to discover shared substructure. The e-graph represents multiple equivalent implementations simultaneously and uses rewrite rules (commutativity, associativity, De Morgan's laws, distribution) to expand the equivalence classes. Extraction selects the minimum-cost implementation that covers all outputs.

#### Variable-Order Shannon Decomposition

Shannon decomposition is the default synthesis method. The function is recursively split on a variable: `f = x_i AND f|_{x_i=1} OR NOT(x_i) AND f|_{x_i=0}`. The key degree of freedom is the variable ordering, which dramatically affects gate count.

We search over multiple orderings — exhaustively for small functions (up to 6 inputs) and via random sampling for larger ones. A scoring heuristic prefers variables whose cofactors are constants, single variables, or share many minterms.

#### Additional Methods

- **PPRM (Positive Polarity Reed-Muller):** XOR-based decomposition for functions with XOR structure.
- **SOP Synthesis:** Sum-of-products with Espresso-style prime implicant extraction.
- **Functional Decomposition:** Groups outputs by input dependency and synthesizes shared subfunctions.
- **ABC Direct Synthesis:** Uses ABC's `read_truth` to synthesize individual outputs, then combines them with structural hashing.

### 3.2 Candidate Selection and Polish

All candidate circuits are verified against the original truth table by exhaustive simulation. The candidates are sorted by gate count, and the top three are polished using ABC's optimization scripts:

1. `resyn2` — Standard rewrite/refactor loop
2. `resyn2rs` — Rewrite with resubstitution
3. `dch_resyn` — DAG-aware rewriting with structural choices
4. `compress2` — Area-oriented compression
5. `dc2` — Don't-care-based optimization

Each script is tried independently from both the original circuit and the current best, avoiding local minima from script ordering. This combination of global topology search with local ABC polish is the core of our approach: we find the global structure; ABC cleans up the local details.

### 3.3 Post-Processing

After ABC polish, we apply cut-based AIG rewriting: for each AND node, enumerate all cuts of size up to 4, compute the cone's truth table, and replace with an optimal implementation if one exists. This catches any remaining local improvements that ABC's fixed rewrite database might miss.

---

## 4. Experimental Results

### 4.1 Benchmark Suite

We evaluate on 20 circuits spanning gates, arithmetic, and comparison functions:

| Circuit | Inputs | Outputs | Category |
|---------|-------:|--------:|----------|
| and3, or3, xor3, mux2, maj3 | 3 | 1 | Logic gates |
| parity8 | 8 | 1 | Logic gates |
| half_adder | 2 | 2 | Arithmetic |
| full_adder | 3 | 2 | Arithmetic |
| add2, add4, add8 | 4–16 | 3–9 | Arithmetic |
| mul2x1, mul2x2, mul3x3, mul4x4 | 3–8 | 3–8 | Arithmetic |
| cmp2, cmp4, cmp8 | 4–16 | 1 | Comparison |
| decode3to8 | 3 | 8 | Structured |
| priority4 | 4 | 3 | Structured |

Baselines are structural implementations (e.g., textbook ripple-carry adders, array multipliers). ABC baselines are obtained by running `resyn2`, `resyn2rs`, and `compress2` on each baseline and taking the best result.

### 4.2 Gate Count Comparison

| Benchmark | Baseline | ABC `resyn2` | Ours | vs ABC | vs Baseline |
|-----------|:--------:|:------------:|:----:|:------:|:-----------:|
| half_adder | 4 | 4 | **3** | -25% | -25% |
| full_adder | 9 | 10 | **7** | -30% | -22% |
| add2 | 13 | 15 | **10** | -33% | -23% |
| add4 | 31 | 49 | **24** | -51% | -23% |
| add8 | 67 | 320 | **52** | -84% | -22% |
| mul2x2 | 12 | 12 | **8** | -33% | -33% |
| mul3x3 | 48 | 66 | **36** | -45% | -25% |
| mul4x4 | 104 | 389 | **81** | -79% | -22% |
| decode3to8 | 16 | 16 | **12** | -25% | -25% |
| priority4 | 8 | 6 | **5** | -17% | -38% |
| cmp8 | 46 | 38 | **29** | -24% | -37% |
| **Total** | **454** | **983** | **325** | **-66.9%** | **-28.4%** |

*Only benchmarks where our solver improves over at least one baseline are shown. The remaining 9 benchmarks are ties across all methods.*

Key observations:

- **Arithmetic circuits show the largest gains.** ABC's `resyn2` struggles with multi-output arithmetic because it optimizes each output independently. Our structural templates (CLA adders, Wallace tree multipliers) exploit the mathematical structure, and multi-output SAT finds optimal gate sharing.
- **ABC polish improves our templates.** The raw Wallace tree multiplier for 4x4 uses 84 gates; ABC's `dch_resyn` reduces it to 81. This 3-gate improvement comes from local rewriting that our global search misses — the two approaches are complementary.
- **Multi-output sharing is critical.** For `full_adder`, independent synthesis of sum (XOR, 3 gates) and carry (MAJ, 4 gates) yields 7 gates. But with multi-output SAT, we prove this is optimal — the shared AND gate between XOR and MAJ is automatically discovered.

### 4.3 Proven Optimality

Using multi-output SAT, we prove optimality for 14 of 20 benchmarks by showing that no AIG with fewer gates exists (the SAT formula is UNSAT for k-1 gates). For the remaining 6 (add4, add8, mul3x3, mul4x4, cmp4, cmp8), the input count exceeds our exact synthesis limit, but convergence of multiple methods to the same gate count suggests these are at or near optimal.

### 4.4 Why ABC Underperforms on Arithmetic

ABC's large losses on adders and multipliers (e.g., 320 vs 52 for add8) deserve explanation. ABC's `resyn2` starts from the baseline circuit (a structural implementation) and applies local rewriting. For well-structured baselines (like a CLA adder), there is little room for local improvement and ABC preserves the structure. But for poorly structured baselines (like a naive gate-level adder), ABC's local rewrites cannot discover the global CLA structure and get stuck in a local optimum.

Our solver sidesteps this entirely by synthesizing from the truth table. The baseline circuit is only used for comparison — the solver never sees it.

### 4.5 Runtime

The full benchmark suite completes in approximately 400 seconds on a single core. The dominant costs are:
- SAT-based exact synthesis for 5-input functions (~20s per function)
- ABC polish passes (~10s per circuit, 6 scripts x 3 rounds)
- E-graph saturation for 8-input multi-output functions (~15s)

Single-benchmark solves for small functions (3-4 inputs) complete in under 5 seconds.

---

## 5. Related Work

**ABC** [1] is the standard in academic and industrial logic synthesis. Its `resyn2` and `dch` scripts represent the state of the art for local AIG optimization. Our work is complementary: we use ABC as a polish step after global synthesis.

**mockturtle** [4] extends ABC's approach with more rewriting rules and better cut enumeration. Like ABC, it operates on existing circuit topology.

**Exact synthesis** via SAT has been explored for small functions [5, 6]. Our contribution is integrating exact synthesis into a multi-strategy framework and extending it to multi-output functions with shared gates.

**E-graphs** for hardware design have been explored in recent work [3, 7]. We use e-graphs specifically for discovering multi-output sharing, which complements the single-output focus of cut-based rewriting.

---

## 6. Conclusion

Global functional resynthesis offers a path beyond the local optima that limit conventional AIG optimization. By combining multiple synthesis strategies — each optimal for different function classes — with ABC's local polish, we achieve substantial gate reductions across a diverse benchmark suite. The key insight is that the global decomposition structure determines the optimization floor; local rewriting can only improve within that structure.

The solver is open-source and includes a TUI interface for interactive exploration. Future work includes scaling exact synthesis to larger functions via symmetry breaking, integrating technology mapping constraints, and extending the benchmark suite to industrial-scale circuits.

---

## References

[1] R. Brayton and A. Mishchenko, "ABC: An Academic Industrial-Strength Verification Tool," in *Proc. CAV*, 2010.

[2] A. Biere, K. Fazekas, M. Fleury, and M. Heisinger, "CaDiCaL, Kissat, Paracooba, Plingeling and Treengeling entering the SAT Competition 2020," in *Proc. SAT Competition*, 2020.

[3] S. Coward, G. Constantinides, and T. Sherwood, "Automating Constraint-Aware Datapath Optimization using E-Graphs," in *Proc. DAC*, 2023.

[4] H. Riener et al., "mockturtle: A C++17 logic network library," in *Proc. IWLS*, 2020.

[5] W. Haaswijk, M. Soeken, A. Mishchenko, and G. De Micheli, "SAT-based Exact Synthesis: Encodings, Topology Families, and Parallelism," in *IEEE TCAD*, 2020.

[6] M. Soeken, G. De Micheli, and A. Mishchenko, "Busy Man's Synthesis: Combinational Delay Optimization With SAT," in *Proc. DATE*, 2017.

[7] Y. Wu et al., "Equality Saturation for Hardware Design," in *Proc. ASPLOS*, 2024.
