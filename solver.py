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
    """Find minimum-gate AIG using SAT-based exact synthesis."""
    if tt.n_outputs > 1:
        return _exact_multi_output(tt, max_gates)
    return _exact_single_output(tt, max_gates, total_timeout=60)


def _exact_single_output(tt: TruthTable, max_gates: Optional[int] = None,
                         total_timeout: Optional[float] = None) -> Optional[Circuit]:
    """Exact synthesis for single-output function."""
    import time as _time
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

    if max_gates is None:
        max_gates = min(20, size)

    if total_timeout is None:
        total_timeout = 60 if n >= 6 else 300

    global_start = _time.time()
    for num_gates in range(1, max_gates + 1):
        remaining = total_timeout - (_time.time() - global_start)
        if remaining <= 0:
            return None
        result = _try_exact(tt, num_gates, per_timeout=min(remaining, 10 if n >= 6 else 30))
        if result is not None:
            return result

    return None


def _try_exact(tt: TruthTable, num_gates: int, per_timeout: float = 30) -> Optional[Circuit]:
    """Try to synthesize with exactly num_gates AND gates using SAT."""
    import time as _time
    n = tt.n_inputs
    t = tt.table[0]

    import random
    size = 1 << n

    if size <= 32:
        patterns = list(range(size))
    else:
        patterns = random.sample(range(size), min(32, size))

    start = _time.time()

    while True:
        if _time.time() - start > per_timeout:
            return None

        result = _sat_solve(tt, num_gates, patterns)
        if result is None:
            return None

        circuit = result
        for p in range(size):
            expected = (t >> p) & 1
            got = (circuit.simulate(p) >> 0) & 1
            if got != expected:
                patterns.append(p)
                break
        else:
            return circuit


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
    per_timeout = 15 if tt.n_inputs >= 6 else 60
    for j in range(tt.n_outputs):
        single_tt = TruthTable(tt.n_inputs, 1, (tt.table[j],))
        circ = _exact_single_output(single_tt, max_gates, total_timeout=per_timeout)
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
                exact_circ = _exact_single_output(reduced_tt, max_gates=15, total_timeout=30)
                if exact_circ is not None:
                    from benchmark import verify_equivalence as ve
                    if ve(exact_circ, reduced_tt):
                        lit = _embed_circuit(exact_circ, dep_vars, builder)
                        outputs[j] = lit
                        continue
            except Exception:
                pass

        if len(dep_vars) < n:
            reduced_tt = _reduce_to_vars(single_tt, dep_vars)
            lit = _best_order_shannon(reduced_tt, dep_vars, builder)
            outputs[j] = lit
        else:
            lit = _best_order_shannon(single_tt, list(range(n)), builder)
            outputs[j] = lit

    return builder.build(outputs)


def _best_order_shannon(tt: TruthTable, orig_vars: list[int],
                        builder: AIGBuilder) -> int:
    """Try multiple variable orderings for Shannon decomposition, pick best."""
    import random
    n = tt.n_inputs
    if n <= 6:
        n_tries = min(100, 1)
        orderings = [list(range(n))]
        for _ in range(n_tries):
            order = list(range(n))
            random.shuffle(order)
            orderings.append(order)
    else:
        orderings = [list(range(n))]
        orderings.append(list(reversed(range(n))))
        half = n // 2
        interleaved = []
        for i in range(half):
            interleaved.append(i)
            interleaved.append(i + half)
        if n % 2:
            interleaved.append(n - 1)
        orderings.append(interleaved)
        n_random = 10 if n > 10 else 50
        for _ in range(n_random):
            order = list(range(n))
            random.shuffle(order)
            orderings.append(order)

    best_lit = None
    best_gates = float('inf')

    for order in orderings:
        test_builder = AIGBuilder(len(orig_vars))
        reordered_vars = list(range(n))
        cache = {}
        lit = _shannon_rec(tt, [reordered_vars[i] for i in order], test_builder, cache)
        test_circ = test_builder.build([lit])
        gc = test_circ.gate_count()
        if gc < best_gates:
            best_gates = gc
            best_order = order

    cache = {}
    mapped_vars = [orig_vars[i] for i in best_order]
    return _shannon_rec(tt, mapped_vars, builder, cache)


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


