"""Generate benchmark suite for global functional resynthesis.

Run once to produce benchmarks.json with test circuits, truth tables,
and baseline implementations.
"""

from benchmark import TruthTable, Circuit, Benchmark, save_benchmarks, verify_equivalence


# ---------------------------------------------------------------------------
# Circuit builders - structural baselines
# ---------------------------------------------------------------------------

def build_full_adder() -> tuple[TruthTable, Circuit]:
    """3 inputs (a, b, cin), 2 outputs (sum, cout)."""
    def fa(a, b, cin):
        s = a ^ b ^ cin
        cout = (a & b) | (a & cin) | (b & cin)
        return (s, cout)
    tt = TruthTable.from_multi_output_function(3, 2, fa)
    c = Circuit.new(3)
    a, b, cin = 1, 2, 3
    ab_xor = c.add_xor(a, b)
    s = c.add_xor(ab_xor, cin)
    ab_and = c.add_and(a, b)
    axor_cin = c.add_and(ab_xor, cin)
    cout = c.add_or(ab_and, axor_cin)
    c.set_outputs([s, cout])
    return tt, c


def build_half_adder() -> tuple[TruthTable, Circuit]:
    def ha(a, b):
        return (a ^ b, a & b)
    tt = TruthTable.from_multi_output_function(2, 2, ha)
    c = Circuit.new(2)
    s = c.add_xor(1, 2)
    cout = c.add_and(1, 2)
    c.set_outputs([s, cout])
    return tt, c


def build_ripple_carry_adder(n_bits: int) -> tuple[TruthTable, Circuit]:
    """n_bits-bit adder: 2*n_bits inputs, n_bits+1 outputs."""
    n_inputs = 2 * n_bits
    n_outputs = n_bits + 1

    def adder(*inputs):
        a = sum(inputs[i] << i for i in range(n_bits))
        b = sum(inputs[n_bits + i] << i for i in range(n_bits))
        result = a + b
        return tuple((result >> i) & 1 for i in range(n_outputs))

    tt = TruthTable.from_multi_output_function(n_inputs, n_outputs, adder)

    c = Circuit.new(n_inputs)
    carry = 0  # const 0
    sum_outputs = []
    for i in range(n_bits):
        a_id = i + 1
        b_id = n_bits + i + 1
        ab_xor = c.add_xor(a_id, b_id)
        if carry == 0:
            s = ab_xor
            new_carry = c.add_and(a_id, b_id)
        else:
            s = c.add_xor(ab_xor, carry)
            ab_and = c.add_and(a_id, b_id)
            xor_cin = c.add_and(ab_xor, carry)
            new_carry = c.add_or(ab_and, xor_cin)
        sum_outputs.append(s)
        carry = new_carry
    sum_outputs.append(carry)
    c.set_outputs(sum_outputs)
    return tt, c


def build_array_multiplier(n_a: int, n_b: int) -> tuple[TruthTable, Circuit]:
    """n_a x n_b multiplier."""
    n_inputs = n_a + n_b
    n_outputs = n_a + n_b

    def mul(*inputs):
        a = sum(inputs[i] << i for i in range(n_a))
        b = sum(inputs[n_a + i] << i for i in range(n_b))
        result = a * b
        return tuple((result >> i) & 1 for i in range(n_outputs))

    tt = TruthTable.from_multi_output_function(n_inputs, n_outputs, mul)

    c = Circuit.new(n_inputs)

    # Generate partial products: pp[j][i] = a[i] & b[j], represents bit position i+j
    pp = [[0] * n_a for _ in range(n_b)]
    for j in range(n_b):
        for i in range(n_a):
            pp[j][i] = c.add_and(i + 1, n_a + j + 1)

    # Accumulate row by row using ripple-carry addition
    # Start with first row of partial products
    accum = [0] * n_outputs
    for i in range(n_a):
        accum[i] = pp[0][i]

    for j in range(1, n_b):
        carry = 0
        for i in range(n_a):
            col = i + j
            a_val = accum[col]
            b_val = pp[j][i]
            if a_val == 0 and carry == 0:
                accum[col] = b_val
            elif a_val == 0:
                ab_xor = b_val
                s = c.add_xor(ab_xor, carry)
                new_carry = c.add_and(ab_xor, carry)
                accum[col] = s
                carry = new_carry
            elif carry == 0:
                s = c.add_xor(a_val, b_val)
                new_carry = c.add_and(a_val, b_val)
                accum[col] = s
                carry = new_carry
            else:
                ab_xor = c.add_xor(a_val, b_val)
                s = c.add_xor(ab_xor, carry)
                ab_and = c.add_and(a_val, b_val)
                xor_c = c.add_and(ab_xor, carry)
                new_carry = c.add_or(ab_and, xor_c)
                accum[col] = s
                carry = new_carry
        if carry != 0:
            accum[n_a + j] = carry

    c.set_outputs(accum)
    return tt, c


