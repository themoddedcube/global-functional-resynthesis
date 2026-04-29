"""Global functional resynthesis solver.

This is the file that gets iteratively improved by the autoresearch loop.
Given a TruthTable, produce an optimized Circuit (AIG).
"""

from __future__ import annotations

import itertools
from typing import Optional

from pysat.solvers import Cadical153

from benchmark import TruthTable, Circuit, load_benchmarks, run_evaluation, print_results


# ---------------------------------------------------------------------------
# Structural hashing for AIG construction
# ---------------------------------------------------------------------------

CONST1 = -(10**9)  # sentinel that won't collide with any real node ID

class AIGBuilder:
    """Build AIGs with structural hashing (automatic deduplication).

    Uses a special CONST1 sentinel (-1) since Python's -0 == 0.
    CONST1 is lazily materialized as an actual gate only if needed at build time.
    """

    def __init__(self, n_inputs: int):
        self.circuit = Circuit.new(n_inputs)
        self._hash: dict[tuple[int, int], int] = {}
        self._const1_id: Optional[int] = None

    def _get_const1(self) -> int:
        if self._const1_id is None:
            inp = self.circuit.inputs[0]
            # x OR NOT x = 1, built as NAND(NAND(x, x), NAND(NOT_x, NOT_x))
            # Simpler: create a dummy AND and then handle. Actually:
            # NOT(x AND NOT_x) but x AND NOT_x = 0, so NOT(0) = 1
            # We need: an AND node whose output we invert.
            # Build: n = AND(inp, inp) = inp. Then NOT(AND(NOT inp, NOT inp)) = NOT(NOT inp) = inp.
            # That doesn't help. Let's just build: AND(inp, 1) needs 1...
            # Simplest: create an OR gate = NAND(NOT a, NOT b) where a=inp, b=NOT inp
            # OR(inp, NOT inp) = NOT(AND(NOT inp, inp)) = NOT(0) = 1
            and_node = self.circuit.add_and(-inp, inp)  # always 0
            self._const1_id = -and_node  # NOT(0) = 1... but -and_node is just the negative lit
            # Actually AND(-inp, inp) = 0 for all inputs. So -and_node = NOT(0) = 1.
            # But wait, and_node might also be used. The and gate computes NOT(inp) AND inp = 0.
            # So the node always outputs 0, and -node always outputs 1. Good.
            return -and_node
        return self._const1_id

    def input(self, idx: int) -> int:
        return self.circuit.inputs[idx]

    def _resolve(self, a: int) -> int:
        if a == CONST1:
            return self._get_const1()
        if a == -CONST1:
            return 0
        return a

    def add_and(self, a: int, b: int) -> int:
        a = self._resolve(a)
        b = self._resolve(b)
        if a == 0 or b == 0:
            return 0
        if a == b:
            return a
        na, nb = abs(a), abs(b)
        if na == nb and a != b:
            return 0
        if na > nb:
            a, b = b, a
        key = (a, b)
        if key in self._hash:
            return self._hash[key]
        nid = self.circuit.add_and(a, b)
        self._hash[key] = nid
        return nid

    def add_or(self, a: int, b: int) -> int:
        a = self._resolve(a)
        b = self._resolve(b)
        if a == 0:
            return b
        if b == 0:
            return a
        if a == b:
            return a
        na, nb = abs(a), abs(b)
        if na == nb and a != b:
            return self._get_const1()
        return -self.add_and(-a, -b)

    def add_xor(self, a: int, b: int) -> int:
        a = self._resolve(a)
        b = self._resolve(b)
        if a == 0:
            return b
        if b == 0:
            return a
        if a == b:
            return 0
        na, nb = abs(a), abs(b)
        if na == nb and a != b:
            return self._get_const1()
        return self.add_or(self.add_and(a, -b), self.add_and(-a, b))

    def add_mux(self, sel: int, then_: int, else_: int) -> int:
        sel = self._resolve(sel)
        then_ = self._resolve(then_)
        else_ = self._resolve(else_)
        if then_ == else_:
            return then_
        if sel == 0:
            return else_
        return self.add_or(self.add_and(sel, then_), self.add_and(-sel, else_))

    def build(self, outputs: list[int]) -> Circuit:
        resolved = [self._resolve(o) for o in outputs]
        self.circuit.set_outputs(resolved)
        return self.circuit


