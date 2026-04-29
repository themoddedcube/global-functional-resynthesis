"""Variable-order search: try many Shannon decomposition orderings, keep best.

This is a lightweight form of the e-graph exploration - different variable
orderings produce different circuit structures and gate counts. Exhaustive
search over orderings for small functions; sampling for larger ones.
"""

from __future__ import annotations

import itertools
import random
from typing import Optional

from benchmark import TruthTable, Circuit, verify_equivalence


def var_order_search(tt: TruthTable, max_orderings: int = 1000) -> Circuit:
    """Try multiple variable orderings for Shannon decomposition, keep best."""
    from solver import AIGBuilder, _shannon_rec, CONST1

    n = tt.n_inputs
    best_circuit = None
    best_gates = float('inf')

    if n <= 7:
        orderings = list(itertools.permutations(range(n)))
        if len(orderings) > max_orderings:
            orderings = random.sample(orderings, max_orderings)
    else:
        orderings = []
        for _ in range(max_orderings):
            order = list(range(n))
            random.shuffle(order)
            orderings.append(tuple(order))

    for order in orderings:
        builder = AIGBuilder(n)
        outputs = []
        cache = {}
        for j in range(tt.n_outputs):
            single_tt = TruthTable(n, 1, (tt.table[j],))
            lit = _shannon_rec_ordered(single_tt, list(order), builder, cache)
            outputs.append(lit)
        circ = builder.build(outputs)
        gc = circ.gate_count()
        if gc < best_gates:
            if verify_equivalence(circ, tt):
                best_gates = gc
                best_circuit = circ

    return best_circuit


def _shannon_rec_ordered(tt: TruthTable, var_order: list[int],
                         builder, cache: dict) -> int:
    """Shannon decomposition using specified variable ordering."""
    from solver import CONST1

    t = tt.table[0]
    n = tt.n_inputs

    cache_key = (t, tuple(var_order))
    if cache_key in cache:
        return cache[cache_key]

    all_bits = (1 << (1 << n)) - 1
    if t == 0:
        return 0
    if t == all_bits:
        return CONST1

    if n == 1:
        inp = builder.input(var_order[0])
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

    # Try each position in order until we find a variable the function depends on
    var_idx = 0
    for i in range(n):
        if tt.depends_on(i):
            var_idx = i
            break

    # Use the first variable in the order that the function depends on
    for idx_in_order, orig_var in enumerate(var_order):
        pos = None
        for i in range(n):
            if var_order[i] == var_order[idx_in_order]:
                pos = i
                break
        if pos is not None and tt.depends_on(pos):
            var_idx = pos
            break

    original_input = var_order[var_idx]
    cof0 = tt.cofactor(var_idx, 0)
    cof1 = tt.cofactor(var_idx, 1)
    remaining_order = [v for i, v in enumerate(var_order) if i != var_idx]

    lit0 = _shannon_rec_ordered(cof0, remaining_order, builder, cache)
    lit1 = _shannon_rec_ordered(cof1, remaining_order, builder, cache)

    sel = builder.input(original_input)
    if lit0 == lit1:
        result = lit0
    elif lit0 == 0:
        result = builder.add_and(sel, lit1)
    elif lit1 == 0:
        result = builder.add_and(-sel, lit0)
    else:
        result = builder.add_mux(sel, lit1, lit0)

    cache[cache_key] = result
    return result


def functional_decompose(tt: TruthTable) -> Circuit:
    """Try to decompose multi-output function by finding shared sub-functions.

    Key insight: for an adder, the carry chain means sum[i] depends on
    all lower inputs. Group outputs by their input dependencies and
    synthesize groups that share inputs together.
    """
    from solver import AIGBuilder, CONST1

    n = tt.n_inputs
    n_out = tt.n_outputs
    builder = AIGBuilder(n)

    # For each output, find which variables it depends on
    deps = []
    for j in range(n_out):
        d = set()
        for v in range(n):
            if tt.depends_on(v, j):
                d.add(v)
        deps.append(d)

    # Sort outputs by dependency set size (smallest first)
    order = sorted(range(n_out), key=lambda j: len(deps[j]))

    outputs = [None] * n_out
    cache = {}

    for j in order:
        single_tt = TruthTable(n, 1, (tt.table[j],))

        # Use only the variables this output depends on
        dep_vars = sorted(deps[j])
        if not dep_vars:
            outputs[j] = 0
            continue

        # If output depends on few variables, use exact synthesis
        if len(dep_vars) <= 5:
            # Reduce truth table to only relevant variables
            reduced_tt = _reduce_to_vars(single_tt, dep_vars)
            from solver import exact_synthesis
            try:
                exact_circ = exact_synthesis(reduced_tt, max_gates=15)
                if exact_circ is not None and verify_equivalence(exact_circ, reduced_tt):
                    # Remap exact circuit into the builder
                    lit = _embed_circuit(exact_circ, dep_vars, builder)
                    outputs[j] = lit
                    continue
            except Exception:
                pass

        # Variable order search on this output using shared cache
        best_order = sorted(dep_vars)
        full_order = best_order + [v for v in range(n) if v not in dep_vars]
        lit = _shannon_rec_ordered(single_tt, full_order, builder, cache)
        outputs[j] = lit

    return builder.build(outputs)


def _reduce_to_vars(tt: TruthTable, vars: list[int]) -> TruthTable:
    """Reduce a truth table to only use specified variables."""
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


def _embed_circuit(circ: Circuit, var_map: list[int], builder) -> int:
    """Embed a small circuit into a larger builder using variable mapping."""
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
