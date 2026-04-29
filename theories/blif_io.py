"""BLIF file I/O for circuit import/export.

Reads gate-level BLIF (AND2, OR2, XOR2, NOT1) and converts to truth table
or AIG circuit for optimization.
"""

from __future__ import annotations

from typing import Optional
from benchmark import TruthTable, Circuit


def read_blif(filename: str) -> Optional[tuple[list[str], list[str], dict]]:
    """Parse a gate-level BLIF file.

    Returns (inputs, outputs, gates) where gates is a dict mapping
    signal_name -> (gate_type, input_signals).
    Gate types: 'AND', 'OR', 'XOR', 'NOT', 'BUF', 'NAND', 'NOR', 'XNOR'
    """
    inputs = []
    outputs = []
    gates = {}  # signal -> (type, [inputs])

    with open(filename, 'r') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith('#'):
            i += 1
            continue

        # Handle line continuations
        while line.endswith('\\'):
            i += 1
            line = line[:-1] + ' ' + lines[i].strip()

        if line.startswith('.model'):
            pass
        elif line.startswith('.inputs'):
            parts = line.split()[1:]
            inputs.extend(parts)
        elif line.startswith('.outputs'):
            parts = line.split()[1:]
            outputs.extend(parts)
        elif line.startswith('.gate') or line.startswith('.subckt'):
            parts = line.split()
            gate_type = parts[1].upper()
            connections = {}
            for part in parts[2:]:
                if '=' in part:
                    pin, sig = part.split('=', 1)
                    connections[pin] = sig

            # Determine output and inputs based on gate type
            out_sig = connections.get('O', connections.get('Y', connections.get('Z', '')))
            if gate_type in ('AND2', 'AND'):
                in_sigs = [connections.get('A', ''), connections.get('B', '')]
                gates[out_sig] = ('AND', in_sigs)
            elif gate_type in ('OR2', 'OR'):
                in_sigs = [connections.get('A', ''), connections.get('B', '')]
                gates[out_sig] = ('OR', in_sigs)
            elif gate_type in ('XOR2', 'XOR'):
                in_sigs = [connections.get('A', ''), connections.get('B', '')]
                gates[out_sig] = ('XOR', in_sigs)
            elif gate_type in ('NOT1', 'NOT', 'INV'):
                in_sigs = [connections.get('A', '')]
                gates[out_sig] = ('NOT', in_sigs)
            elif gate_type in ('BUF', 'BUF1'):
                in_sigs = [connections.get('A', '')]
                gates[out_sig] = ('BUF', in_sigs)
            elif gate_type in ('NAND2', 'NAND'):
                in_sigs = [connections.get('A', ''), connections.get('B', '')]
                gates[out_sig] = ('NAND', in_sigs)
            elif gate_type in ('NOR2', 'NOR'):
                in_sigs = [connections.get('A', ''), connections.get('B', '')]
                gates[out_sig] = ('NOR', in_sigs)
            elif gate_type in ('XNOR2', 'XNOR'):
                in_sigs = [connections.get('A', ''), connections.get('B', '')]
                gates[out_sig] = ('XNOR', in_sigs)
            else:
                # Try generic parsing
                in_sigs = [v for k, v in connections.items() if k != 'O' and k != 'Y' and k != 'Z']
                out_sig = connections.get('O', connections.get('Y', connections.get('Z', '')))
                if out_sig:
                    gates[out_sig] = (gate_type, in_sigs)

        elif line.startswith('.names'):
            # SOP-style gate definition
            parts = line.split()[1:]
            if len(parts) >= 2:
                gate_inputs = parts[:-1]
                gate_output = parts[-1]
                # Read the truth table rows
                rows = []
                i += 1
                while i < len(lines):
                    row = lines[i].strip()
                    if not row or row.startswith('.'):
                        break
                    rows.append(row)
                    i += 1
                gates[gate_output] = _parse_sop_gate(gate_inputs, rows)
                continue

        elif line.startswith('.end'):
            break

        i += 1

    return inputs, outputs, gates


def _parse_sop_gate(inputs: list[str], rows: list[str]) -> tuple[str, list[str]]:
    """Parse a .names SOP definition into a gate type."""
    if len(inputs) == 0:
        # Constant
        if rows and rows[0].strip() == '1':
            return ('CONST1', [])
        return ('CONST0', [])

    if len(inputs) == 1:
        if len(rows) == 1:
            row = rows[0].split()
            if len(row) == 2:
                pattern, output = row[0], row[1]
                if pattern == '1' and output == '1':
                    return ('BUF', inputs)
                elif pattern == '0' and output == '1':
                    return ('NOT', inputs)
        return ('BUF', inputs)

    if len(inputs) == 2:
        # Determine gate type from truth table
        on_set = set()
        for row in rows:
            parts = row.split()
            if len(parts) == 2:
                pattern, output = parts
            else:
                pattern = row[:-1].strip()
                output = row[-1]
            if output == '1':
                on_set.add(pattern)

        if on_set == {'11'}:
            return ('AND', inputs)
        elif on_set == {'1-', '-1'} or on_set == {'01', '10', '11'}:
            return ('OR', inputs)
        elif on_set == {'01', '10'}:
            return ('XOR', inputs)
        elif on_set == {'00', '01', '10'}:
            return ('NAND', inputs)
        elif on_set == {'00'}:
            return ('NOR', inputs)
        elif on_set == {'00', '11'}:
            return ('XNOR', inputs)

    return ('SOP', inputs)


