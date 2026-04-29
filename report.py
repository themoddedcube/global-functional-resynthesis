"""Benchmark comparison dashboard.

Generates formatted comparison tables showing our solver vs ABC resyn2
and structural baselines.
"""

import json
import os
from benchmark import load_benchmarks, run_evaluation


def load_abc_baselines(path: str = 'abc_baselines.json') -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {r['name']: r for r in data}


def generate_report(solver_func=None, abc_path: str = 'abc_baselines.json'):
    benchmarks = load_benchmarks()
    abc_baselines = load_abc_baselines(abc_path)

    if solver_func is None:
        from solver import solve
        solver_func = solve

    results = run_evaluation(solver_func, benchmarks)

    # Header
    print()
    print("=" * 100)
    print("GLOBAL FUNCTIONAL RESYNTHESIS — BENCHMARK COMPARISON")
    print("=" * 100)
    print()

    # Table header
    print(f"{'Benchmark':<15} {'In':>3} {'Out':>3} {'Tier':>4} │ "
          f"{'Naive':>5} {'ABC':>5} {'Ours':>5} │ "
          f"{'vs Naive':>8} {'vs ABC':>7} │ "
          f"{'OK':>2} {'Time':>5}")
    print("─" * 15 + "─" * 12 + "─┼─" + "─" * 17 + "─┼─" + "─" * 16 + "─┼─" + "─" * 8)

    tier_stats = {}
    total_abc_gates = 0
    total_our_gates = 0
    total_naive_gates = 0
    wins = 0
    losses = 0
    ties = 0

    for r in results:
        name = r['name']
        abc_data = abc_baselines.get(name, {})
        abc_gates = abc_data.get('abc_gates')

        naive_gates = r['baseline_gates']
        our_gates = r['gate_count']
        tier = r['tier']

        # Compute comparisons
        vs_naive = f"{(our_gates / naive_gates - 1) * 100:+.0f}%" if naive_gates > 0 else "-"

        if abc_gates is not None and abc_gates > 0:
            vs_abc = f"{(our_gates / abc_gates - 1) * 100:+.0f}%"
            total_abc_gates += abc_gates
            if our_gates < abc_gates:
                wins += 1
                vs_abc = f"\033[32m{vs_abc}\033[0m"  # green
            elif our_gates > abc_gates:
                losses += 1
                vs_abc = f"\033[31m{vs_abc}\033[0m"  # red
            else:
                ties += 1
        else:
            vs_abc = "-"

        total_our_gates += our_gates
        total_naive_gates += naive_gates

        ok = "Y" if r['correct'] else "N"
        abc_str = str(abc_gates) if abc_gates is not None else "-"

        print(f"{name:<15} {r['n_inputs']:>3} {r['n_outputs']:>3} {tier:>4} │ "
              f"{naive_gates:>5} {abc_str:>5} {our_gates:>5} │ "
              f"{vs_naive:>8} {vs_abc:>7} │ "
              f"{ok:>2} {r['time_s']:>4.1f}s")

        if tier not in tier_stats:
            tier_stats[tier] = {'naive': 0, 'abc': 0, 'ours': 0, 'count': 0}
        tier_stats[tier]['naive'] += naive_gates
        tier_stats[tier]['ours'] += our_gates
        if abc_gates:
            tier_stats[tier]['abc'] += abc_gates
        tier_stats[tier]['count'] += 1

    # Summary
    print("─" * 100)
    print()
    print("SUMMARY")
    print("─" * 40)
    for tier in sorted(tier_stats):
        s = tier_stats[tier]
        print(f"  Tier {tier}: {s['ours']} gates total "
              f"(naive: {s['naive']}, ABC: {s['abc'] or 'N/A'})")
    print()
    print(f"  Total our gates:   {total_our_gates}")
    print(f"  Total naive gates: {total_naive_gates}")
    if total_abc_gates:
        print(f"  Total ABC gates:   {total_abc_gates}")
        pct = (total_our_gates / total_abc_gates - 1) * 100
        print(f"  vs ABC overall:    {pct:+.1f}%")
    print()
    print(f"  vs ABC: {wins} wins, {ties} ties, {losses} losses")
    all_correct = all(r['correct'] for r in results)
    print(f"  All correct: {all_correct}")

    # ASCII bar chart
    print()
    print("GATE COUNT COMPARISON (per benchmark)")
    print("─" * 60)
    max_gates = max(r['gate_count'] for r in results if r['gate_count'] < 500)

    for r in results:
        name = r['name']
        our = r['gate_count']
        abc_data = abc_baselines.get(name, {})
        abc_g = abc_data.get('abc_gates', 0) or 0
        naive = r['baseline_gates']

        if our > 500:
            print(f"  {name:<12} [our={our}, abc={abc_g}, naive={naive}] (truncated)")
            continue

        scale = 50 / max(max_gates, 1)
        our_bar = int(our * scale)
        abc_bar = int(abc_g * scale)
        naive_bar = int(naive * scale)

        print(f"  {name:<12} Ours  {'█' * our_bar} {our}")
        if abc_g:
            print(f"  {'':12} ABC   {'░' * abc_bar} {abc_g}")
        print(f"  {'':12} Naive {'▒' * naive_bar} {naive}")
        print()


if __name__ == '__main__':
    generate_report()
