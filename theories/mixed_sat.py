"""SAT-based exact synthesis with mixed gate library {AND2, OR2, XOR2, NOT1}.

Each gate costs 1. This differs from AIG exact synthesis where only AND gates
are used with free inversions. Here AND, OR, XOR each take 2 inputs and NOT
takes 1 input, all costing 1 gate.

Uses CEGAR (CounterExample-Guided Abstraction Refinement) for scalability:
start with a subset of input patterns, find a candidate circuit, verify
against all patterns, add counterexamples on failure.
"""

from __future__ import annotations

import time
from typing import Optional

from pysat.solvers import Cadical153

from benchmark import TruthTable


# Gate type constants
GATE_AND = 0
GATE_OR = 1
GATE_XOR = 2
GATE_NOT = 3
GATE_NAMES = ['AND', 'OR', 'XOR', 'NOT']


def mixed_exact_synthesis(
    tt: TruthTable,
    max_gates: int = 20,
    timeout_s: float = 300.0,
    verbose: bool = False,
) -> Optional[dict]:
    """Find minimum-gate circuit using {AND2, OR2, XOR2, NOT1} library.

    Returns a dict describing the circuit:
        {
            'gates': [(type_str, input_indices...), ...],
            'outputs': [(node_index, inverted), ...],
            'gate_count': int,
            'n_inputs': int,
        }
    or None if no circuit with <= max_gates was found.

    Node indices: 0..n_inputs-1 are primary inputs, n_inputs.. are gates.
    """
    n = tt.n_inputs
    t_start = time.time()

    # Handle trivial cases
    for num_gates in range(0, max_gates + 1):
        if time.time() - t_start > timeout_s:
            if verbose:
                print(f"  Timeout after {timeout_s}s")
            return None

        if verbose:
            print(f"  Trying {num_gates} gates...", end=" ", flush=True)

        result = _cegar_solve(tt, num_gates, timeout_s - (time.time() - t_start), verbose)

        if result is not None:
            if verbose:
                print(f"SAT! Found {num_gates}-gate circuit")
            return result
        else:
            if verbose:
                print("UNSAT")

    return None


def mixed_exact_synthesis_range(
    tt: TruthTable,
    min_gates: int,
    max_gates: int,
    timeout_s: float = 300.0,
    verbose: bool = False,
) -> Optional[dict]:
    """Search for circuit with gate count in [min_gates, max_gates].

    Useful for trying to beat a known upper bound: set max_gates = known - 1.
    Returns the smallest circuit found, or None.
    """
    t_start = time.time()
    for num_gates in range(min_gates, max_gates + 1):
        if time.time() - t_start > timeout_s:
            if verbose:
                print(f"  Timeout after {timeout_s}s")
            return None

        if verbose:
            print(f"  Trying {num_gates} gates...", end=" ", flush=True)

        result = _cegar_solve(tt, num_gates, timeout_s - (time.time() - t_start), verbose)

        if result is not None:
            if verbose:
                print(f"SAT! Found {num_gates}-gate circuit")
            return result
        else:
            if verbose:
                print("UNSAT")

    return None


