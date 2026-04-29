"""Autoresearch ratchet loop for iterative solver improvement.

Runs solver against benchmarks, logs results, and only keeps changes
that improve the average reduction ratio. Inspired by Karpathy's
autoresearch pattern.

Usage:
    python3 autoresearch.py                    # Run one evaluation
    python3 autoresearch.py --watch            # Watch mode: re-run on solver.py change
    python3 autoresearch.py --compare REV      # Compare current vs git revision
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time

from benchmark import load_benchmarks, run_evaluation, TruthTable, Circuit


RESULTS_FILE = 'results.tsv'
BEST_FILE = 'best_results.json'


def get_solver_hash() -> str:
    with open('solver.py', 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def get_git_rev() -> str:
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def evaluate_solver() -> dict:
    from solver import solve
    benchmarks = load_benchmarks()

    start = time.time()
    results = run_evaluation(solve, benchmarks)
    elapsed = time.time() - start

    total_ours = sum(r['gate_count'] for r in results)
    total_baseline = sum(r['baseline_gates'] for r in results)
    all_correct = all(r['correct'] for r in results)

    tier_ratios = {}
    for r in results:
        tier = r['tier']
        if tier not in tier_ratios:
            tier_ratios[tier] = []
        tier_ratios[tier].append(r['gate_count'] / r['baseline_gates']
                                  if r['baseline_gates'] > 0 else 1.0)

    avg_ratio = sum(r['gate_count'] / r['baseline_gates']
                    for r in results if r['baseline_gates'] > 0) / len(results)

    abc_baselines = {}
    if os.path.exists('abc_baselines.json'):
        with open('abc_baselines.json') as f:
            for entry in json.load(f):
                abc_baselines[entry['name']] = entry.get('abc_gates')

    total_abc = sum(abc_baselines.get(r['name'], 0) or 0 for r in results)
    vs_abc = (total_ours / total_abc - 1.0) if total_abc > 0 else None

    return {
        'timestamp': datetime.datetime.now().isoformat(),
        'git_rev': get_git_rev(),
        'solver_hash': get_solver_hash(),
        'avg_reduction_ratio': avg_ratio,
        'total_gates': total_ours,
        'total_baseline': total_baseline,
        'vs_abc_pct': vs_abc,
        'all_correct': all_correct,
        'elapsed_s': elapsed,
        'tier_avg': {t: sum(rs)/len(rs) for t, rs in tier_ratios.items()},
        'per_benchmark': {r['name']: r['gate_count'] for r in results},
    }


def load_best() -> dict:
    if os.path.exists(BEST_FILE):
        with open(BEST_FILE) as f:
            return json.load(f)
    return {}


def save_best(result: dict):
    with open(BEST_FILE, 'w') as f:
        json.dump(result, f, indent=2)


def append_log(result: dict):
    file_exists = os.path.exists(RESULTS_FILE)
    with open(RESULTS_FILE, 'a') as f:
        writer = csv.writer(f, delimiter='\t')
        if not file_exists:
            writer.writerow([
                'timestamp', 'git_rev', 'solver_hash', 'avg_ratio',
                'total_gates', 'vs_abc_pct', 'correct', 'elapsed_s'
            ])
        writer.writerow([
            result['timestamp'],
            result['git_rev'],
            result['solver_hash'],
            f"{result['avg_reduction_ratio']:.4f}",
            result['total_gates'],
            f"{result['vs_abc_pct']:.3f}" if result['vs_abc_pct'] is not None else '-',
            result['all_correct'],
            f"{result['elapsed_s']:.1f}",
        ])


def print_summary(result: dict, best: dict):
    print()
    print("=" * 70)
    print(f"  Solver hash: {result['solver_hash']}  Git: {result['git_rev']}")
    print(f"  Avg reduction ratio: {result['avg_reduction_ratio']:.4f}")
    print(f"  Total gates: {result['total_gates']} / {result['total_baseline']} baseline")
    if result['vs_abc_pct'] is not None:
        print(f"  vs ABC: {result['vs_abc_pct']:+.1%}")
    for tier in sorted(result['tier_avg']):
        print(f"  Tier {tier}: {result['tier_avg'][tier]:.3f}")
    print(f"  All correct: {result['all_correct']}")
    print(f"  Time: {result['elapsed_s']:.1f}s")

    if best:
        prev = best.get('avg_reduction_ratio', float('inf'))
        curr = result['avg_reduction_ratio']
        if curr < prev:
            print(f"\n  IMPROVEMENT: {prev:.4f} -> {curr:.4f} ({(curr/prev-1)*100:+.1f}%)")
        elif curr > prev:
            print(f"\n  REGRESSION: {prev:.4f} -> {curr:.4f} ({(curr/prev-1)*100:+.1f}%)")
            print(f"  Best remains at {prev:.4f}")
        else:
            print(f"\n  No change from best ({prev:.4f})")
    print("=" * 70)


def run_once():
    print("Evaluating solver...")
    result = evaluate_solver()
    best = load_best()

    append_log(result)
    print_summary(result, best)

    if not result['all_correct']:
        print("\n  WARNING: Not all benchmarks correct! Not saving as best.")
        return result

    if not best or result['avg_reduction_ratio'] < best.get('avg_reduction_ratio', float('inf')):
        save_best(result)
        print(f"\n  Saved as new best result.")

    return result


def watch_mode():
    print("Watch mode: re-running on solver.py changes. Ctrl+C to stop.")
    last_hash = None
    while True:
        current_hash = get_solver_hash()
        if current_hash != last_hash:
            last_hash = current_hash
            try:
                run_once()
            except Exception as e:
                print(f"Error: {e}")
        time.sleep(2)


def compare_with_revision(rev: str):
    print(f"Comparing current solver vs {rev}...")

    current = evaluate_solver()

    stash_result = subprocess.run(
        ['git', 'stash'], capture_output=True, text=True
    )
    stashed = 'No local changes' not in stash_result.stdout

    subprocess.run(['git', 'checkout', rev, '--', 'solver.py'],
                   capture_output=True)

    import importlib
    import solver
    importlib.reload(solver)

    old = evaluate_solver()

    if stashed:
        subprocess.run(['git', 'stash', 'pop'], capture_output=True)
    else:
        subprocess.run(['git', 'checkout', 'HEAD', '--', 'solver.py'],
                       capture_output=True)

    importlib.reload(solver)

    print(f"\n{'Benchmark':<15} {'Old':>6} {'New':>6} {'Diff':>6}")
    print("-" * 40)
    for name in current['per_benchmark']:
        old_g = old['per_benchmark'].get(name, '?')
        new_g = current['per_benchmark'][name]
        if isinstance(old_g, int):
            diff = new_g - old_g
            print(f"{name:<15} {old_g:>6} {new_g:>6} {diff:>+6}")
        else:
            print(f"{name:<15} {'?':>6} {new_g:>6}")

    print(f"\n  Old avg ratio: {old['avg_reduction_ratio']:.4f}")
    print(f"  New avg ratio: {current['avg_reduction_ratio']:.4f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Autoresearch ratchet loop')
    parser.add_argument('--watch', action='store_true',
                        help='Watch mode: re-run on solver.py changes')
    parser.add_argument('--compare', type=str, default=None,
                        help='Compare current vs git revision')
    args = parser.parse_args()

    if args.watch:
        watch_mode()
    elif args.compare:
        compare_with_revision(args.compare)
    else:
        run_once()