# ---------------------------------------------------------------------------
# Shannon Decomposition
# ---------------------------------------------------------------------------

def shannon_decompose(tt: TruthTable) -> Circuit:
    """Synthesize circuit via recursive Shannon decomposition.

    available_vars tracks the ORIGINAL input indices throughout recursion.
    The truth table gets smaller at each level, but available_vars maps
    positions in the smaller truth table back to original input IDs.
    """
    builder = AIGBuilder(tt.n_inputs)
    outputs = []
    for j in range(tt.n_outputs):
        single_tt = TruthTable(tt.n_inputs, 1, (tt.table[j],))
        lit = _shannon_rec(single_tt, list(range(tt.n_inputs)), builder, {})
        outputs.append(lit)
    return builder.build(outputs)


def _best_shannon_var_idx(tt: TruthTable) -> int:
    """Choose variable INDEX (0-based position in truth table) that minimizes cofactor complexity."""
    best_idx = 0
    best_score = float('inf')
    t = tt.table[0]
    n = tt.n_inputs

    for var in range(n):
        step = 1 << var
        mask_lo = 0
        for block in range(1 << (n - var - 1)):
            base = block << (var + 1)
            for i in range(step):
                mask_lo |= (1 << (base + i))
        lo = t & mask_lo
        hi = (t >> step) & mask_lo
        score = bin(lo).count('1') + bin(hi).count('1')
        if lo == hi:
            score = -1
        if lo == 0 or hi == 0:
            score = 0
        if score < best_score:
            best_score = score
            best_idx = var
    return best_idx


def _shannon_rec(tt: TruthTable, orig_vars: list[int],
                 builder: AIGBuilder, cache: dict) -> int:
    """Recursive Shannon decomposition.

    orig_vars[i] = the original input index corresponding to position i
    in the current (possibly cofactored) truth table.
    """
    t = tt.table[0]
    n = tt.n_inputs

    cache_key = (t, tuple(orig_vars))
    if cache_key in cache:
        return cache[cache_key]

    all_bits = (1 << (1 << n)) - 1
    if t == 0:
        return 0
    if t == all_bits:
        return CONST1

    if n == 1:
        inp = builder.input(orig_vars[0])
        if t == 0b10:
            result = inp
        elif t == 0b01:
            result = -inp
        elif t == 0b11:
            result = CONST1
        else:
            result = 0
        cache[cache_key] = result
        return result

    var_idx = _best_shannon_var_idx(tt)
    original_input = orig_vars[var_idx]

    cof0 = tt.cofactor(var_idx, 0)
    cof1 = tt.cofactor(var_idx, 1)

    remaining_orig = [v for i, v in enumerate(orig_vars) if i != var_idx]

    lit0 = _shannon_rec(cof0, remaining_orig, builder, cache)
    lit1 = _shannon_rec(cof1, remaining_orig, builder, cache)

    sel = builder.input(original_input)

    if lit0 == lit1:
        cache[cache_key] = lit0
        return lit0
    if lit0 == 0:
        result = builder.add_and(sel, lit1)
    elif lit1 == 0:
        result = builder.add_and(-sel, lit0)
    else:
        result = builder.add_mux(sel, lit1, lit0)

    cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# PPRM (Positive Polarity Reed-Muller) / XOR Decomposition
# ---------------------------------------------------------------------------

def pprm_decompose(tt: TruthTable) -> Circuit:
    """Synthesize circuit using Positive Polarity Reed-Muller form."""
    builder = AIGBuilder(tt.n_inputs)
    outputs = []
    for j in range(tt.n_outputs):
        coeffs = _compute_pprm(tt.table[j], tt.n_inputs)
        lit = _pprm_to_aig(coeffs, tt.n_inputs, builder)
        outputs.append(lit)
    return builder.build(outputs)


def _compute_pprm(truth_table_bits: int, n: int) -> list[int]:
    """Compute PPRM coefficients via butterfly (Reed-Muller transform)."""
    size = 1 << n
    coeffs = [(truth_table_bits >> i) & 1 for i in range(size)]
    for i in range(n):
        step = 1 << i
        for j in range(0, size, step * 2):
            for k in range(step):
                coeffs[j + k + step] ^= coeffs[j + k]
    return coeffs