def _cegar_solve(
    tt: TruthTable,
    num_gates: int,
    timeout_s: float,
    verbose: bool = False,
) -> Optional[dict]:
    """Incremental CEGAR: build solver once, add counterexample patterns incrementally."""
    n = tt.n_inputs
    size = 1 << n
    g = num_gates
    n_out = tt.n_outputs
    total_nodes = n + g

    if num_gates == 0:
        return _try_trivial(tt)

    # Choose initial patterns
    if size <= 32:
        init_patterns = list(range(size))
    else:
        import random
        init_patterns = [0, size - 1]
        for v in range(n):
            init_patterns.append(1 << v)
        for j in range(n_out):
            for v in range(n):
                if tt.depends_on(v, j):
                    for trial in range(min(16, size)):
                        p = random.randint(0, size - 1)
                        p0 = p & ~(1 << v)
                        p1 = p | (1 << v)
                        if ((tt.table[j] >> p0) & 1) != ((tt.table[j] >> p1) & 1):
                            init_patterns.extend([p0, p1])
                            break
        init_patterns = sorted(set(init_patterns))
        while len(init_patterns) < 16:
            p = random.randint(0, size - 1)
            if p not in set(init_patterns):
                init_patterns.append(p)

    solver = Cadical153()
    var_count = [0]

    def new_var():
        var_count[0] += 1
        return var_count[0]

    def new_vars(count):
        result = []
        for _ in range(count):
            var_count[0] += 1
            result.append(var_count[0])
        return result

    # =========================================================
    # STRUCTURE VARIABLES (created once)
    # =========================================================

    type_var = []
    for gi in range(g):
        type_var.append(new_vars(4))

    for gi in range(g):
        solver.add_clause(type_var[gi])
        for a in range(4):
            for b in range(a + 1, 4):
                solver.add_clause([-type_var[gi][a], -type_var[gi][b]])

    sel0 = []
    sel1 = []
    for gi in range(g):
        max_src = n + gi
        s0 = new_vars(max_src)
        s1 = new_vars(max_src)
        sel0.append(s0)
        sel1.append(s1)

        solver.add_clause(s0)
        for a in range(max_src):
            for b in range(a + 1, max_src):
                solver.add_clause([-s0[a], -s0[b]])

        solver.add_clause(s1)
        for a in range(max_src):
            for b in range(a + 1, max_src):
                solver.add_clause([-s1[a], -s1[b]])

        # Symmetry: for commutative gates, sel0 <= sel1
        for a in range(max_src):
            for b in range(a):
                for t in [GATE_AND, GATE_OR, GATE_XOR]:
                    solver.add_clause([-type_var[gi][t], -s0[a], -s1[b]])

    out_sel = []
    for j in range(n_out):
        os = new_vars(total_nodes)
        out_sel.append(os)
        solver.add_clause(os)
        for a in range(total_nodes):
            for b in range(a + 1, total_nodes):
                solver.add_clause([-os[a], -os[b]])

    # =========================================================
    # SIMULATION VARIABLES (added per pattern)
    # =========================================================
    sim = {}  # (node, pidx) -> var
    added_patterns = set()
    pattern_to_pidx = {}

    def add_pattern(p):
        """Add simulation constraints for a new input pattern."""
        if p in added_patterns:
            return
        added_patterns.add(p)
        pidx = len(pattern_to_pidx)
        pattern_to_pidx[p] = pidx

        # Input simulation values
        for node in range(n):
            v = new_var()
            sim[(node, pidx)] = v
            val = (p >> node) & 1
            solver.add_clause([v if val else -v])

        # Gate simulation values
        for gi in range(g):
            gate_node = n + gi
            max_src = n + gi
            out_v = new_var()
            sim[(gate_node, pidx)] = out_v

            eff0 = new_var()
            eff1 = new_var()

            for j in range(max_src):
                sj = sim[(j, pidx)]
                s0j = sel0[gi][j]
                solver.add_clause([-s0j, -eff0, sj])
                solver.add_clause([-s0j, eff0, -sj])

            for k in range(max_src):
                sk = sim[(k, pidx)]
                s1k = sel1[gi][k]
                solver.add_clause([-s1k, -eff1, sk])
                solver.add_clause([-s1k, eff1, -sk])

            # AND
            t_and = type_var[gi][GATE_AND]
            solver.add_clause([-t_and, -out_v, eff0])
            solver.add_clause([-t_and, -out_v, eff1])
            solver.add_clause([-t_and, out_v, -eff0, -eff1])

            # OR
            t_or = type_var[gi][GATE_OR]
            solver.add_clause([-t_or, out_v, -eff0])
            solver.add_clause([-t_or, out_v, -eff1])
            solver.add_clause([-t_or, -out_v, eff0, eff1])

            # XOR
            t_xor = type_var[gi][GATE_XOR]
            solver.add_clause([-t_xor, -out_v, eff0, eff1])
            solver.add_clause([-t_xor, out_v, eff0, -eff1])
            solver.add_clause([-t_xor, out_v, -eff0, eff1])
            solver.add_clause([-t_xor, -out_v, -eff0, -eff1])

            # NOT
            t_not = type_var[gi][GATE_NOT]
            solver.add_clause([-t_not, -out_v, -eff0])
            solver.add_clause([-t_not, out_v, eff0])

        # Output constraints for this pattern
        for j in range(n_out):
            expected = (tt.table[j] >> p) & 1
            for node in range(total_nodes):
                os = out_sel[j][node]
                sv = sim[(node, pidx)]
                if expected:
                    solver.add_clause([-os, sv])
                else:
                    solver.add_clause([-os, -sv])

    # Add initial patterns
    for p in init_patterns:
        add_pattern(p)

    # =========================================================
    # CEGAR LOOP
    # =========================================================
    t_start = time.time()
    iteration = 0

    while True:
        if time.time() - t_start > timeout_s:
            solver.delete()
            return None

        iteration += 1
        sat = solver.solve()

        if not sat:
            solver.delete()
            return None

        model = set(solver.get_model())

        # Extract solution
        gates = []
        for gi in range(g):
            max_src = n + gi
            gate_type = None
            for t in range(4):
                if type_var[gi][t] in model:
                    gate_type = t
                    break
            in0 = None
            for j in range(max_src):
                if sel0[gi][j] in model:
                    in0 = j
                    break
            in1 = None
            for k in range(max_src):
                if sel1[gi][k] in model:
                    in1 = k
                    break
            if gate_type == GATE_NOT:
                gates.append((GATE_NAMES[gate_type], in0))
            else:
                gates.append((GATE_NAMES[gate_type], in0, in1))

        outputs = []
        for j in range(n_out):
            node = None
            for nd in range(total_nodes):
                if out_sel[j][nd] in model:
                    node = nd
                    break
            outputs.append((node, False))

        # Verify against ALL patterns
        counterexamples = []
        for p in range(size):
            expected = tuple((tt.table[j] >> p) & 1 for j in range(n_out))
            got = _simulate_mixed(n, gates, outputs, p)
            if got != expected:
                counterexamples.append(p)
                if len(counterexamples) >= 16:
                    break

        if not counterexamples:
            solver.delete()
            if verbose:
                print(f"(CEGAR: {iteration} iters, {len(added_patterns)} patterns) ", end="")
            return {
                'gates': gates,
                'outputs': outputs,
                'gate_count': g,
                'n_inputs': n,
            }

        # Add counterexamples incrementally
        for p in counterexamples:
            add_pattern(p)


