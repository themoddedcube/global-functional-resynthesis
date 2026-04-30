"""AIG optimization passes: rewriting, balancing, and resubstitution.

These are the core techniques from ABC that do local optimization on AIGs.
Even though they're "local," they're essential as a polishing step.
"""

from __future__ import annotations

from typing import Optional

from benchmark import TruthTable, Circuit, AIGNode, verify_equivalence


def balance(circuit: Circuit) -> Circuit:
    """Rebuild the AIG as a balanced tree to minimize depth."""
    tt = circuit.to_truth_table()
    return _build_balanced(tt)


def _build_balanced(tt: TruthTable) -> Circuit:
    """Build a balanced AIG from truth table using recursive factoring."""
    from solver import AIGBuilder, CONST1

    n = tt.n_inputs
    builder = AIGBuilder(n)
    outputs = []
    cache = {}

    for j in range(tt.n_outputs):
        single_bits = tt.table[j]
        lit = _balanced_rec(single_bits, n, list(range(n)), builder, cache)
        outputs.append(lit)

    return builder.build(outputs)


def _balanced_rec(bits: int, n: int, orig_vars: list[int],
                  builder, cache: dict) -> int:
    """Balanced Shannon decomposition choosing median variable."""
    from solver import CONST1

    cache_key = (bits, tuple(orig_vars))
    if cache_key in cache:
        return cache[cache_key]

    all_bits = (1 << (1 << n)) - 1
    if bits == 0:
        return 0
    if bits == all_bits:
        return CONST1

    if n == 1:
        inp = builder.input(orig_vars[0])
        if bits == 0b10:
            result = inp
        elif bits == 0b01:
            result = -inp
        elif bits == 0b11:
            result = CONST1
        else:
            result = 0
        cache[cache_key] = result
        return result

    # Choose middle variable for balanced tree
    mid = n // 2
    var_idx = mid

    tt_temp = TruthTable(n, 1, (bits,))
    # Find a variable the function actually depends on, preferring middle
    for offset in range(n):
        candidates = []
        if mid + offset < n:
            candidates.append(mid + offset)
        if mid - offset >= 0 and mid - offset != mid + offset:
            candidates.append(mid - offset)
        for idx in candidates:
            if tt_temp.depends_on(idx):
                var_idx = idx
                break
        else:
            continue
        break

    original_input = orig_vars[var_idx]

    # Compute cofactors
    cof0_bits = _cofactor_bits(bits, var_idx, 0, n)
    cof1_bits = _cofactor_bits(bits, var_idx, 1, n)

    remaining = [v for i, v in enumerate(orig_vars) if i != var_idx]

    lit0 = _balanced_rec(cof0_bits, n - 1, remaining, builder, cache)
    lit1 = _balanced_rec(cof1_bits, n - 1, remaining, builder, cache)

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


def _cofactor_bits(bits: int, var_idx: int, value: int, n: int) -> int:
    """Compute cofactor as (n-1)-input truth table bits."""
    result = 0
    new_n = n - 1
    for p in range(1 << new_n):
        lo = p & ((1 << var_idx) - 1)
        hi = (p >> var_idx) << (var_idx + 1)
        orig_p = hi | (value << var_idx) | lo
        if (bits >> orig_p) & 1:
            result |= (1 << p)
    return result


def optimize_multi_pass(circuit: Circuit, tt: TruthTable,
                        max_passes: int = 5) -> Circuit:
    """Apply multiple optimization passes, keeping improvements."""
    current = circuit
    best_gates = current.gate_count()

    for _ in range(max_passes):
        # Try balanced rebuild
        balanced = _build_balanced(tt)
        if balanced.gate_count() < best_gates and verify_equivalence(balanced, tt):
            current = balanced
            best_gates = balanced.gate_count()

        # Try rebuilding via SOP with different methods
        from solver import sop_synthesize, pprm_decompose, shannon_decompose
        for method in [sop_synthesize, pprm_decompose, shannon_decompose]:
            try:
                c = method(tt)
                if c.gate_count() < best_gates and verify_equivalence(c, tt):
                    current = c
                    best_gates = c.gate_count()
            except Exception:
                pass

    return current


def iterative_improvement(tt: TruthTable, initial: Circuit = None,
                          time_budget: float = 5.0) -> Circuit:
    """Try multiple synthesis strategies and keep the best."""
    import time
    from solver import (AIGBuilder, CONST1, shannon_decompose, pprm_decompose,
                        sop_synthesize, functional_decompose, _exact_single_output,
                        _reduce_to_vars, _embed_circuit)

    start = time.time()
    n = tt.n_inputs

    candidates = []

    # Strategy 1: Shannon with different variable orderings
    import itertools
    import random

    if n <= 6:
        orderings = list(itertools.permutations(range(n)))
        if len(orderings) > 200:
            orderings = random.sample(orderings, 200)
    else:
        orderings = [list(range(n))]
        for _ in range(min(100, int(time_budget * 20))):
            order = list(range(n))
            random.shuffle(order)
            orderings.append(tuple(order))

    from solver import _shannon_rec

    for order in orderings:
        if time.time() - start > time_budget:
            break
        builder = AIGBuilder(n)
        outputs = []
        cache = {}
        for j in range(tt.n_outputs):
            single_tt = TruthTable(n, 1, (tt.table[j],))
            lit = _shannon_rec(single_tt, list(order), builder, cache)
            outputs.append(lit)
        circ = builder.build(outputs)
        candidates.append(circ)

    # Strategy 2: Balanced decomposition
    if time.time() - start < time_budget:
        candidates.append(_build_balanced(tt))

    # Strategy 3: PPRM
    if time.time() - start < time_budget:
        try:
            candidates.append(pprm_decompose(tt))
        except Exception:
            pass

    # Strategy 4: SOP
    if tt.n_inputs <= 12 and time.time() - start < time_budget:
        try:
            candidates.append(sop_synthesize(tt))
        except Exception:
            pass

    # Strategy 5: Functional decomposition with exact synthesis on outputs
    if tt.n_outputs > 1 and time.time() - start < time_budget:
        try:
            candidates.append(functional_decompose(tt))
        except Exception:
            pass

    # Strategy 6: Per-output exact synthesis + shared builder
    if time.time() - start < time_budget:
        try:
            builder = AIGBuilder(n)
            outputs = []
            for j in range(tt.n_outputs):
                dep_vars = sorted(v for v in range(n) if tt.depends_on(v, j))
                if not dep_vars:
                    outputs.append(0)
                    continue
                if len(dep_vars) <= 5:
                    reduced = _reduce_to_vars(TruthTable(n, 1, (tt.table[j],)), dep_vars)
                    exact = _exact_single_output(reduced, max_gates=15, total_timeout=10)
                    if exact is not None:
                        lit = _embed_circuit(exact, dep_vars, builder)
                        outputs.append(lit)
                        continue
                # Fallback to Shannon
                single_tt = TruthTable(n, 1, (tt.table[j],))
                lit = _shannon_rec(single_tt, list(range(n)), builder, {})
                outputs.append(lit)
            circ = builder.build(outputs)
            candidates.append(circ)
        except Exception:
            pass

    if initial is not None:
        candidates.append(initial)

    # Pick best valid circuit
    best = None
    best_gates = float('inf')
    for circ in candidates:
        gc = circ.gate_count()
        if gc < best_gates and verify_equivalence(circ, tt):
            best = circ
            best_gates = gc

    return best if best is not None else candidates[0]