def _build_cla_adder(n_bits: int) -> Circuit:
    builder = AIGBuilder(2 * n_bits)
    G, P = [], []
    for i in range(n_bits):
        a = builder.input(i)
        b = builder.input(n_bits + i)
        g = builder.add_and(a, b)
        nab = builder.add_and(-a, -b)
        p = builder.add_and(-g, -nab)
        G.append(g)
        P.append(p)
    carries = [0]
    for i in range(n_bits):
        c_prev = carries[i]
        if c_prev == 0:
            c_next = G[i]
        else:
            pc = builder.add_and(P[i], c_prev)
            c_next = -builder.add_and(-G[i], -pc)
        carries.append(c_next)
    outputs = []
    for i in range(n_bits):
        p, c = P[i], carries[i]
        if c == 0:
            outputs.append(p)
        else:
            pc_and = builder.add_and(p, c)
            pc_nor = builder.add_and(-p, -c)
            outputs.append(builder.add_and(-pc_and, -pc_nor))
    outputs.append(carries[n_bits])
    return builder.build(outputs)


def _build_ripple_carry_adder(n_bits: int) -> Circuit:
    builder = AIGBuilder(2 * n_bits)
    carry = 0
    outputs = []
    for i in range(n_bits):
        a = builder.input(i)
        b = builder.input(n_bits + i)
        ab = builder.add_and(a, b)
        na_nb = builder.add_and(-a, -b)
        xor_ab = builder.add_and(-ab, -na_nb)
        if carry == 0:
            sum_bit = xor_ab
            new_carry = ab
        else:
            xc = builder.add_and(xor_ab, carry)
            nxc = builder.add_and(-xor_ab, -carry)
            sum_bit = builder.add_and(-xc, -nxc)
            carry_or = builder.add_and(carry, -na_nb)
            new_carry = -builder.add_and(-ab, -carry_or)
        outputs.append(sum_bit)
        carry = new_carry
    outputs.append(carry)
    return builder.build(outputs)


def _build_array_multiplier(n_bits: int) -> Circuit:
    builder = AIGBuilder(2 * n_bits)
    partial_sums = [0] * (2 * n_bits)
    for i in range(n_bits):
        carry = 0
        for j in range(n_bits):
            pp = builder.add_and(builder.input(i), builder.input(n_bits + j))
            prev = partial_sums[i + j]
            if prev == 0 and carry == 0:
                partial_sums[i + j] = pp
                continue
            # Full adder: sum = XOR(pp, prev, carry), carry_out = MAJ(pp, prev, carry)
            if carry == 0:
                a, b = pp, prev
            elif prev == 0:
                a, b = pp, carry
            else:
                # 3-input: need full adder
                ab = builder.add_and(pp, prev)
                nab = builder.add_and(-pp, -prev)
                xor_ab = builder.add_and(-ab, -nab)
                xc = builder.add_and(xor_ab, carry)
                nxc = builder.add_and(-xor_ab, -carry)
                partial_sums[i + j] = builder.add_and(-xc, -nxc)
                or_ab = -nab
                carry_or = builder.add_and(carry, or_ab)
                carry = -builder.add_and(-ab, -carry_or)
                continue
            ab = builder.add_and(a, b)
            nab = builder.add_and(-a, -b)
            partial_sums[i + j] = builder.add_and(-ab, -nab)
            carry = ab
        if carry != 0:
            partial_sums[i + n_bits] = carry
    return builder.build(partial_sums[:2 * n_bits])


def _shared_exact_multi(tt: TruthTable) -> Optional[Circuit]:
    """Multi-output exact synthesis with shared gates via a single AIGBuilder."""
    n = tt.n_inputs
    builder = AIGBuilder(n)
    outputs = []

    # Group outputs by their dependency sets
    output_info = []
    for j in range(tt.n_outputs):
        dep_vars = sorted(v for v in range(n) if tt.depends_on(v, j))
        output_info.append((j, dep_vars, tt.table[j]))

    # Sort by complexity (fewer dependencies first) to maximize sharing
    output_info.sort(key=lambda x: len(x[1]))

    cache = {}
    all_outputs = [None] * tt.n_outputs

    for j, dep_vars, table_bits in output_info:
        if not dep_vars:
            all_outputs[j] = 0
            continue

        if len(dep_vars) <= 5:
            reduced_tt = _reduce_to_vars(TruthTable(n, 1, (table_bits,)), dep_vars)
            exact_circ = _exact_single_output(reduced_tt, max_gates=15, total_timeout=30)
            if exact_circ is not None:
                from benchmark import verify_equivalence as ve
                if ve(exact_circ, reduced_tt):
                    lit = _embed_circuit(exact_circ, dep_vars, builder)
                    all_outputs[j] = lit
                    continue

        # Fallback: Shannon with the shared builder
        single_tt = TruthTable(n, 1, (table_bits,))
        lit = _shannon_rec(single_tt, list(range(n)), builder, cache)
        all_outputs[j] = lit

    if any(o is None for o in all_outputs):
        return None

    return builder.build(all_outputs)