def _pprm_to_aig(coeffs: list[int], n: int, builder: AIGBuilder) -> int:
    """Convert PPRM coefficients to AIG."""
    terms = []
    for idx, coeff in enumerate(coeffs):
        if not coeff:
            continue
        if idx == 0:
            # Constant 1 term - defer handling
            terms.append(('const1',))
            continue
        # Build product of variables in idx
        lits = []
        for v in range(n):
            if (idx >> v) & 1:
                lits.append(builder.input(v))
        product = lits[0]
        for lit in lits[1:]:
            product = builder.add_and(product, lit)
        terms.append(product)

    if not terms:
        return 0

    # XOR all terms together
    result = None
    for t in terms:
        if t == ('const1',):
            if result is None:
                # Need a const1. Build as x0 OR NOT x0 via a truth table hack
                # Actually, let's just invert the final result
                result = ('pending_const1',)
            else:
                # XOR with 1 = NOT
                if result == ('pending_const1',):
                    result = None  # 1 XOR 1 = 0
                else:
                    result = -result
            continue

        if result is None:
            result = t
        elif result == ('pending_const1',):
            result = -t  # 1 XOR x = NOT x
        else:
            result = builder.add_xor(result, t)

    if result is None:
        return 0
    if result == ('pending_const1',):
        return CONST1
    return result


# ---------------------------------------------------------------------------
# SOP (Sum of Products) with basic minimization
# ---------------------------------------------------------------------------

def sop_synthesize(tt: TruthTable) -> Circuit:
    """Synthesize via Sum of Products with simple minterm grouping."""
    builder = AIGBuilder(tt.n_inputs)
    outputs = []
    for j in range(tt.n_outputs):
        lit = _sop_single(tt.table[j], tt.n_inputs, builder)
        outputs.append(lit)
    return builder.build(outputs)


def _sop_single(truth_bits: int, n: int, builder: AIGBuilder) -> int:
    """Build AIG for a single output using SOP."""
    size = 1 << n
    all_bits = size - 1

    on_count = bin(truth_bits).count('1')
    if on_count == 0:
        return 0
    if on_count == size:
        return CONST1

    # Use the smaller set (on-set or off-set)
    invert = on_count > size // 2
    target = truth_bits if not invert else (truth_bits ^ ((1 << size) - 1))

    minterms = [i for i in range(size) if (target >> i) & 1]

    # Simple prime implicant computation via pairwise merging
    primes = _compute_prime_implicants(minterms, n)

    # Greedy set cover
    covered = set()
    selected = []
    uncovered = set(minterms)
    while uncovered:
        best = max(primes, key=lambda p: len(_covers(p, n) & uncovered))
        selected.append(best)
        uncovered -= _covers(best, n)

    # Build AIG for selected implicants
    products = []
    for impl in selected:
        lits = []
        for v in range(n):
            if impl[v] == 1:
                lits.append(builder.input(v))
            elif impl[v] == 0:
                lits.append(-builder.input(v))
        if not lits:
            products.append(CONST1)
        else:
            p = lits[0]
            for l in lits[1:]:
                p = builder.add_and(p, l)
            products.append(p)

    result = products[0]
    for p in products[1:]:
        result = builder.add_or(result, p)

    if invert:
        result = -result
    return result


def _compute_prime_implicants(minterms: list[int], n: int) -> list[tuple]:
    """Compute prime implicants via iterated consensus."""
    # Represent implicants as tuples: 0=negated, 1=positive, 2=don't-care
    current = set()
    for m in minterms:
        impl = tuple((m >> v) & 1 for v in range(n))
        current.add(impl)

    changed = True
    while changed:
        changed = False
        merged = set()
        used = set()
        for a, b in itertools.combinations(current, 2):
            diff = [i for i in range(n) if a[i] != b[i]]
            if len(diff) == 1 and a[diff[0]] != 2 and b[diff[0]] != 2:
                new = list(a)
                new[diff[0]] = 2
                merged.add(tuple(new))
                used.add(a)
                used.add(b)
                changed = True
        current = (current - used) | merged

    return list(current)


def _covers(impl: tuple, n: int) -> set:
    """Return set of minterms covered by an implicant."""
    result = set()
    dc_vars = [i for i in range(n) if impl[i] == 2]
    fixed = 0
    for i in range(n):
        if impl[i] == 1:
            fixed |= (1 << i)
    for combo in range(1 << len(dc_vars)):
        m = fixed
        for j, v in enumerate(dc_vars):
            if (combo >> j) & 1:
                m |= (1 << v)
        result.add(m)
    return result


