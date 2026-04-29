#!/usr/bin/env python3
"""CLI for optimizing circuits via Global Functional Resynthesis.

Accepts BLIF or AIGER format, outputs optimized circuit.

Usage:
    python3 optimize.py input.blif [--output output.blif] [--format blif|aiger]
    python3 optimize.py input.aig [--output output.aig]
"""

import argparse
import sys
import time

from benchmark import TruthTable, Circuit, verify_equivalence
from solver import solve, AIGBuilder, CONST1
from theories.blif_io import read_blif, blif_to_truth_table, blif_to_aig
from theories.abc_polish import abc_polish, write_aiger, read_aiger


def optimize_from_blif(filename: str, verbose: bool = True) -> tuple[Circuit, TruthTable]:
    """Read a BLIF, compute truth table, optimize, return (circuit, tt)."""
    if verbose:
        print(f"Reading {filename}...")

    tt = blif_to_truth_table(filename)
    if tt is None:
        print("ERROR: Could not parse BLIF or too many inputs (>20)", file=sys.stderr)
        sys.exit(1)

    if verbose:
        print(f"  {tt.n_inputs} inputs, {tt.n_outputs} outputs")
        print(f"  Truth table size: {1 << tt.n_inputs} rows")

    # Also read the structural circuit for comparison
    orig = blif_to_aig(filename)
    orig_gates = orig.gate_count() if orig else "?"

    if verbose:
        print(f"  Original AIG gates: {orig_gates}")
        print(f"\nOptimizing...")

    start = time.time()
    optimized = solve(tt)
    elapsed = time.time() - start

    if not verify_equivalence(optimized, tt):
        print("ERROR: Optimization produced incorrect circuit!", file=sys.stderr)
        sys.exit(1)

    opt_gates = optimized.gate_count()
    if verbose:
        print(f"  Optimized: {opt_gates} AIG gates")
        if isinstance(orig_gates, int):
            improvement = (1 - opt_gates / orig_gates) * 100
            print(f"  Improvement: {improvement:.1f}% reduction")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Verified correct: True")

    return optimized, tt


def optimize_from_aiger(filename: str, n_inputs: int, verbose: bool = True) -> tuple[Circuit, TruthTable]:
    """Read an AIGER file, simulate for truth table, optimize."""
    if verbose:
        print(f"Reading {filename}...")

    circ = read_aiger(filename, n_inputs)
    if circ is None:
        print("ERROR: Could not parse AIGER file", file=sys.stderr)
        sys.exit(1)

    # Simulate to get truth table
    if n_inputs > 20:
        print("ERROR: Too many inputs for truth table simulation (>20)", file=sys.stderr)
        sys.exit(1)

    orig_gates = circ.gate_count()
    tt = circ.to_truth_table()

    if verbose:
        print(f"  {tt.n_inputs} inputs, {tt.n_outputs} outputs")
        print(f"  Original: {orig_gates} AIG gates")
        print(f"\nOptimizing...")

    start = time.time()
    optimized = solve(tt)
    elapsed = time.time() - start

    if not verify_equivalence(optimized, tt):
        print("ERROR: Optimization produced incorrect circuit!", file=sys.stderr)
        sys.exit(1)

    opt_gates = optimized.gate_count()
    if verbose:
        improvement = (1 - opt_gates / orig_gates) * 100 if orig_gates > 0 else 0
        print(f"  Optimized: {opt_gates} AIG gates")
        print(f"  Improvement: {improvement:.1f}% reduction")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Verified correct: True")

    return optimized, tt