def build_comparator(n_bits: int) -> tuple[TruthTable, Circuit]:
    """n_bits-bit comparator: a > b. 2*n_bits inputs, 1 output."""
    n_inputs = 2 * n_bits

    def cmp(*inputs):
        a = sum(inputs[i] << i for i in range(n_bits))
        b = sum(inputs[n_bits + i] << i for i in range(n_bits))
        return (1,) if a > b else (0,)

    tt = TruthTable.from_multi_output_function(n_inputs, 1, cmp)

    c = Circuit.new(n_inputs)
    gt = 0  # const 0
    for i in range(n_bits):
        a_id = i + 1
        b_id = n_bits + i + 1
        a_gt_b_here = c.add_and(a_id, -b_id)
        a_eq_b_here = -c.add_xor(a_id, b_id)
        if gt == 0:
            gt = a_gt_b_here
        else:
            prev_and_eq = c.add_and(gt, a_eq_b_here)
            gt = c.add_or(a_gt_b_here, prev_and_eq)
    c.set_outputs([gt])
    return tt, c


def build_parity(n: int) -> tuple[TruthTable, Circuit]:
    """n-input parity (XOR of all inputs)."""
    def parity(*inputs):
        r = 0
        for x in inputs:
            r ^= x
        return (r,)
    tt = TruthTable.from_multi_output_function(n, 1, parity)
    c = Circuit.new(n)
    val = 1
    for i in range(2, n + 1):
        val = c.add_xor(val, i)
    c.set_outputs([val])
    return tt, c


def build_decoder(n: int) -> tuple[TruthTable, Circuit]:
    """n-to-2^n decoder."""
    n_outputs = 1 << n

    def decode(*inputs):
        idx = sum(inputs[i] << i for i in range(n))
        return tuple(1 if j == idx else 0 for j in range(n_outputs))

    tt = TruthTable.from_multi_output_function(n, n_outputs, decode)

    c = Circuit.new(n)
    outputs = []
    for j in range(n_outputs):
        val = 0
        for i in range(n):
            bit = (j >> i) & 1
            inp = i + 1 if bit else -(i + 1)
            if val == 0:
                val = inp
            else:
                val = c.add_and(val, inp)
        outputs.append(val)
    c.set_outputs(outputs)
    return tt, c


def build_priority_encoder(n: int) -> tuple[TruthTable, Circuit]:
    """n-bit priority encoder: outputs index of highest set bit + valid flag."""
    import math
    n_out_bits = max(1, math.ceil(math.log2(n))) if n > 1 else 1
    n_outputs = n_out_bits + 1

    def prienc(*inputs):
        highest = -1
        for i in range(n - 1, -1, -1):
            if inputs[i]:
                highest = i
                break
        if highest < 0:
            return tuple(0 for _ in range(n_outputs))
        valid = 1
        bits = tuple((highest >> j) & 1 for j in range(n_out_bits))
        return bits + (valid,)

    tt = TruthTable.from_multi_output_function(n, n_outputs, prienc)

    c = Circuit.new(n)
    any_set = 1
    for i in range(2, n + 1):
        any_set = c.add_or(any_set, i)

    idx_bits = []
    for b in range(n_out_bits):
        val = 0
        for i in range(n):
            if (i >> b) & 1:
                no_higher = i + 1
                for j in range(i + 1, n):
                    no_higher = c.add_and(no_higher, -(j + 1))
                if val == 0:
                    val = no_higher
                else:
                    val = c.add_or(val, no_higher)
        idx_bits.append(val if val != 0 else 0)

    c.set_outputs(idx_bits + [any_set])
    return tt, c