# ---------------------------------------------------------------------------
# SAT-based Exact Synthesis
# ---------------------------------------------------------------------------

def exact_synthesis(tt: TruthTable, max_gates: Optional[int] = None) -> Optional[Circuit]:
    """Find minimum-gate AIG using SAT-based exact synthesis.

    Only practical for n_inputs <= 5 and n_outputs == 1.
    For multi-output, synthesize each output independently.
    """
    if tt.n_outputs > 1:
        builder = AIGBuilder(tt.n_inputs)
        outputs = []
        for j in range(tt.n_outputs):
            single_tt = TruthTable(tt.n_inputs, 1, (tt.table[j],))
            single_circ = exact_synthesis(single_tt, max_gates)
            if single_circ is None:
                return None
            # Merge into builder - need to extract the logic
            # For simplicity, build each output independently
            outputs.append(None)

        # Simpler approach: build each output as independent circuit, merge
        return _exact_multi_output(tt, max_gates)

    return _exact_single_output(tt, max_gates)


def _exact_single_output(tt: TruthTable, max_gates: Optional[int] = None) -> Optional[Circuit]:
    """Exact synthesis for single-output function."""
    n = tt.n_inputs
    t = tt.table[0]
    size = 1 << n

    if t == 0:
        c = Circuit.new(n)
        c.set_outputs([0])
        return c
    if t == (1 << size) - 1:
        c = Circuit.new(n)
        n0 = c.add_and(-1, 1)
        c.set_outputs([-n0])
        return c

    # Check if function is a single literal
    for v in range(n):
        pos_mask = 0
        for i in range(size):
            if (i >> v) & 1:
                pos_mask |= (1 << i)
        if t == pos_mask:
            c = Circuit.new(n)
            c.set_outputs([v + 1])
            return c
        if t == ((1 << size) - 1) ^ pos_mask:
            c = Circuit.new(n)
            c.set_outputs([-(v + 1)])
            return c

    # Binary search on gate count
    if max_gates is None:
        max_gates = min(20, size)

    for num_gates in range(1, max_gates + 1):
        result = _try_exact(tt, num_gates)
        if result is not None:
            return result

    return None


def _try_exact(tt: TruthTable, num_gates: int) -> Optional[Circuit]:
    """Try to synthesize with exactly num_gates AND gates using SAT."""
    n = tt.n_inputs
    t = tt.table[0]

    # Use CEGIS: start with subset of patterns, add counterexamples
    import random
    size = 1 << n

    if size <= 32:
        patterns = list(range(size))
    else:
        patterns = random.sample(range(size), min(32, size))

    num_nodes = n + num_gates  # inputs + gates
    # Node IDs: 0..n-1 are inputs, n..n+num_gates-1 are gates

    while True:
        result = _sat_solve(tt, num_gates, patterns)
        if result is None:
            return None  # UNSAT with these constraints = need more gates

        circuit = result
        # Verify against full truth table
        for p in range(size):
            expected = (t >> p) & 1
            got = (circuit.simulate(p) >> 0) & 1
            if got != expected:
                patterns.append(p)
                break
        else:
            return circuit  # All patterns match