def _try_trivial(tt: TruthTable) -> Optional[dict]:
    """Check if the function can be realized with 0 gates (constants/literals)."""
    n = tt.n_inputs
    size = 1 << n
    all_ones = (1 << size) - 1

    outputs = []
    for j in range(tt.n_outputs):
        t = tt.table[j]
        if t == 0:
            outputs.append((None, False, 0))  # constant 0
        elif t == all_ones:
            outputs.append((None, False, 1))  # constant 1
        else:
            # Check single literal
            found = False
            for v in range(n):
                pos_mask = 0
                for i in range(size):
                    if (i >> v) & 1:
                        pos_mask |= (1 << i)
                if t == pos_mask:
                    outputs.append((v, False, None))  # input v
                    found = True
                    break
                elif t == (all_ones ^ pos_mask):
                    outputs.append((v, True, None))  # NOT input v
                    found = True
                    break
            if not found:
                return None

    # All outputs are trivial
    result_outputs = []
    for v_idx, inverted, const in outputs:
        if const is not None:
            # Need a way to represent constants -- we'll skip this for now
            # Constants require 0 gates but we represent them specially
            result_outputs.append((-1, const))  # -1 = constant
        else:
            result_outputs.append((v_idx, inverted))

    return {
        'gates': [],
        'outputs': result_outputs,
        'gate_count': 0,
        'n_inputs': tt.n_inputs,
    }


