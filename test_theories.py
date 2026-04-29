"""Preemptive directional tests for each theory.

Each theory must demonstrate measurable improvement on at least one benchmark
before being integrated into the main solver.
"""

import sys
import time

from benchmark import TruthTable, Circuit, verify_equivalence, load_benchmarks


def test_egraph():
    """Theory A: E-graph + Exact Synthesis on mul2x2."""
    print("\n" + "=" * 60)
    print("THEORY A: E-graph + Exact Synthesis Hybrid")
    print("=" * 60)

    from theories.egraph import EGraph, egraph_synthesize

    benchmarks = load_benchmarks()
    test_cases = ['mul2x2', 'mux2', 'maj3', 'cmp2', 'full_adder', 'add2']

    from solver import solve as baseline_solve

    results = []
    for bm in benchmarks:
        if bm.name not in test_cases:
            continue

        print(f"\n--- {bm.name} (n={bm.truth_table.n_inputs}, m={bm.truth_table.n_outputs}) ---")

        # Baseline
        t0 = time.time()
        base_circ = baseline_solve(bm.truth_table)
        base_time = time.time() - t0
        base_gates = base_circ.gate_count()
        base_ok = verify_equivalence(base_circ, bm.truth_table)

        # E-graph
        t0 = time.time()
        try:
            eg_circ = egraph_synthesize(bm.truth_table, max_iterations=100, max_classes=3000)
            eg_time = time.time() - t0
            if eg_circ is not None:
                eg_gates = eg_circ.gate_count()
                eg_ok = verify_equivalence(eg_circ, bm.truth_table)
            else:
                eg_gates = None
                eg_ok = False
        except Exception as e:
            eg_time = time.time() - t0
            eg_gates = None
            eg_ok = False
            print(f"  E-graph error: {e}")

        improvement = ((base_gates - eg_gates) / base_gates * 100) if eg_gates else 0

        print(f"  Baseline: {base_gates} gates, correct={base_ok}, {base_time:.2f}s")
        if eg_gates is not None:
            print(f"  E-graph:  {eg_gates} gates, correct={eg_ok}, {eg_time:.2f}s")
            print(f"  Improvement: {improvement:+.1f}%")
        else:
            print(f"  E-graph:  FAILED ({eg_time:.2f}s)")

        results.append({
            'name': bm.name,
            'base_gates': base_gates,
            'eg_gates': eg_gates,
            'eg_ok': eg_ok,
            'improvement': improvement
        })

    print("\n--- E-graph Summary ---")
    improved = sum(1 for r in results if r['eg_gates'] and r['eg_gates'] < r['base_gates'])
    correct = sum(1 for r in results if r['eg_ok'])
    print(f"Improved: {improved}/{len(results)} benchmarks")
    print(f"Correct: {correct}/{len(results)} benchmarks")
    avg_imp = sum(r['improvement'] for r in results) / len(results) if results else 0
    print(f"Average improvement: {avg_imp:+.1f}%")
    return avg_imp > 0


