"""Generate ABC baseline results for comparison.

Exports each benchmark as truth table, runs ABC's resyn2 script,
and collects the resulting gate counts.
"""

import subprocess
import tempfile
import os
import json
import sys

from benchmark import TruthTable, Circuit, load_benchmarks

ABC_PATH = '/tmp/abc/abc'


def truth_table_to_hex(bits: int, n_inputs: int) -> str:
    """Convert truth table to hex string for ABC's read_truth."""
    n_chars = max(1, (1 << n_inputs) // 4)
    return format(bits, f'0{n_chars}x')


def run_abc_on_single_output(tt_bits: int, n_inputs: int, script: str = 'resyn2') -> dict:
    """Run ABC on a single-output truth table."""
    hex_str = truth_table_to_hex(tt_bits, n_inputs)

    abc_commands = f"read_truth {hex_str}; strash; {get_script(script)}; print_stats"

    try:
        result = subprocess.run(
            [ABC_PATH, '-c', abc_commands],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout + result.stderr

        # Parse gate count from ABC output
        # After strash, ABC prints: "xxx : i/o = X/Y lat = Z and = N lev = L"
        for line in output.split('\n'):
            if 'and =' in line:
                parts = line.split('and =')
                if len(parts) >= 2:
                    nd_str = parts[1].strip().split()[0]
                    try:
                        return {'gates': int(nd_str), 'output': output}
                    except ValueError:
                        pass
            elif 'nd =' in line:
                parts = line.split('nd =')
                if len(parts) >= 2:
                    nd_str = parts[1].strip().split()[0]
                    try:
                        return {'gates': int(nd_str), 'output': output}
                    except ValueError:
                        pass

        return {'gates': None, 'output': output, 'error': 'Could not parse gate count'}

    except subprocess.TimeoutExpired:
        return {'gates': None, 'error': 'timeout'}
    except Exception as e:
        return {'gates': None, 'error': str(e)}


def get_script(name: str) -> str:
    scripts = {
        'resyn': 'b; rw; rwz; b; rwz; b',
        'resyn2': 'b; rw; rf; b; rw; rwz; b; rfz; rwz; b',
        'resyn2a': 'b; rw; b; rw; rwz; b; rwz; b',
        'resyn3': 'b; rs; rw; rs -K 6; b; rsz; rwz; rsz -K 6; b',
        'resyn2rs': 'b; rs -K 6; rw; rs -K 6 -N 2; rf; rs -K 8; b; rs -K 8 -N 2; rw; rs -K 10; rwz; rs -K 10 -N 2; b; rs -K 12; rfz; rs -K 12 -N 2; rwz; b',
        'compress': 'b -l; rw -l; rwz -l; b -l; rwz -l; b -l',
        'compress2': 'b -l; rw -l; rf -l; b -l; rw -l; rwz -l; b -l; rfz -l; rwz -l; b -l',
    }
    return scripts.get(name, scripts['resyn2'])


def run_abc_benchmark(benchmark, scripts: list[str] = None) -> dict:
    """Run ABC on a benchmark, trying multiple scripts, return best."""
    if scripts is None:
        scripts = ['resyn2', 'resyn2rs', 'compress2']

    tt = benchmark.truth_table
    results_per_script = {}

    for script in scripts:
        total_gates = 0
        all_ok = True

        for j in range(tt.n_outputs):
            r = run_abc_on_single_output(tt.table[j], tt.n_inputs, script)
            if r['gates'] is not None:
                total_gates += r['gates']
            else:
                all_ok = False
                break

        if all_ok:
            results_per_script[script] = total_gates

    if results_per_script:
        best_script = min(results_per_script, key=results_per_script.get)
        return {
            'name': benchmark.name,
            'abc_gates': results_per_script[best_script],
            'best_script': best_script,
            'all_scripts': results_per_script,
        }
    else:
        return {
            'name': benchmark.name,
            'abc_gates': None,
            'error': 'All scripts failed',
        }


def generate_abc_baselines():
    """Generate ABC baseline results for all benchmarks."""
    if not os.path.exists(ABC_PATH):
        print(f"ABC not found at {ABC_PATH}. Please compile it first.")
        return None

    benchmarks = load_benchmarks()
    results = []

    print(f"{'Benchmark':<15} {'In':>3} {'Out':>3} {'Struct':>6} {'ABC':>6} {'Script':<12}")
    print("-" * 55)

    for bm in benchmarks:
        r = run_abc_benchmark(bm)
        r['baseline_gates'] = bm.baseline_circuit.gate_count()
        r['n_inputs'] = bm.truth_table.n_inputs
        r['n_outputs'] = bm.truth_table.n_outputs
        r['tier'] = bm.tier
        results.append(r)

        abc_str = str(r['abc_gates']) if r['abc_gates'] is not None else 'FAIL'
        script_str = r.get('best_script', '-')
        print(f"{bm.name:<15} {bm.truth_table.n_inputs:>3} {bm.truth_table.n_outputs:>3} "
              f"{bm.baseline_circuit.gate_count():>6} {abc_str:>6} {script_str:<12}")

    # Save results
    with open('abc_baselines.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to abc_baselines.json")
    return results


if __name__ == '__main__':
    generate_abc_baselines()