def blif_to_truth_table(filename: str) -> Optional[TruthTable]:
    """Convert a BLIF file to a truth table by simulation."""
    result = read_blif(filename)
    if result is None:
        return None

    inputs, outputs, gates = result
    n_inputs = len(inputs)
    n_outputs = len(outputs)

    if n_inputs > 20:
        return None

    tables = [0] * n_outputs

    for pattern in range(1 << n_inputs):
        # Set input values
        sig_vals = {}
        for i, inp in enumerate(inputs):
            sig_vals[inp] = (pattern >> i) & 1

        # Evaluate gates in topological order
        _evaluate(gates, sig_vals)

        # Read outputs
        for j, out in enumerate(outputs):
            if sig_vals.get(out, 0):
                tables[j] |= (1 << pattern)

    return TruthTable(n_inputs, n_outputs, tuple(tables))


def _evaluate(gates: dict, sig_vals: dict):
    """Evaluate all gates given input signal values."""
    evaluated = set(sig_vals.keys())
    queue = list(gates.keys())
    max_iter = len(queue) * 2
    iteration = 0

    while queue and iteration < max_iter:
        iteration += 1
        next_queue = []
        for sig in queue:
            if sig in evaluated:
                continue
            gate_type, gate_inputs = gates[sig]
            # Check if all inputs are ready
            if all(inp in evaluated for inp in gate_inputs):
                val = _eval_gate(gate_type, [sig_vals.get(inp, 0) for inp in gate_inputs])
                sig_vals[sig] = val
                evaluated.add(sig)
            else:
                next_queue.append(sig)
        queue = next_queue


def _eval_gate(gate_type: str, inputs: list[int]) -> int:
    if gate_type == 'AND':
        return inputs[0] & inputs[1]
    elif gate_type == 'OR':
        return inputs[0] | inputs[1]
    elif gate_type == 'XOR':
        return inputs[0] ^ inputs[1]
    elif gate_type == 'NOT':
        return 1 - inputs[0]
    elif gate_type == 'BUF':
        return inputs[0]
    elif gate_type == 'NAND':
        return 1 - (inputs[0] & inputs[1])
    elif gate_type == 'NOR':
        return 1 - (inputs[0] | inputs[1])
    elif gate_type == 'XNOR':
        return 1 - (inputs[0] ^ inputs[1])
    elif gate_type == 'CONST0':
        return 0
    elif gate_type == 'CONST1':
        return 1
    return 0


def blif_to_aig(filename: str) -> Optional[Circuit]:
    """Convert a BLIF file to an AIG circuit directly (preserving structure)."""
    result = read_blif(filename)
    if result is None:
        return None

    inputs, outputs, gates = result
    from solver import AIGBuilder, CONST1

    builder = AIGBuilder(len(inputs))
    sig_to_lit = {}

    for i, inp in enumerate(inputs):
        sig_to_lit[inp] = builder.input(i)

    # Topological eval
    queue = list(gates.keys())
    max_iter = len(queue) * 2
    iteration = 0

    while queue and iteration < max_iter:
        iteration += 1
        next_queue = []
        for sig in queue:
            if sig in sig_to_lit:
                continue
            gate_type, gate_inputs = gates[sig]
            if all(inp in sig_to_lit for inp in gate_inputs):
                lits = [sig_to_lit[inp] for inp in gate_inputs]
                lit = _build_gate(builder, gate_type, lits)
                sig_to_lit[sig] = lit
            else:
                next_queue.append(sig)
        queue = next_queue

    out_lits = [sig_to_lit.get(out, 0) for out in outputs]
    return builder.build(out_lits)


def _build_gate(builder, gate_type: str, lits: list[int]) -> int:
    from solver import CONST1
    if gate_type == 'AND':
        return builder.add_and(lits[0], lits[1])
    elif gate_type == 'OR':
        return builder.add_or(lits[0], lits[1])
    elif gate_type == 'XOR':
        return builder.add_xor(lits[0], lits[1])
    elif gate_type == 'NOT':
        if lits[0] == 0:
            return CONST1
        return -lits[0]
    elif gate_type == 'BUF':
        return lits[0]
    elif gate_type == 'NAND':
        return -builder.add_and(lits[0], lits[1])
    elif gate_type == 'NOR':
        return -builder.add_or(lits[0], lits[1])
    elif gate_type == 'XNOR':
        return -builder.add_xor(lits[0], lits[1])
    elif gate_type == 'CONST0':
        return 0
    elif gate_type == 'CONST1':
        return CONST1
    return 0
