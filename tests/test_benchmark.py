"""Tests for core data structures in benchmark.py."""

import sys
sys.path.insert(0, '.')

from benchmark import TruthTable, Circuit, AIGNode, verify_equivalence


def test_truth_table_and2():
    tt = TruthTable.from_function(2, lambda a, b: a & b)
    assert tt.n_inputs == 2
    assert tt.n_outputs == 1
    assert tt.evaluate(0b00) == 0  # 0&0=0
    assert tt.evaluate(0b01) == 0  # 1&0=0
    assert tt.evaluate(0b10) == 0  # 0&1=0
    assert tt.evaluate(0b11) == 1  # 1&1=1
    assert tt.table == (0b1000,)


def test_truth_table_xor2():
    tt = TruthTable.from_function(2, lambda a, b: a ^ b)
    assert tt.evaluate(0b00) == 0
    assert tt.evaluate(0b01) == 1
    assert tt.evaluate(0b10) == 1
    assert tt.evaluate(0b11) == 0
    assert tt.table == (0b0110,)


def test_truth_table_or3():
    tt = TruthTable.from_function(3, lambda a, b, c: a | b | c)
    assert tt.evaluate(0b000) == 0
    assert tt.evaluate(0b001) == 1
    assert tt.evaluate(0b111) == 1


def test_truth_table_cofactor():
    tt = TruthTable.from_function(3, lambda a, b, c: (a & b) | c)
    cof0 = tt.negative_cofactor(2)  # c=0 -> a&b
    cof1 = tt.positive_cofactor(2)  # c=1 -> 1
    assert cof0.n_inputs == 2
    and2 = TruthTable.from_function(2, lambda a, b: a & b)
    assert cof0 == and2
    assert cof1.is_constant() == 1


def test_truth_table_depends_on():
    tt = TruthTable.from_function(3, lambda a, b, c: a & b)
    assert tt.depends_on(0) == True
    assert tt.depends_on(1) == True
    assert tt.depends_on(2) == False


def test_circuit_and2():
    c = Circuit.new(2)
    and_node = c.add_and(1, 2)  # inputs are 1, 2
    c.set_outputs([and_node])
    assert c.gate_count() == 1
    assert c.simulate(0b00) == 0
    assert c.simulate(0b01) == 0
    assert c.simulate(0b10) == 0
    assert c.simulate(0b11) == 1


def test_circuit_or2():
    c = Circuit.new(2)
    or_node = c.add_or(1, 2)
    c.set_outputs([or_node])
    assert c.simulate(0b00) == 0
    assert c.simulate(0b01) == 1
    assert c.simulate(0b10) == 1
    assert c.simulate(0b11) == 1


def test_circuit_xor2():
    c = Circuit.new(2)
    xor_node = c.add_xor(1, 2)
    c.set_outputs([xor_node])
    assert c.simulate(0b00) == 0
    assert c.simulate(0b01) == 1
    assert c.simulate(0b10) == 1
    assert c.simulate(0b11) == 0


def test_circuit_mux():
    c = Circuit.new(3)  # sel=1, a=2, b=3
    mux_node = c.add_mux(1, 2, 3)  # sel ? a : b
    c.set_outputs([mux_node])
    # sel=0 -> output=b, sel=1 -> output=a
    assert c.simulate(0b000) == 0  # sel=0, a=0, b=0 -> 0
    assert c.simulate(0b100) == 1  # sel=0, a=0, b=1 -> 1 (b)
    assert c.simulate(0b001) == 0  # sel=1, a=0, b=0 -> 0 (a)
    assert c.simulate(0b011) == 1  # sel=1, a=1, b=0 -> 1 (a)


def test_circuit_depth():
    c = Circuit.new(3)
    n1 = c.add_and(1, 2)
    n2 = c.add_and(n1, 3)
    c.set_outputs([n2])
    assert c.depth() == 2
    assert c.gate_count() == 2


def test_circuit_inverted_output():
    c = Circuit.new(2)
    and_node = c.add_and(1, 2)
    c.set_outputs([-and_node])  # NAND
    assert c.simulate(0b00) == 1
    assert c.simulate(0b01) == 1
    assert c.simulate(0b10) == 1
    assert c.simulate(0b11) == 0


def test_circuit_to_truth_table():
    c = Circuit.new(2)
    xor_node = c.add_xor(1, 2)
    c.set_outputs([xor_node])
    tt = c.to_truth_table()
    expected = TruthTable.from_function(2, lambda a, b: a ^ b)
    assert tt == expected


def test_verify_equivalence():
    tt = TruthTable.from_function(3, lambda a, b, c: (a & b) | c)
    c = Circuit.new(3)
    ab = c.add_and(1, 2)
    result = c.add_or(ab, 3)
    c.set_outputs([result])
    assert verify_equivalence(c, tt)


def test_verify_equivalence_wrong():
    tt = TruthTable.from_function(2, lambda a, b: a & b)
    c = Circuit.new(2)
    or_node = c.add_or(1, 2)
    c.set_outputs([or_node])
    assert not verify_equivalence(c, tt)


def test_multi_output():
    # Half adder: sum = a^b, carry = a&b
    tt = TruthTable.from_multi_output_function(
        2, 2, lambda a, b: (a ^ b, a & b)
    )
    assert tt.evaluate(0b00) == 0b00  # sum=0, carry=0
    assert tt.evaluate(0b01) == 0b01  # sum=1, carry=0
    assert tt.evaluate(0b10) == 0b01  # sum=1, carry=0
    assert tt.evaluate(0b11) == 0b10  # sum=0, carry=1

    c = Circuit.new(2)
    xor_node = c.add_xor(1, 2)
    and_node = c.add_and(1, 2)
    c.set_outputs([xor_node, and_node])
    assert verify_equivalence(c, tt)


def test_numpy_simulation():
    c = Circuit.new(3)
    ab = c.add_and(1, 2)
    result = c.add_or(ab, 3)
    c.set_outputs([result])
    sim = c.simulate_all_numpy()
    assert sim.shape == (1, 8)
    for p in range(8):
        assert sim[0, p] == c.simulate(p)


def test_serialization():
    c = Circuit.new(2)
    n = c.add_and(1, 2)
    c.set_outputs([n])
    d = c.to_dict()
    c2 = Circuit.from_dict(d)
    assert c2.gate_count() == 1
    for p in range(4):
        assert c.simulate(p) == c2.simulate(p)


if __name__ == '__main__':
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed")