def decompose_and_synthesize(
    tt: TruthTable,
    max_gates_per_piece: int = 12,
    timeout_s: float = 120.0,
    verbose: bool = False,
) -> Optional[dict]:
    """Decompose a large function using Shannon expansion, then synthesize pieces.

    For functions with many inputs, split on the variable that gives the simplest
    cofactors, synthesize each cofactor, then combine with a MUX structure.
    MUX(sel, a, b) = OR(AND(sel, a), AND(NOT(sel), b)) = 4 gates.
    But with mixed gates: OR(AND(sel, a), AND(NOT(sel), b))
    """
    n = tt.n_inputs
    if n <= 5:
        # Small enough for direct exact synthesis
        return mixed_exact_synthesis(tt, max_gates_per_piece, timeout_s, verbose)

    assert tt.n_outputs == 1, "Decomposition only for single-output functions"
    t = tt.table[0]

    t_start = time.time()

    # Find best variable to split on (minimize max cofactor complexity)
    best_var = None
    best_score = float('inf')
    for v in range(n):
        if not tt.depends_on(v, 0):
            continue
        f0 = tt.negative_cofactor(v)
        f1 = tt.positive_cofactor(v)
        # Score: number of essential variables in each cofactor
        deps0 = sum(1 for w in range(f0.n_inputs) if f0.depends_on(w, 0))
        deps1 = sum(1 for w in range(f1.n_inputs) if f1.depends_on(w, 0))
        onset0 = bin(f0.table[0]).count('1')
        onset1 = bin(f1.table[0]).count('1')
        # Prefer splits where one cofactor is simple (few deps, small onset)
        score = min(deps0, deps1) + max(deps0, deps1) * 0.5
        # Bonus if a cofactor is constant
        if onset0 == 0 or onset0 == (1 << f0.n_inputs):
            score -= 10
        if onset1 == 0 or onset1 == (1 << f1.n_inputs):
            score -= 10
        if score < best_score:
            best_score = score
            best_var = v

    if best_var is None:
        return mixed_exact_synthesis(tt, max_gates_per_piece, timeout_s, verbose)

    f0 = tt.negative_cofactor(best_var)
    f1 = tt.positive_cofactor(best_var)

    if verbose:
        print(f"  Splitting on var {best_var}: f0 has {bin(f0.table[0]).count('1')} onset, "
              f"f1 has {bin(f1.table[0]).count('1')} onset")

    # Synthesize each cofactor (recursively decompose if needed)
    remaining = timeout_s - (time.time() - t_start)
    r0 = decompose_and_synthesize(f0, max_gates_per_piece, remaining / 2, verbose)
    if r0 is None:
        return None

    remaining = timeout_s - (time.time() - t_start)
    r1 = decompose_and_synthesize(f1, max_gates_per_piece, remaining, verbose)
    if r1 is None:
        return None

    # Combine: MUX(var, f1, f0) = OR(AND(var, f1_out), AND(NOT(var), f0_out))
    # This costs 4 gates: NOT, AND, AND, OR (if neither cofactor is constant)
    # But if a cofactor is constant 0, we save: AND(var, f1) only needs 1 gate
    # If constant 1: AND(NOT(var), 1) = NOT(var), 1 gate

    # Build the combined circuit
    # The cofactors operate on n-1 inputs (var removed).
    # We need to remap: in the original n inputs, var is at position best_var.
    # After cofactor removal, inputs 0..best_var-1 stay, best_var..n-2 = original best_var+1..n-1.

    # For the combined circuit, we use all n original inputs.
    # The cofactor circuits use n-1 inputs (the n-1 inputs excluding best_var).
    # We need to remap cofactor input indices to combined input indices.

    combined_gates = []
    n_combined_inputs = n

    # Map cofactor input index -> original input index
    cofactor_to_orig = []
    for i in range(n):
        if i != best_var:
            cofactor_to_orig.append(i)

    # Add f0's gates, remapping input indices
    f0_remap = {}  # cofactor node index -> combined node index
    for ci in range(f0.n_inputs):
        f0_remap[ci] = cofactor_to_orig[ci]  # maps to original input

    for gi, gate in enumerate(r0['gates']):
        gtype = gate[0]
        old_node = f0.n_inputs + gi
        new_node = n_combined_inputs + len(combined_gates)
        f0_remap[old_node] = new_node
        if gtype == 'NOT':
            combined_gates.append(('NOT', f0_remap[gate[1]]))
        else:
            combined_gates.append((gtype, f0_remap[gate[1]], f0_remap[gate[2]]))

    f0_out_node, f0_out_inv = r0['outputs'][0]
    if f0_out_node == -1:
        f0_result = ('const', f0_out_inv)  # constant value
    else:
        f0_result = ('node', f0_remap[f0_out_node])

    # Add f1's gates similarly
    f1_remap = {}
    for ci in range(f1.n_inputs):
        f1_remap[ci] = cofactor_to_orig[ci]

    for gi, gate in enumerate(r1['gates']):
        gtype = gate[0]
        old_node = f1.n_inputs + gi
        new_node = n_combined_inputs + len(combined_gates)
        f1_remap[old_node] = new_node
        if gtype == 'NOT':
            combined_gates.append(('NOT', f1_remap[gate[1]]))
        else:
            combined_gates.append((gtype, f1_remap[gate[1]], f1_remap[gate[2]]))

    f1_out_node, f1_out_inv = r1['outputs'][0]
    if f1_out_node == -1:
        f1_result = ('const', f1_out_inv)
    else:
        f1_result = ('node', f1_remap[f1_out_node])

    # Build MUX: y = OR(AND(var, f1), AND(NOT(var), f0))
    sel_var = best_var  # original input index

    # Handle special cases
    if f0_result[0] == 'const' and f0_result[1] == 0:
        # y = AND(var, f1)
        if f1_result[0] == 'const':
            if f1_result[1] == 0:
                # y = 0
                return {
                    'gates': combined_gates,
                    'outputs': [(-1, 0)],
                    'gate_count': len(combined_gates),
                    'n_inputs': n,
                }
            else:
                # y = var (just the selector)
                return {
                    'gates': combined_gates,
                    'outputs': [(sel_var, False)],
                    'gate_count': len(combined_gates),
                    'n_inputs': n,
                }
        and_node = n_combined_inputs + len(combined_gates)
        combined_gates.append(('AND', sel_var, f1_result[1]))
        return {
            'gates': combined_gates,
            'outputs': [(and_node, False)],
            'gate_count': len(combined_gates),
            'n_inputs': n,
        }

    if f1_result[0] == 'const' and f1_result[1] == 0:
        # y = AND(NOT(var), f0)
        if f0_result[0] == 'const':
            if f0_result[1] == 0:
                return {
                    'gates': combined_gates,
                    'outputs': [(-1, 0)],
                    'gate_count': len(combined_gates),
                    'n_inputs': n,
                }
            else:
                # y = NOT(var)
                not_node = n_combined_inputs + len(combined_gates)
                combined_gates.append(('NOT', sel_var))
                return {
                    'gates': combined_gates,
                    'outputs': [(not_node, False)],
                    'gate_count': len(combined_gates),
                    'n_inputs': n,
                }
        not_node = n_combined_inputs + len(combined_gates)
        combined_gates.append(('NOT', sel_var))
        and_node = n_combined_inputs + len(combined_gates)
        combined_gates.append(('AND', not_node, f0_result[1]))
        return {
            'gates': combined_gates,
            'outputs': [(and_node, False)],
            'gate_count': len(combined_gates),
            'n_inputs': n,
        }

    # General case: MUX
    # NOT(var)
    not_node = n_combined_inputs + len(combined_gates)
    combined_gates.append(('NOT', sel_var))

    # AND(var, f1)
    f1_node = f1_result[1] if f1_result[0] == 'node' else None
    and1_node = n_combined_inputs + len(combined_gates)
    if f1_result[0] == 'const' and f1_result[1] == 1:
        # AND(var, 1) = var -- but we still need a gate for it
        # Actually just use sel_var directly
        and1_node = sel_var
    else:
        combined_gates.append(('AND', sel_var, f1_node))

    # AND(NOT(var), f0)
    f0_node = f0_result[1] if f0_result[0] == 'node' else None
    and0_node = n_combined_inputs + len(combined_gates)
    if f0_result[0] == 'const' and f0_result[1] == 1:
        and0_node = not_node
    else:
        combined_gates.append(('AND', not_node, f0_node))

    # OR
    or_node = n_combined_inputs + len(combined_gates)
    combined_gates.append(('OR', and0_node, and1_node))

    return {
        'gates': combined_gates,
        'outputs': [(or_node, False)],
        'gate_count': len(combined_gates),
        'n_inputs': n,
    }