def write_blif(circuit: Circuit, tt: TruthTable, filename: str,
               input_names: list[str] = None, output_names: list[str] = None):
    """Write circuit as BLIF file."""
    n_inputs = len(circuit.inputs)
    n_outputs = len(circuit.outputs)

    if input_names is None:
        input_names = [f"i{i}" for i in range(n_inputs)]
    if output_names is None:
        output_names = [f"o{j}" for j in range(n_outputs)]

    with open(filename, 'w') as f:
        f.write(f".model optimized\n")
        f.write(f".inputs {' '.join(input_names)}\n")
        f.write(f".outputs {' '.join(output_names)}\n")
        f.write("\n")

        # Map node IDs to signal names
        sig_names = {0: "const0"}
        for i, inp_id in enumerate(circuit.inputs):
            sig_names[inp_id] = input_names[i]

        and_nodes = sorted([n for n in circuit.nodes.values() if n.type == 'AND'],
                          key=lambda n: n.id)

        for node in and_nodes:
            sig_names[node.id] = f"n{node.id}"

        def lit_name(lit):
            if lit == 0:
                return "const0", False
            nid = abs(lit)
            inv = lit < 0
            name = sig_names.get(nid, f"n{nid}")
            return name, inv

        # Write AND gates (in AIG, everything is AND + inversions)
        for node in and_nodes:
            out_name = sig_names[node.id]
            a_name, a_inv = lit_name(node.fanin0)
            b_name, b_inv = lit_name(node.fanin1)

            # Express as .names (SOP form)
            # AND gate with optional inversions on inputs
            a_pattern = '0' if a_inv else '1'
            b_pattern = '0' if b_inv else '1'

            f.write(f".names {a_name} {b_name} {out_name}\n")
            f.write(f"{a_pattern}{b_pattern} 1\n")

        # Write outputs (possibly inverted)
        for j, out_lit in enumerate(circuit.outputs):
            out_name = output_names[j]
            if out_lit == 0:
                f.write(f".names {out_name}\n")
                # No rows = always 0
            else:
                src_name, inv = lit_name(out_lit)
                if not inv:
                    if src_name != out_name:
                        f.write(f".names {src_name} {out_name}\n")
                        f.write("1 1\n")
                else:
                    f.write(f".names {src_name} {out_name}\n")
                    f.write("0 1\n")

        f.write(".end\n")


def main():
    parser = argparse.ArgumentParser(
        description="Optimize a circuit using Global Functional Resynthesis")
    parser.add_argument("input", help="Input file (BLIF or AIGER format)")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--format", "-f", choices=["blif", "aiger"],
                       help="Output format (default: same as input)")
    parser.add_argument("--inputs", type=int,
                       help="Number of inputs (required for AIGER without header)")
    parser.add_argument("--quiet", "-q", action="store_true",
                       help="Suppress verbose output")
    args = parser.parse_args()

    verbose = not args.quiet

    if args.input.endswith('.blif'):
        circuit, tt = optimize_from_blif(args.input, verbose)
        # Read original for names
        result = read_blif(args.input)
        input_names = result[0] if result else None
        output_names = result[1] if result else None
    elif args.input.endswith('.aig') or args.input.endswith('.aag'):
        n_inputs = args.inputs
        if n_inputs is None:
            # Try to read from AIGER header
            with open(args.input, 'rb') as f:
                header = f.readline().decode().split()
                if len(header) >= 3:
                    n_inputs = int(header[2])
        if n_inputs is None:
            print("ERROR: Cannot determine number of inputs. Use --inputs N", file=sys.stderr)
            sys.exit(1)
        circuit, tt = optimize_from_aiger(args.input, n_inputs, verbose)
        input_names = None
        output_names = None
    else:
        print(f"ERROR: Unknown file format: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Write output
    if args.output:
        fmt = args.format or ('aiger' if args.output.endswith('.aig') else 'blif')
        if fmt == 'aiger':
            write_aiger(circuit, args.output)
            if verbose:
                print(f"\nWritten: {args.output} (AIGER)")
        else:
            write_blif(circuit, tt, args.output, input_names, output_names)
            if verbose:
                print(f"\nWritten: {args.output} (BLIF)")


if __name__ == '__main__':
    main()