def _sat_solve(tt: TruthTable, num_gates: int, patterns: list[int]) -> Optional[Circuit]:
    """Core SAT encoding for exact synthesis."""
    n = tt.n_inputs
    t = tt.table[0]
    g = num_gates
    total_nodes = n + g  # 0..n-1 inputs, n..n+g-1 gates

    solver = Cadical153()
    var_count = [0]

    def new_var():
        var_count[0] += 1
        return var_count[0]

    # Variables:
    # sel[i][j][k] - gate i (0-indexed from 0 to g-1) has inputs j and k
    #   j, k in range(0, n+i) - can use inputs or earlier gates
    # pol[i][0], pol[i][1] - polarity of input 0 and 1 of gate i (1=inverted)
    # sim[node][pattern] - simulation value of node at pattern

    sel = {}   # (gate_idx, input_idx_0, input_idx_1) -> var
    pol = {}   # (gate_idx, 0_or_1) -> var
    sim = {}   # (node_idx, pattern_idx) -> var

    # Create simulation variables for inputs
    for node in range(n):
        for pidx, p in enumerate(patterns):
            v = new_var()
            sim[(node, pidx)] = v
            val = (p >> node) & 1
            solver.add_clause([v if val else -v])

    # Create variables for gates
    for gi in range(g):
        gate_node = n + gi
        max_input = n + gi  # can use inputs 0..n-1 and gates n..n+gi-1

        # Selection variables: which two nodes are inputs
        # Use a pair-selection encoding
        for j in range(max_input):
            for k in range(j + 1, max_input):
                v = new_var()
                sel[(gi, j, k)] = v

        # Exactly one pair selected
        pair_vars = [sel[(gi, j, k)] for j in range(max_input) for k in range(j + 1, max_input)]
        if not pair_vars:
            solver.delete()
            return None

        # At least one
        solver.add_clause(pair_vars)
        # At most one (pairwise)
        for a, b in itertools.combinations(pair_vars, 2):
            solver.add_clause([-a, -b])

        # Polarity variables
        pol[(gi, 0)] = new_var()
        pol[(gi, 1)] = new_var()

        # Simulation variables for this gate
        for pidx in range(len(patterns)):
            sim[(gate_node, pidx)] = new_var()

    # Simulation constraints for each gate
    for gi in range(g):
        gate_node = n + gi
        max_input = n + gi
        p0 = pol[(gi, 0)]
        p1 = pol[(gi, 1)]

        for pidx in range(len(patterns)):
            out_var = sim[(gate_node, pidx)]

            for j in range(max_input):
                for k in range(j + 1, max_input):
                    s = sel[(gi, j, k)]
                    sj = sim[(j, pidx)]
                    sk = sim[(k, pidx)]

                    # When sel[(gi,j,k)] is true:
                    # out = (sj XOR p0) AND (sk XOR p1)
                    # We need: s -> (out <-> (sj XOR p0) AND (sk XOR p1))

                    # Create intermediate variables for XOR
                    xj = new_var()  # sj XOR p0
                    xk = new_var()  # sk XOR p1

                    # xj <-> sj XOR p0
                    solver.add_clause([-s, -xj, sj, p0])
                    solver.add_clause([-s, -xj, -sj, -p0])
                    solver.add_clause([-s, xj, sj, -p0])
                    solver.add_clause([-s, xj, -sj, p0])

                    # xk <-> sk XOR p1
                    solver.add_clause([-s, -xk, sk, p1])
                    solver.add_clause([-s, -xk, -sk, -p1])
                    solver.add_clause([-s, xk, sk, -p1])
                    solver.add_clause([-s, xk, -sk, p1])

                    # out <-> xj AND xk (when s is true)
                    solver.add_clause([-s, -out_var, xj])
                    solver.add_clause([-s, -out_var, xk])
                    solver.add_clause([-s, out_var, -xj, -xk])

    # Output constraint: last gate must produce correct output
    # Allow output to be any gate or input, possibly inverted
    out_node_var = {}
    out_pol_var = new_var()

    for node in range(total_nodes):
        out_node_var[node] = new_var()

    # Exactly one output node selected
    solver.add_clause(list(out_node_var.values()))
    for a, b in itertools.combinations(out_node_var.values(), 2):
        solver.add_clause([-a, -b])

    # Output matches truth table
    for pidx, p in enumerate(patterns):
        expected = (t >> p) & 1
        for node in range(total_nodes):
            s_var = out_node_var[node]
            sim_var = sim[(node, pidx)]
            # When this node is selected as output:
            # (sim XOR out_pol) must equal expected
            if expected:
                # sim XOR pol = 1 -> sim != pol
                solver.add_clause([-s_var, sim_var, out_pol_var])
                solver.add_clause([-s_var, -sim_var, -out_pol_var])
            else:
                # sim XOR pol = 0 -> sim == pol
                solver.add_clause([-s_var, sim_var, -out_pol_var])
                solver.add_clause([-s_var, -sim_var, out_pol_var])

    # Symmetry breaking: gate i's first input index < gate i+1's first input index (weak)
    # Skip for now - just solve

    if not solver.solve():
        solver.delete()
        return None

    model = set(solver.get_model())
    solver.delete()

    # Extract circuit from model
    c = Circuit.new(n)
    gate_ids = {}  # gate_idx -> circuit node id

    for gi in range(g):
        max_input = n + gi
        # Find selected pair
        found = False
        for j in range(max_input):
            for k in range(j + 1, max_input):
                if sel[(gi, j, k)] in model:
                    # Get polarities
                    inv0 = pol[(gi, 0)] in model
                    inv1 = pol[(gi, 1)] in model

                    # Map j, k to circuit node IDs
                    def to_circuit_id(idx):
                        if idx < n:
                            return idx + 1  # input IDs are 1-based
                        return gate_ids[idx - n]

                    cid_j = to_circuit_id(j)
                    cid_k = to_circuit_id(k)

                    if inv0:
                        cid_j = -cid_j
                    if inv1:
                        cid_k = -cid_k

                    gate_ids[gi] = c.add_and(cid_j, cid_k)
                    found = True
                    break
            if found:
                break

    # Find output node and polarity
    out_inv = out_pol_var in model
    for node in range(total_nodes):
        if out_node_var[node] in model:
            if node < n:
                out_lit = node + 1
            else:
                out_lit = gate_ids[node - n]
            if out_inv:
                out_lit = -out_lit
            c.set_outputs([out_lit])
            return c

    return None