def _simulate_mixed(n_inputs: int, gates: list, outputs: list, pattern: int) -> tuple:
    """Simulate a mixed-gate circuit on a single input pattern."""
    vals = {}
    for i in range(n_inputs):
        vals[i] = (pattern >> i) & 1

    for gi, gate in enumerate(gates):
        node = n_inputs + gi
        gtype = gate[0]
        if gtype == 'AND':
            vals[node] = vals[gate[1]] & vals[gate[2]]
        elif gtype == 'OR':
            vals[node] = vals[gate[1]] | vals[gate[2]]
        elif gtype == 'XOR':
            vals[node] = vals[gate[1]] ^ vals[gate[2]]
        elif gtype == 'NOT':
            vals[node] = 1 - vals[gate[1]]

    result = []
    for node, inv in outputs:
        if node == -1:
            # Constant
            result.append(inv)  # inv holds the constant value
        else:
            v = vals[node]
            if inv:
                v = 1 - v
            result.append(v)

    return tuple(result)


def verify_mixed_circuit(n_inputs: int, gates: list, outputs: list, tt: TruthTable) -> bool:
    """Verify a mixed-gate circuit against a truth table."""
    size = 1 << n_inputs
    for p in range(size):
        expected = tuple((tt.table[j] >> p) & 1 for j in range(tt.n_outputs))
        got = _simulate_mixed(n_inputs, gates, outputs, p)
        if got != expected:
            return False
    return True