def _build_prefix_adder(n_bits: int) -> Circuit:
    """Brent-Kung style parallel prefix adder. Fewer gates than ripple for n >= 4."""
    builder = AIGBuilder(2 * n_bits)

    # Generate and propagate for each bit
    G = []  # generate: a AND b
    P = []  # propagate: a XOR b (= -AND(-AND(a,-b), -AND(-a,b)) ... but we use the AIG version)
    for i in range(n_bits):
        a = builder.input(i)
        b = builder.input(n_bits + i)
        g = builder.add_and(a, b)
        nab = builder.add_and(-a, -b)
        p = builder.add_and(-g, -nab)  # XOR via AIG
        G.append(g)
        P.append(p)

    # Parallel prefix tree (Brent-Kung)
    # prefix_G[i] = carry into bit i+1
    # (G_combined, P_combined) = prefix operator applied pairwise
    # Operator: (G_hi, P_hi) o (G_lo, P_lo) = (G_hi OR (P_hi AND G_lo), P_hi AND P_lo)
    levels_G = [list(G)]
    levels_P = [list(P)]

    # Up-sweep: combine pairs at increasing distances
    stride = 1
    while stride < n_bits:
        prev_G = levels_G[-1]
        prev_P = levels_P[-1]
        new_G = list(prev_G)
        new_P = list(prev_P)
        for i in range(stride * 2 - 1, n_bits, stride * 2):
            j = i - stride
            # (new_G[i], new_P[i]) = (prev_G[i], prev_P[i]) o (prev_G[j], prev_P[j])
            pg = builder.add_and(prev_P[i], prev_G[j])
            new_G[i] = builder.add_or(prev_G[i], pg)
            new_P[i] = builder.add_and(prev_P[i], prev_P[j])
        levels_G.append(new_G)
        levels_P.append(new_P)
        stride *= 2

    # Down-sweep: fill in remaining prefix sums
    cur_G = levels_G[-1]
    cur_P = levels_P[-1]
    stride = stride // 4
    while stride >= 1:
        new_G = list(cur_G)
        new_P = list(cur_P)
        for i in range(stride * 3 - 1, n_bits, stride * 2):
            j = i - stride
            if j >= 0:
                pg = builder.add_and(cur_P[i], cur_G[j])
                new_G[i] = builder.add_or(cur_G[i], pg)
                new_P[i] = builder.add_and(cur_P[i], cur_P[j])
        cur_G = new_G
        cur_P = new_P
        stride //= 2

    # Sum bits: s[i] = P[i] XOR carry[i], where carry[0]=0, carry[i+1]=cur_G[i]
    outputs = []
    for i in range(n_bits):
        if i == 0:
            outputs.append(P[i])
        else:
            carry = cur_G[i - 1]
            xor_pc = builder.add_and(P[i], carry)
            nxor_pc = builder.add_and(-P[i], -carry)
            outputs.append(builder.add_and(-xor_pc, -nxor_pc))
    outputs.append(cur_G[n_bits - 1])
    return builder.build(outputs)


def _aig_half_adder(builder, a, b):
    """Returns (sum, carry) in AIG."""
    ab = builder.add_and(a, b)
    nab = builder.add_and(-a, -b)
    s = builder.add_and(-ab, -nab)  # XOR
    return s, ab

def _aig_full_adder(builder, a, b, cin):
    """Returns (sum, carry) in AIG."""
    ab = builder.add_and(a, b)
    nab = builder.add_and(-a, -b)
    xor_ab = builder.add_and(-ab, -nab)

    xc = builder.add_and(xor_ab, cin)
    nxc = builder.add_and(-xor_ab, -cin)
    s = builder.add_and(-xc, -nxc)  # XOR(XOR(a,b), cin)

    # carry = (a AND b) OR (XOR(a,b) AND cin)
    carry = builder.add_or(ab, xc)
    return s, carry