def _exact_multi_output(tt: TruthTable, max_gates: Optional[int] = None) -> Optional[Circuit]:
    """Exact synthesis for multi-output by synthesizing each output independently."""
    builder = AIGBuilder(tt.n_inputs)
    outputs = []
    for j in range(tt.n_outputs):
        single_tt = TruthTable(tt.n_inputs, 1, (tt.table[j],))
        circ = _exact_single_output(single_tt, max_gates)
        if circ is None:
            return None

        # Need to remap nodes from individual circuit into shared builder
        # Simple approach: just rebuild from truth table using best non-exact method
        single_tt_result = circ.to_truth_table()
        assert single_tt_result == single_tt

        # For now, use individual circuits - we'll combine later
        outputs.append(circ)

    # Merge individual circuits into one
    merged = Circuit.new(tt.n_inputs)
    all_outputs = []
    for circ in outputs:
        remap = {}
        for inp_id in circ.inputs:
            remap[inp_id] = inp_id  # inputs are same

        sorted_nodes = sorted(
            [n for n in circ.nodes.values() if n.type == 'AND'],
            key=lambda n: n.id
        )
        for node in sorted_nodes:
            def remap_lit(lit):
                nid = abs(lit)
                mapped = remap.get(nid, nid)
                return -mapped if lit < 0 else mapped

            new_id = merged.add_and(remap_lit(node.fanin0), remap_lit(node.fanin1))
            remap[node.id] = new_id

        for out in circ.outputs:
            nid = abs(out)
            mapped = remap.get(nid, nid)
            all_outputs.append(-mapped if out < 0 else mapped)

    merged.set_outputs(all_outputs)
    return merged


# ---------------------------------------------------------------------------
# AIG Rewriting (basic passes)
# ---------------------------------------------------------------------------

def aig_rewrite(circuit: Circuit) -> Circuit:
    """Apply basic AIG rewriting passes for cleanup via structural hashing rebuild."""
    tt = circuit.to_truth_table()
    n = tt.n_inputs
    if n > 16:
        return circuit

    builder = AIGBuilder(n)
    outputs = []
    cache = {}
    for j in range(tt.n_outputs):
        single_tt = TruthTable(n, 1, (tt.table[j],))
        lit = _shannon_rec(single_tt, list(range(n)), builder, cache)
        outputs.append(lit)

    rewritten = builder.build(outputs)
    if rewritten.gate_count() < circuit.gate_count():
        return rewritten
    return circuit