def build_mux2to1() -> tuple[TruthTable, Circuit]:
    """2:1 MUX. 3 inputs: (a, b, sel), output = sel ? b : a."""
    def mux(a, b, sel):
        return (b if sel else a,)
    tt = TruthTable.from_multi_output_function(3, 1, mux)
    c = Circuit.new(3)
    out = c.add_mux(3, 2, 1)  # sel=3, then=b=2, else=a=1
    c.set_outputs([out])
    return tt, c


def build_majority3() -> tuple[TruthTable, Circuit]:
    """3-input majority."""
    def maj(a, b, c_in):
        return ((a & b) | (a & c_in) | (b & c_in),)
    tt = TruthTable.from_multi_output_function(3, 1, maj)
    c = Circuit.new(3)
    ab = c.add_and(1, 2)
    ac = c.add_and(1, 3)
    bc = c.add_and(2, 3)
    ab_or_ac = c.add_or(ab, ac)
    result = c.add_or(ab_or_ac, bc)
    c.set_outputs([result])
    return tt, c


def build_simple_gate(n: int, func, name: str) -> tuple[TruthTable, Circuit]:
    """Build simple single-output function with naive SOP circuit."""
    tt = TruthTable.from_function(n, func)

    c = Circuit.new(n)
    minterms = []
    for pattern in range(1 << n):
        if (tt.table[0] >> pattern) & 1:
            term = 0
            for i in range(n):
                lit = (i + 1) if ((pattern >> i) & 1) else -(i + 1)
                if term == 0:
                    term = lit
                else:
                    term = c.add_and(term, lit)
            minterms.append(term)
    if not minterms:
        c.set_outputs([0])
    elif len(minterms) == 1:
        c.set_outputs([minterms[0]])
    else:
        val = minterms[0]
        for m in minterms[1:]:
            val = c.add_or(val, m)
        c.set_outputs([val])
    return tt, c


# ---------------------------------------------------------------------------
# Generate all benchmarks
# ---------------------------------------------------------------------------