def _build_wallace_tree_multiplier(n_bits: int) -> Circuit:
    """Wallace tree multiplier: reduce partial products with 3-2 counters."""
    builder = AIGBuilder(2 * n_bits)
    n_out = 2 * n_bits

    # Generate partial products
    columns = [[] for _ in range(n_out)]
    for i in range(n_bits):
        for j in range(n_bits):
            pp = builder.add_and(builder.input(i), builder.input(n_bits + j))
            columns[i + j].append(pp)

    # Wallace tree reduction: repeatedly reduce columns until each has <= 2 entries
    while max(len(col) for col in columns) > 2:
        new_columns = [[] for _ in range(n_out)]
        for c in range(n_out):
            col = columns[c]
            i = 0
            while i + 2 < len(col):
                s, carry = _aig_full_adder(builder, col[i], col[i+1], col[i+2])
                new_columns[c].append(s)
                if c + 1 < n_out:
                    new_columns[c + 1].append(carry)
                i += 3
            while i < len(col):
                new_columns[c].append(col[i])
                i += 1
        columns = new_columns

    # Final addition: each column has at most 2 entries
    carry = 0
    outputs = []
    for c in range(n_out):
        col = columns[c]
        if carry != 0:
            col = col + [carry]

        if len(col) == 0:
            outputs.append(0)
            carry = 0
        elif len(col) == 1:
            outputs.append(col[0])
            carry = 0
        elif len(col) == 2:
            s, carry = _aig_half_adder(builder, col[0], col[1])
            outputs.append(s)
        else:  # 3
            s, carry = _aig_full_adder(builder, col[0], col[1], col[2])
            outputs.append(s)

    return builder.build(outputs)


def _build_dadda_tree_multiplier(n_bits: int) -> Circuit:
    """Dadda tree multiplier: minimizes half adders compared to Wallace tree."""
    builder = AIGBuilder(2 * n_bits)
    n_out = 2 * n_bits

    columns = [[] for _ in range(n_out)]
    for i in range(n_bits):
        for j in range(n_bits):
            pp = builder.add_and(builder.input(i), builder.input(n_bits + j))
            columns[i + j].append(pp)

    max_height = max(len(col) for col in columns)
    targets = [2]
    while targets[-1] < max_height:
        targets.append(int(targets[-1] * 3 / 2))
    targets.reverse()

    for target in targets:
        if max(len(col) for col in columns) <= target:
            continue
        new_columns = [[] for _ in range(n_out)]
        for c in range(n_out):
            col = columns[c]
            i = 0
            while len(col) - i + len(new_columns[c]) > target:
                if len(col) - i >= 3:
                    s, carry = _aig_full_adder(builder, col[i], col[i+1], col[i+2])
                    new_columns[c].append(s)
                    if c + 1 < n_out:
                        new_columns[c + 1].append(carry)
                    i += 3
                elif len(col) - i == 2 and len(col) - i + len(new_columns[c]) > target:
                    s, carry = _aig_half_adder(builder, col[i], col[i+1])
                    new_columns[c].append(s)
                    if c + 1 < n_out:
                        new_columns[c + 1].append(carry)
                    i += 2
                else:
                    break
            while i < len(col):
                new_columns[c].append(col[i])
                i += 1
        columns = new_columns

    carry = 0
    outputs = []
    for c in range(n_out):
        col = columns[c]
        if carry != 0:
            col = col + [carry]
        if len(col) == 0:
            outputs.append(0)
            carry = 0
        elif len(col) == 1:
            outputs.append(col[0])
            carry = 0
        elif len(col) == 2:
            s, carry = _aig_half_adder(builder, col[0], col[1])
            outputs.append(s)
        else:
            s, carry = _aig_full_adder(builder, col[0], col[1], col[2])
            outputs.append(s)
    return builder.build(outputs)