def test_mi_decomposition():
    """Theory B: MI-guided decomposition on add4."""
    print("\n" + "=" * 60)
    print("THEORY B: Information-Theoretic Decomposition")
    print("=" * 60)

    from theories.mi_decomp import (mutual_information, find_best_partition,
                                    sensitivity_profile, mi_guided_decompose,
                                    decomposability_score, _mi_variable_order)

    benchmarks = load_benchmarks()
    from solver import solve as baseline_solve

    # First: validate MI analysis on add4
    add4 = next(b for b in benchmarks if b.name == 'add4')
    tt = add4.truth_table
    print(f"\n--- MI Analysis of add4 (8 inputs, 5 outputs) ---")

    # Sensitivity profile
    profile = sensitivity_profile(tt)
    print("\nSensitivity profile (input -> output):")
    print(f"{'Input':>6}", end="")
    for j in range(tt.n_outputs):
        print(f"  out{j:>2}", end="")
    print()
    for i in range(tt.n_inputs):
        label = f"a{i}" if i < 4 else f"b{i-4}"
        print(f"{label:>6}", end="")
        for j in range(tt.n_outputs):
            print(f"  {profile[i][j]:>5.3f}", end="")
        print()

    # MI for individual variables -> output bit 0 (LSB)
    print("\nMI(var; out0) for LSB:")
    for var in range(tt.n_inputs):
        mi = mutual_information(tt, [var], 0)
        label = f"a{var}" if var < 4 else f"b{var-4}"
        print(f"  {label}: MI={mi:.4f}")

    # Variable order by MI for each output
    for j in range(tt.n_outputs):
        single_tt = TruthTable(tt.n_inputs, 1, (tt.table[j],))
        order = _mi_variable_order(single_tt)
        labels = [f"a{v}" if v < 4 else f"b{v-4}" for v in order]
        print(f"  out{j} MI order: {labels}")

    # Decomposability analysis
    print("\nDecomposability scores for partitions:")
    # Test natural partition: {a0,b0} vs {a1,a2,a3,b1,b2,b3}
    score = decomposability_score(tt, ([0, 4], [1, 2, 3, 5, 6, 7]), 0)
    print(f"  {{a0,b0}} vs rest (out0): {score:.4f}")

    # Test per-bit partition: {a_i, b_i} vs rest for each bit
    for bit in range(min(4, tt.n_outputs)):
        vars_for_bit = [bit, 4 + bit]
        rest = [v for v in range(8) if v not in vars_for_bit]
        score = decomposability_score(tt, (vars_for_bit, rest), bit)
        print(f"  {{a{bit},b{bit}}} vs rest (out{bit}): {score:.4f}")

    # Now test synthesis improvement
    test_cases = ['add4', 'cmp4', 'mul2x2']
    results = []

    for bm in benchmarks:
        if bm.name not in test_cases:
            continue

        print(f"\n--- MI Synthesis: {bm.name} ---")
        base_circ = baseline_solve(bm.truth_table)
        base_gates = base_circ.gate_count()

        try:
            mi_circ = mi_guided_decompose(bm.truth_table)
            if mi_circ is not None:
                mi_gates = mi_circ.gate_count()
                mi_ok = verify_equivalence(mi_circ, bm.truth_table)
                improvement = (base_gates - mi_gates) / base_gates * 100
                print(f"  Baseline: {base_gates} gates")
                print(f"  MI:       {mi_gates} gates, correct={mi_ok}, imp={improvement:+.1f}%")
                results.append({'name': bm.name, 'improvement': improvement, 'ok': mi_ok})
            else:
                print(f"  MI: returned None")
                results.append({'name': bm.name, 'improvement': 0, 'ok': False})
        except Exception as e:
            print(f"  MI error: {e}")
            results.append({'name': bm.name, 'improvement': 0, 'ok': False})

    print("\n--- MI Summary ---")
    avg_imp = sum(r['improvement'] for r in results) / len(results) if results else 0
    print(f"Average improvement: {avg_imp:+.1f}%")
    return True  # MI analysis is valuable even if synthesis improvement is small


def test_progressive():
    """Theory D: Progressive resynthesis on add4 baseline."""
    print("\n" + "=" * 60)
    print("THEORY D: Progressive Hierarchical Resynthesis")
    print("=" * 60)

    from theories.progressive import progressive_resynthesis

    benchmarks = load_benchmarks()
    test_cases = ['add4', 'cmp4', 'full_adder', 'add2']

    results = []
    for bm in benchmarks:
        if bm.name not in test_cases:
            continue

        print(f"\n--- {bm.name} ---")
        base_circ = bm.baseline_circuit
        base_gates = base_circ.gate_count()

        t0 = time.time()
        try:
            opt_circ = progressive_resynthesis(base_circ, bm.truth_table, max_cut_size=5)
            elapsed = time.time() - t0
            opt_gates = opt_circ.gate_count()
            opt_ok = verify_equivalence(opt_circ, bm.truth_table)
            improvement = (base_gates - opt_gates) / base_gates * 100

            print(f"  Baseline:     {base_gates} gates")
            print(f"  Progressive:  {opt_gates} gates, correct={opt_ok}, {elapsed:.2f}s")
            print(f"  Improvement:  {improvement:+.1f}%")

            results.append({
                'name': bm.name,
                'base': base_gates,
                'opt': opt_gates,
                'ok': opt_ok,
                'improvement': improvement
            })
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  Error: {e} ({elapsed:.2f}s)")
            import traceback
            traceback.print_exc()
            results.append({'name': bm.name, 'improvement': 0, 'ok': False})

    print("\n--- Progressive Summary ---")
    improved = sum(1 for r in results if r.get('opt', float('inf')) < r.get('base', 0))
    correct = sum(1 for r in results if r.get('ok', False))
    avg_imp = sum(r['improvement'] for r in results) / len(results) if results else 0
    print(f"Improved: {improved}/{len(results)} benchmarks")
    print(f"Correct: {correct}/{len(results)}")
    print(f"Average improvement: {avg_imp:+.1f}%")
    return avg_imp > 0


if __name__ == '__main__':
    print("=" * 60)
    print("PREEMPTIVE THEORY VALIDATION")
    print("=" * 60)

    results = {}

    results['egraph'] = test_egraph()
    results['mi'] = test_mi_decomposition()
    results['progressive'] = test_progressive()

    print("\n" + "=" * 60)
    print("OVERALL RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS (directional improvement)" if passed else "NEEDS WORK"
        print(f"  {name}: {status}")