def generate_benchmarks() -> list[Benchmark]:
    benchmarks = []

    # --- Tier 1: Small functions (up to 5 inputs, optimal known) ---

    # and3
    tt, c = build_simple_gate(3, lambda a, b, c: a & b & c, 'and3')
    benchmarks.append(Benchmark('and3', tt, c, optimal_gate_count=2, category='gate', tier=1))

    # or3
    tt, c = build_simple_gate(3, lambda a, b, c: a | b | c, 'or3')
    benchmarks.append(Benchmark('or3', tt, c, optimal_gate_count=2, category='gate', tier=1))

    # xor3
    tt, c = build_simple_gate(3, lambda a, b, c: a ^ b ^ c, 'xor3')
    benchmarks.append(Benchmark('xor3', tt, c, optimal_gate_count=4, category='gate', tier=1))

    # mux2to1
    tt, c = build_mux2to1()
    benchmarks.append(Benchmark('mux2', tt, c, optimal_gate_count=3, category='gate', tier=1))

    # majority3
    tt, c = build_majority3()
    benchmarks.append(Benchmark('maj3', tt, c, optimal_gate_count=4, category='gate', tier=1))

    # half adder
    tt, c = build_half_adder()
    benchmarks.append(Benchmark('half_adder', tt, c, optimal_gate_count=5, category='arithmetic', tier=1))

    # full adder
    tt, c = build_full_adder()
    benchmarks.append(Benchmark('full_adder', tt, c, optimal_gate_count=7, category='arithmetic', tier=1))

    # 2-bit adder
    tt, c = build_ripple_carry_adder(2)
    benchmarks.append(Benchmark('add2', tt, c, optimal_gate_count=None, category='arithmetic', tier=1))

    # 2-bit comparator
    tt, c = build_comparator(2)
    benchmarks.append(Benchmark('cmp2', tt, c, optimal_gate_count=None, category='comparison', tier=1))

    # 2x1 multiplier
    tt, c = build_array_multiplier(2, 1)
    benchmarks.append(Benchmark('mul2x1', tt, c, optimal_gate_count=None, category='arithmetic', tier=1))

    # --- Tier 2: Medium functions (6-10 inputs) ---

    # 4-bit adder
    tt, c = build_ripple_carry_adder(4)
    benchmarks.append(Benchmark('add4', tt, c, category='arithmetic', tier=2))

    # 2x2 multiplier
    tt, c = build_array_multiplier(2, 2)
    benchmarks.append(Benchmark('mul2x2', tt, c, category='arithmetic', tier=2))

    # 4-bit comparator
    tt, c = build_comparator(4)
    benchmarks.append(Benchmark('cmp4', tt, c, category='comparison', tier=2))

    # 8-input parity
    tt, c = build_parity(8)
    benchmarks.append(Benchmark('parity8', tt, c, optimal_gate_count=7, category='gate', tier=2))

    # 3-to-8 decoder
    tt, c = build_decoder(3)
    benchmarks.append(Benchmark('decode3to8', tt, c, category='structured', tier=2))

    # 4-bit priority encoder
    tt, c = build_priority_encoder(4)
    benchmarks.append(Benchmark('priority4', tt, c, category='structured', tier=2))

    # 3x3 multiplier
    tt, c = build_array_multiplier(3, 3)
    benchmarks.append(Benchmark('mul3x3', tt, c, category='arithmetic', tier=2))

    # --- Tier 3: Large functions (11-16 inputs) ---

    # 8-bit adder
    tt, c = build_ripple_carry_adder(8)
    benchmarks.append(Benchmark('add8', tt, c, category='arithmetic', tier=3))

    # 4x4 multiplier
    tt, c = build_array_multiplier(4, 4)
    benchmarks.append(Benchmark('mul4x4', tt, c, category='arithmetic', tier=3))

    # 8-bit comparator
    tt, c = build_comparator(8)
    benchmarks.append(Benchmark('cmp8', tt, c, category='comparison', tier=3))

    return benchmarks


if __name__ == '__main__':
    print("Generating benchmarks...")
    benchmarks = generate_benchmarks()

    print(f"\nGenerated {len(benchmarks)} benchmarks:")
    print(f"{'Name':<15} {'In':>3} {'Out':>3} {'Gates':>6} {'Depth':>6} {'Tier':>5} {'Category':<12} {'OK':>3}")
    print("-" * 65)

    all_ok = True
    for bm in benchmarks:
        ok = verify_equivalence(bm.baseline_circuit, bm.truth_table)
        all_ok = all_ok and ok
        opt_str = str(bm.optimal_gate_count) if bm.optimal_gate_count else '-'
        print(f"{bm.name:<15} {bm.truth_table.n_inputs:>3} {bm.truth_table.n_outputs:>3} "
              f"{bm.baseline_circuit.gate_count():>6} {bm.baseline_circuit.depth():>6} "
              f"{bm.tier:>5} {bm.category:<12} {'Y' if ok else 'N':>3}")

    print(f"\nAll correct: {all_ok}")

    if all_ok:
        save_benchmarks(benchmarks)
        print(f"Saved to benchmarks.json")
    else:
        print("ERROR: Some benchmarks are incorrect!")