def _build_wallace_cla_multiplier(n_bits: int) -> Circuit:
    """Wallace tree with CLA final adder instead of ripple carry."""
    builder = AIGBuilder(2 * n_bits)
    n_out = 2 * n_bits

    columns = [[] for _ in range(n_out)]
    for i in range(n_bits):
        for j in range(n_bits):
            pp = builder.add_and(builder.input(i), builder.input(n_bits + j))
            columns[i + j].append(pp)

    while max(len(col) for col in columns) > 2:
        new_columns = [[] for _ in range(n_out)]
        for c in range(n_out):
            col = columns[c]
            i = 0
            while i + 2 < len(col):
                s, carry = _aig_full_adder(builder, col[i], col[i+1], col[i+2])
                new_columns[c].append(s)
                if c + 1 < n_out:
                    new_columns[c + 1].append(carry)
                i += 3
            while i < len(col):
                new_columns[c].append(col[i])
                i += 1
        columns = new_columns

    # Extract two rows for CLA addition
    row_a = []
    row_b = []
    for c in range(n_out):
        col = columns[c]
        row_a.append(col[0] if len(col) > 0 else 0)
        row_b.append(col[1] if len(col) > 1 else 0)

    # CLA final addition
    G, P = [], []
    for c in range(n_out):
        a, b = row_a[c], row_b[c]
        if a == 0 and b == 0:
            G.append(0)
            P.append(0)
        elif a == 0 or b == 0:
            G.append(0)
            P.append(a if b == 0 else b)
        else:
            g = builder.add_and(a, b)
            nab = builder.add_and(-a, -b)
            p = builder.add_and(-g, -nab)
            G.append(g)
            P.append(p)

    carries = [0]
    for c in range(n_out):
        c_prev = carries[c]
        if c_prev == 0:
            c_next = G[c]
        elif G[c] == 0 and P[c] == 0:
            c_next = 0
        else:
            pc = builder.add_and(P[c], c_prev)
            c_next = builder.add_or(G[c], pc)
        carries.append(c_next)

    outputs = []
    for c in range(n_out):
        p, cin = P[c], carries[c]
        if cin == 0:
            outputs.append(p)
        elif p == 0:
            outputs.append(cin)
        else:
            outputs.append(builder.add_xor(p, cin))
    return builder.build(outputs)


def _build_ripple_comparator_gt(n_bits: int) -> Circuit:
    """Build n-bit greater-than comparator: output 1 iff a > b (MSB-first ripple)."""
    builder = AIGBuilder(2 * n_bits)
    result = 0
    for i in range(n_bits):
        a_i = builder.input(i)
        b_i = builder.input(n_bits + i)
        gt_i = builder.add_and(a_i, -b_i)
        if result == 0:
            result = gt_i
        else:
            lt_i = builder.add_and(-a_i, b_i)
            eq_i = builder.add_and(-gt_i, -lt_i)
            result = builder.add_or(gt_i, builder.add_and(eq_i, result))
    return builder.build([result])


def _build_ripple_comparator_lt(n_bits: int) -> Circuit:
    """Build n-bit less-than comparator: output 1 iff a < b."""
    builder = AIGBuilder(2 * n_bits)
    result = 0
    for i in range(n_bits):
        a_i = builder.input(i)
        b_i = builder.input(n_bits + i)
        lt_i = builder.add_and(-a_i, b_i)
        if result == 0:
            result = lt_i
        else:
            gt_i = builder.add_and(a_i, -b_i)
            eq_i = builder.add_and(-lt_i, -gt_i)
            result = builder.add_or(lt_i, builder.add_and(eq_i, result))
    return builder.build([result])