def functional_decompose(tt: TruthTable) -> Circuit:
    """Dependency-aware multi-output synthesis.

    For each output, identifies which variables it actually depends on.
    If an output depends on <= exact_limit variables, uses exact synthesis
    on the reduced truth table and embeds the result.
    """
    n = tt.n_inputs
    n_out = tt.n_outputs
    builder = AIGBuilder(n)

    deps = []
    for j in range(n_out):
        d = set()
        for v in range(n):
            if tt.depends_on(v, j):
                d.add(v)
        deps.append(d)

    order = sorted(range(n_out), key=lambda j: len(deps[j]))

    outputs = [None] * n_out
    cache = {}

    for j in order:
        single_tt = TruthTable(n, 1, (tt.table[j],))
        dep_vars = sorted(deps[j])

        if not dep_vars:
            outputs[j] = 0
            continue

        if len(dep_vars) <= 5:
            reduced_tt = _reduce_to_vars(single_tt, dep_vars)
            try:
                from solver import _exact_single_output
                exact_circ = _exact_single_output(reduced_tt, max_gates=15)
                if exact_circ is not None:
                    from benchmark import verify_equivalence as ve
                    if ve(exact_circ, reduced_tt):
                        lit = _embed_circuit(exact_circ, dep_vars, builder)
                        outputs[j] = lit
                        continue
            except Exception:
                pass

        lit = _shannon_rec(single_tt, list(range(n)), builder, cache)
        outputs[j] = lit

    return builder.build(outputs)


def _reduce_to_vars(tt: TruthTable, vars: list[int]) -> TruthTable:
    n = tt.n_inputs
    m = len(vars)
    bits = 0
    for p in range(1 << m):
        full_pattern = 0
        for i, v in enumerate(vars):
            if (p >> i) & 1:
                full_pattern |= (1 << v)
        if (tt.table[0] >> full_pattern) & 1:
            bits |= (1 << p)
    return TruthTable(m, 1, (bits,))


def _embed_circuit(circ: Circuit, var_map: list[int], builder: AIGBuilder) -> int:
    remap = {}
    for i, inp_id in enumerate(circ.inputs):
        remap[inp_id] = builder.input(var_map[i])

    for node in sorted(circ.nodes.values(), key=lambda n: n.id):
        if node.type != 'AND':
            continue

        def remap_lit(lit):
            nid = abs(lit)
            mapped = remap.get(nid, nid)
            return -mapped if lit < 0 else mapped

        new_id = builder.add_and(remap_lit(node.fanin0), remap_lit(node.fanin1))
        remap[node.id] = new_id

    if circ.outputs:
        out = circ.outputs[0]
        nid = abs(out)
        mapped = remap.get(nid, nid)
        return -mapped if out < 0 else mapped
    return 0


# ---------------------------------------------------------------------------
# Main Solver
# ---------------------------------------------------------------------------

class Solver:
    def __init__(self, use_exact: bool = True, exact_limit: int = 5):
        self.use_exact = use_exact
        self.exact_limit = exact_limit

    def solve(self, tt: TruthTable) -> Circuit:
        from benchmark import verify_equivalence
        candidates = []

        # Method 1: Shannon decomposition
        try:
            c1 = shannon_decompose(tt)
            if verify_equivalence(c1, tt):
                candidates.append(('shannon', c1))
        except Exception:
            pass

        # Method 2: PPRM / XOR decomposition
        try:
            c2 = pprm_decompose(tt)
            if verify_equivalence(c2, tt):
                candidates.append(('pprm', c2))
        except Exception:
            pass

        # Method 3: SOP synthesis
        if tt.n_inputs <= 12:
            try:
                c3 = sop_synthesize(tt)
                if verify_equivalence(c3, tt):
                    candidates.append(('sop', c3))
            except Exception:
                pass

        # Method 4: Exact synthesis (for small functions)
        if self.use_exact and tt.n_inputs <= self.exact_limit:
            try:
                c4 = exact_synthesis(tt, max_gates=20)
                if c4 is not None and verify_equivalence(c4, tt):
                    candidates.append(('exact', c4))
            except Exception:
                pass

        # Method 5: Functional decomposition (dependency-aware multi-output)
        if tt.n_outputs > 1:
            try:
                c5 = functional_decompose(tt)
                if verify_equivalence(c5, tt):
                    candidates.append(('funcdec', c5))
            except Exception:
                pass

        if not candidates:
            return shannon_decompose(tt)

        # Pick best by gate count
        best_name, best_circ = min(candidates, key=lambda x: x[1].gate_count())
        return best_circ


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def solve(tt: TruthTable) -> Circuit:
    solver = Solver(use_exact=True, exact_limit=5)
    return solver.solve(tt)


if __name__ == '__main__':
    benchmarks = load_benchmarks()
    results = run_evaluation(solve, benchmarks)
    print_results(results)
