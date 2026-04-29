"""Theory B: Information-Theoretic Decomposition.

Uses mutual information to discover natural sub-function boundaries.
If inputs {a,b} have high MI with the output independently of {c,d},
the function likely factors as g(h(a,b), k(c,d)).
"""

from __future__ import annotations

import math
import itertools
from typing import Optional

from benchmark import TruthTable, Circuit, verify_equivalence


def mutual_information(tt: TruthTable, input_subset: list[int],
                       output_idx: int = 0) -> float:
    """Compute MI between a subset of inputs and one output bit.

    MI(X_subset; Y) = H(Y) - H(Y | X_subset)
    """
    n = tt.n_inputs
    n_patterns = 1 << n
    t = tt.table[output_idx]

    # H(Y) - entropy of output
    count1 = bin(t).count('1')
    count0 = n_patterns - count1
    h_y = _entropy_from_counts([count0, count1])

    # H(Y | X_subset)
    subset_size = len(input_subset)
    n_subset_patterns = 1 << subset_size

    conditional_counts = {}  # subset_pattern -> [count0, count1]
    for p in range(n_patterns):
        subset_val = 0
        for i, var in enumerate(input_subset):
            if (p >> var) & 1:
                subset_val |= (1 << i)
        out_val = (t >> p) & 1
        if subset_val not in conditional_counts:
            conditional_counts[subset_val] = [0, 0]
        conditional_counts[subset_val][out_val] += 1

    h_y_given_x = 0.0
    for counts in conditional_counts.values():
        total = sum(counts)
        p_x = total / n_patterns
        h = _entropy_from_counts(counts)
        h_y_given_x += p_x * h

    return h_y - h_y_given_x


def _entropy_from_counts(counts: list[int]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h


def output_entropy(tt: TruthTable, output_idx: int = 0) -> float:
    """Compute entropy of a single output bit."""
    n_patterns = 1 << tt.n_inputs
    count1 = bin(tt.table[output_idx]).count('1')
    count0 = n_patterns - count1
    return _entropy_from_counts([count0, count1])


def decomposability_score(tt: TruthTable, partition: tuple[list[int], list[int]],
                          output_idx: int = 0) -> float:
    """Score how well a partition decomposes the function.

    Returns 1.0 if MI(S1;Y) + MI(S2;Y) == H(Y), meaning
    the function perfectly decomposes as g(h(S1), k(S2)).
    """
    s1, s2 = partition
    h_y = output_entropy(tt, output_idx)
    if h_y < 1e-10:
        return 1.0
    mi1 = mutual_information(tt, s1, output_idx)
    mi2 = mutual_information(tt, s2, output_idx)
    return (mi1 + mi2) / h_y


def find_best_partition(tt: TruthTable, output_idx: int = 0,
                        max_vars: int = 12) -> Optional[tuple[list[int], list[int]]]:
    """Find the input partition with highest decomposability score."""
    n = tt.n_inputs
    if n > max_vars:
        return None

    all_vars = list(range(n))
    best_partition = None
    best_score = 0.0

    # Try all 2-way partitions (excluding trivial ones)
    for size in range(1, n):
        for s1 in itertools.combinations(all_vars, size):
            s1_list = list(s1)
            s2_list = [v for v in all_vars if v not in s1]
            if not s2_list:
                continue
            score = decomposability_score(tt, (s1_list, s2_list), output_idx)
            if score > best_score:
                best_score = score
                best_partition = (s1_list, s2_list)

    return best_partition


def sensitivity_profile(tt: TruthTable, n_samples: int = 0) -> list[list[float]]:
    """Compute sensitivity of each output to each input variable.

    Returns n_inputs x n_outputs matrix where entry [i][j] is the
    fraction of input patterns where flipping input i changes output j.
    """
    n = tt.n_inputs
    n_patterns = 1 << n

    profile = [[0.0] * tt.n_outputs for _ in range(n)]

    for var in range(n):
        for p in range(n_patterns):
            p_flipped = p ^ (1 << var)
            for j in range(tt.n_outputs):
                orig = (tt.table[j] >> p) & 1
                flipped = (tt.table[j] >> p_flipped) & 1
                if orig != flipped:
                    profile[var][j] += 1

        for j in range(tt.n_outputs):
            profile[var][j] /= n_patterns

    return profile


def mi_guided_decompose(tt: TruthTable) -> Optional[Circuit]:
    """Use MI analysis to guide decomposition and synthesis.

    For each output:
    1. Compute MI-based variable ranking
    2. Find best partition
    3. If decomposable, synthesize sub-functions separately
    4. Combine with a connector function
    """
    from solver import AIGBuilder, shannon_decompose, _shannon_rec, CONST1

    n = tt.n_inputs
    if n > 12:
        return None

    if tt.n_outputs == 1:
        return _mi_single_output(tt)

    # Multi-output: analyze which outputs share variables
    builder = AIGBuilder(n)
    outputs = []

    for j in range(tt.n_outputs):
        single_tt = TruthTable(n, 1, (tt.table[j],))
        circ = _mi_single_output(single_tt)
        if circ is None:
            lit = _shannon_rec(single_tt, list(range(n)), builder, {})
        else:
            lit = _shannon_rec(single_tt, list(range(n)), builder, {})
        outputs.append(lit)

    return builder.build(outputs)


def _mi_single_output(tt: TruthTable) -> Optional[Circuit]:
    """MI-guided synthesis for single output."""
    from solver import AIGBuilder, _shannon_rec, CONST1

    n = tt.n_inputs
    if n <= 3:
        return None  # Too small for MI to help

    # Find best variable to split on using MI
    best_var = None
    best_mi = -1.0
    for var in range(n):
        if not tt.depends_on(var):
            continue
        mi = mutual_information(tt, [var])
        if mi > best_mi:
            best_mi = mi
            best_var = var

    if best_var is None:
        return None

    # Build circuit using MI-guided variable ordering
    builder = AIGBuilder(n)
    var_order = _mi_variable_order(tt)
    lit = _shannon_rec_ordered(tt, var_order, builder, {})
    return builder.build([lit])


def _mi_variable_order(tt: TruthTable) -> list[int]:
    """Order variables by decreasing mutual information with output."""
    n = tt.n_inputs
    mi_scores = []
    for var in range(n):
        mi = mutual_information(tt, [var])
        mi_scores.append((mi, var))
    mi_scores.sort(reverse=True)
    return [var for _, var in mi_scores]


def _shannon_rec_ordered(tt: TruthTable, var_order: list[int],
                         builder, cache: dict) -> int:
    """Shannon decomposition using MI-guided variable ordering."""
    from solver import _shannon_rec, CONST1
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

    # Use first variable in MI-ordered list
    var_idx = None
    for i, v in enumerate(var_order):
        if tt.depends_on(i):
            var_idx = i
            break
    if var_idx is None:
        var_idx = 0

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