def print_mixed_circuit(n_inputs: int, gates: list, outputs: list, input_names: list[str] = None):
    """Pretty-print a mixed-gate circuit."""
    if input_names is None:
        input_names = [f"x{i}" for i in range(n_inputs)]

    node_names = list(input_names)

    for gi, gate in enumerate(gates):
        gtype = gate[0]
        if gtype == 'NOT':
            name = f"g{gi} = NOT({node_names[gate[1]]})"
        else:
            name = f"g{gi} = {gtype}({node_names[gate[1]]}, {node_names[gate[2]]})"
        node_names.append(f"g{gi}")
        print(f"  {name}")

    for j, (node, inv) in enumerate(outputs):
        if node == -1:
            print(f"  y{j} = {inv}")
        else:
            nname = node_names[node]
            if inv:
                print(f"  y{j} = NOT({nname})")
            else:
                print(f"  y{j} = {nname}")


def extract_subproblem(tt: TruthTable, output_indices: list[int],
                       used_inputs: list[int] = None) -> TruthTable:
    """Extract a sub-truth-table for a subset of outputs.

    If used_inputs is provided, project onto only those input variables.
    """
    if used_inputs is not None:
        # Project truth table onto subset of inputs
        n_new = len(used_inputs)
        new_tables = []
        for j in output_indices:
            new_t = 0
            for new_p in range(1 << n_new):
                # Map new_p back to original pattern
                orig_p = 0
                for ni, oi in enumerate(used_inputs):
                    if (new_p >> ni) & 1:
                        orig_p |= (1 << oi)
                if (tt.table[j] >> orig_p) & 1:
                    new_t |= (1 << new_p)
            new_tables.append(new_t)
        return TruthTable(n_new, len(output_indices), tuple(new_tables))
    else:
        tables = tuple(tt.table[j] for j in output_indices)
        return TruthTable(tt.n_inputs, len(output_indices), tables)


# =========================================================================
# Multi-output shared-gate synthesis
# =========================================================================

def mixed_exact_multi_output(
    tt: TruthTable,
    max_gates: int = 30,
    timeout_s: float = 300.0,
    verbose: bool = False,
) -> Optional[dict]:
    """Multi-output exact synthesis with shared gates.

    This handles the case where multiple outputs can share internal gates,
    potentially reducing the total gate count vs independent synthesis.
    """
    return mixed_exact_synthesis(tt, max_gates, timeout_s, verbose)


# =========================================================================
# Convenience: synthesize individual outputs of FP4 multiplier
# =========================================================================

def synthesize_fp4_outputs(blif_path: str = 'circuits/fp4_63gate.blif',
                           timeout_per_output: float = 60.0,
                           verbose: bool = True):
    """Synthesize each output of the FP4 multiplier independently."""
    from theories.blif_io import blif_to_truth_table

    tt = blif_to_truth_table(blif_path)
    if tt is None:
        print("Failed to read BLIF file")
        return

    input_names = ['a3', 'a2', 'a1', 'a0', 'b3', 'b2', 'b1', 'b0']
    results = {}
    total_gates = 0

    for j in range(tt.n_outputs):
        # Find which inputs this output depends on
        deps = [v for v in range(tt.n_inputs) if tt.depends_on(v, j)]
        dep_names = [input_names[d] for d in deps]

        print(f"\n{'='*60}")
        print(f"Output y{j}: depends on {len(deps)} inputs: {dep_names}")

        # Extract subproblem with only relevant inputs
        sub_tt = extract_subproblem(tt, [j], deps)
        print(f"  Sub-problem: {sub_tt.n_inputs} inputs, 1 output")

        result = mixed_exact_synthesis(
            sub_tt,
            max_gates=20,
            timeout_s=timeout_per_output,
            verbose=verbose,
        )

        if result is not None:
            gc = result['gate_count']
            total_gates += gc
            results[j] = result
            print(f"  Result: {gc} gates")
            print_mixed_circuit(sub_tt.n_inputs, result['gates'], result['outputs'],
                               dep_names)

            # Verify
            ok = verify_mixed_circuit(sub_tt.n_inputs, result['gates'], result['outputs'], sub_tt)
            print(f"  Verified: {ok}")
        else:
            print(f"  No solution found within timeout")
            results[j] = None

    print(f"\n{'='*60}")
    print(f"Total gates (independent outputs): {total_gates}")
    print(f"Original: 63 gates")
    print(f"Reduction: {total_gates}/63 = {total_gates/63:.3f}")

    return results