def _try_structural_templates(tt: TruthTable) -> Circuit:
    """Try known circuit templates and return any that match the truth table."""
    from benchmark import verify_equivalence
    n = tt.n_inputs
    m = tt.n_outputs

    best = None

    # Try adder templates: 2k inputs, k+1 outputs
    if n % 2 == 0 and m == n // 2 + 1:
        k = n // 2
        for builder_fn in [_build_cla_adder, _build_ripple_carry_adder]:
            circ = builder_fn(k)
            if verify_equivalence(circ, tt):
                if best is None or circ.gate_count() < best.gate_count():
                    best = circ

    # Try multiplier templates: 2k inputs, 2k outputs
    if n % 2 == 0 and m == n:
        k = n // 2
        if k >= 2:
            for builder_fn in [_build_array_multiplier, _build_wallace_tree_multiplier,
                               _build_dadda_tree_multiplier, _build_wallace_cla_multiplier]:
                circ = builder_fn(k)
                if verify_equivalence(circ, tt):
                    if best is None or circ.gate_count() < best.gate_count():
                        best = circ

    # Try comparator: 2k inputs, 1 output
    if n % 2 == 0 and m == 1 and n >= 4:
        k = n // 2
        for build_fn in [_build_ripple_comparator_gt, _build_ripple_comparator_lt]:
            circ = build_fn(k)
            if verify_equivalence(circ, tt):
                if best is None or circ.gate_count() < best.gate_count():
                    best = circ

    return best


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

        # Method 3: SOP synthesis (limited to avoid exponential blowup)
        if tt.n_inputs <= 8:
            try:
                c3 = sop_synthesize(tt)
                if verify_equivalence(c3, tt):
                    candidates.append(('sop', c3))
            except Exception:
                pass

        # Method 4: Exact synthesis (for small functions)
        if self.use_exact and tt.n_inputs <= self.exact_limit:
            try:
                gate_limit = 15 if tt.n_inputs <= 5 else 12
                c4 = exact_synthesis(tt, max_gates=gate_limit)
                if c4 is not None and verify_equivalence(c4, tt):
                    candidates.append(('exact', c4))
            except Exception:
                pass

        # Method 5: Functional decomposition (dependency-aware multi-output)
        if tt.n_outputs > 1 and tt.n_inputs <= 10:
            try:
                c5 = functional_decompose(tt)
                if verify_equivalence(c5, tt):
                    candidates.append(('funcdec', c5))
            except Exception:
                pass

        # Method 6: Iterative improvement with variable order search
        if tt.n_inputs <= 10:
            try:
                from theories.aig_opt import iterative_improvement
                c6 = iterative_improvement(tt, time_budget=3.0)
                if c6 is not None and verify_equivalence(c6, tt):
                    candidates.append(('iterative', c6))
            except Exception:
                pass

        # Method 7: Structural templates (adder, multiplier, comparator)
        try:
            c_struct = _try_structural_templates(tt)
            if c_struct is not None and verify_equivalence(c_struct, tt):
                candidates.append(('structural', c_struct))
        except Exception:
            pass

        # Method 8: ABC-based synthesis (per-output read_truth + optimization)
        try:
            from theories.abc_polish import abc_synthesize_multi, abc_synthesize_single
            if tt.n_outputs == 1:
                c7 = abc_synthesize_single(tt.table[0], tt.n_inputs, 1)
                if c7 is not None:
                    builder7 = AIGBuilder(tt.n_inputs)
                    lit7 = _embed_circuit(c7, list(range(tt.n_inputs)), builder7)
                    c7_circ = builder7.build([lit7])
                    if verify_equivalence(c7_circ, tt):
                        candidates.append(('abc_synth', c7_circ))
            elif tt.n_inputs <= 12:
                c7 = abc_synthesize_multi(tt)
                if c7 is not None and verify_equivalence(c7, tt):
                    candidates.append(('abc_synth', c7))
        except Exception:
            pass

        # Method 9a: Multi-output shared exact synthesis
        if tt.n_outputs > 1 and tt.n_inputs <= 5:
            try:
                c9a = _shared_exact_multi(tt)
                if c9a is not None and verify_equivalence(c9a, tt):
                    candidates.append(('shared_exact', c9a))
            except Exception:
                pass

        # Method 9: E-graph equality saturation
        if tt.n_inputs <= 10:
            try:
                from theories.egraph import egraph_synthesize
                c9 = egraph_synthesize(tt, max_iterations=50, max_classes=3000)
                if c9 is not None and verify_equivalence(c9, tt):
                    candidates.append(('egraph', c9))
            except Exception:
                pass

        if not candidates:
            return shannon_decompose(tt)

        # Polish top candidates with ABC rewriting
        try:
            from theories.abc_polish import abc_polish
            candidates.sort(key=lambda x: x[1].gate_count())
            top_n = min(3, len(candidates))
            polished_candidates = []
            for name, circ in candidates[:top_n]:
                try:
                    p = abc_polish(circ, tt, max_rounds=3)
                    polished_candidates.append((name + '+abc', p))
                except Exception:
                    pass
            candidates.extend(polished_candidates)
        except ImportError:
            pass

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