def synthesize_fp4_multi_output(
    blif_path: str = 'circuits/fp4_63gate.blif',
    output_groups: list[list[int]] = None,
    timeout_per_group: float = 120.0,
    verbose: bool = True,
):
    """Synthesize groups of outputs with shared gates.

    output_groups: list of lists of output indices to synthesize together.
    If None, tries pairs of adjacent outputs.
    """
    from theories.blif_io import blif_to_truth_table

    tt = blif_to_truth_table(blif_path)
    if tt is None:
        print("Failed to read BLIF file")
        return

    input_names = ['a3', 'a2', 'a1', 'a0', 'b3', 'b2', 'b1', 'b0']

    if output_groups is None:
        # Default: try pairs
        output_groups = [[0, 1], [2, 3], [4, 5], [6, 7], [8]]

    total_gates = 0
    results = {}

    for group in output_groups:
        group_name = ','.join(f'y{j}' for j in group)

        # Find union of input dependencies
        all_deps = set()
        for j in group:
            for v in range(tt.n_inputs):
                if tt.depends_on(v, j):
                    all_deps.add(v)
        deps = sorted(all_deps)
        dep_names = [input_names[d] for d in deps]

        print(f"\n{'='*60}")
        print(f"Outputs [{group_name}]: {len(deps)} inputs: {dep_names}")

        sub_tt = extract_subproblem(tt, group, deps)
        print(f"  Sub-problem: {sub_tt.n_inputs} inputs, {sub_tt.n_outputs} outputs")

        # Estimate max gates needed
        max_g = min(30, len(deps) * len(group) * 3)

        result = mixed_exact_synthesis(
            sub_tt,
            max_gates=max_g,
            timeout_s=timeout_per_group,
            verbose=verbose,
        )

        if result is not None:
            gc = result['gate_count']
            total_gates += gc
            results[tuple(group)] = result
            print(f"  Result: {gc} gates")
            print_mixed_circuit(sub_tt.n_inputs, result['gates'], result['outputs'],
                               dep_names)
            ok = verify_mixed_circuit(sub_tt.n_inputs, result['gates'], result['outputs'], sub_tt)
            print(f"  Verified: {ok}")
        else:
            print(f"  No solution found within timeout")
            results[tuple(group)] = None

    print(f"\n{'='*60}")
    solved_gates = sum(r['gate_count'] for r in results.values() if r is not None)
    print(f"Total gates (grouped outputs): {solved_gates}")
    print(f"Original: 63 gates")

    return results


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--multi':
        synthesize_fp4_multi_output()
    elif len(sys.argv) > 1 and sys.argv[1] == '--full':
        # Try the full problem
        from theories.blif_io import blif_to_truth_table
        tt = blif_to_truth_table('circuits/fp4_63gate.blif')
        print(f"Full problem: {tt.n_inputs} inputs, {tt.n_outputs} outputs")
        print("Trying to beat 63 gates...")
        result = mixed_exact_synthesis_range(tt, 55, 62, timeout_s=600, verbose=True)
        if result:
            print(f"\nFound {result['gate_count']}-gate circuit!")
            ok = verify_mixed_circuit(tt.n_inputs, result['gates'], result['outputs'], tt)
            print(f"Verified: {ok}")
        else:
            print("\nNo improvement found within timeout")
    else:
        synthesize_fp4_outputs()
