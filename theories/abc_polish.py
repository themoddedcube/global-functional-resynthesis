"""Use ABC as a polishing backend for AIG optimization.

Our solver finds the decomposition; ABC's rewrite/resub cleans it up.
This gives ABC-quality local optimization on our global structure.
"""

from __future__ import annotations

import subprocess
import tempfile
import os
from typing import Optional

from benchmark import TruthTable, Circuit, AIGNode, verify_equivalence

ABC_PATH = '/tmp/abc/abc'
ABC_RC = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'abc.rc')


def _encode_delta(delta: int) -> bytes:
    """Encode a non-negative integer in AIGER binary variable-length format."""
    result = bytearray()
    while delta >= 0x80:
        result.append((delta & 0x7f) | 0x80)
        delta >>= 7
    result.append(delta & 0x7f)
    return bytes(result)


def write_aiger(circuit: Circuit, filename: str):
    """Write circuit in binary AIGER format (.aig)."""
    n_inputs = len(circuit.inputs)
    n_outputs = len(circuit.outputs)
    and_nodes = sorted([n for n in circuit.nodes.values() if n.type == 'AND'],
                       key=lambda n: n.id)
    n_ands = len(and_nodes)

    var_map = {}
    var_map[0] = 0
    next_var = 1
    for inp_id in circuit.inputs:
        var_map[inp_id] = next_var
        next_var += 1
    for node in and_nodes:
        var_map[node.id] = next_var
        next_var += 1

    def lit_to_aiger(lit: int) -> int:
        nid = abs(lit)
        if nid not in var_map:
            return 0
        aiger_var = var_map[nid]
        aiger_lit = aiger_var * 2
        if lit < 0:
            aiger_lit += 1
        return aiger_lit

    M = next_var - 1

    with open(filename, 'wb') as f:
        header = f"aig {M} {n_inputs} 0 {n_outputs} {n_ands}\n"
        f.write(header.encode())

        for out in circuit.outputs:
            f.write(f"{lit_to_aiger(out)}\n".encode())

        for node in and_nodes:
            out_lit = var_map[node.id] * 2
            in0_lit = lit_to_aiger(node.fanin0)
            in1_lit = lit_to_aiger(node.fanin1)
            d0 = out_lit - max(in0_lit, in1_lit)
            d1 = max(in0_lit, in1_lit) - min(in0_lit, in1_lit)
            f.write(_encode_delta(d0))
            f.write(_encode_delta(d1))


def _decode_delta(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a variable-length integer, return (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        b = data[pos]
        result |= (b & 0x7f) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def read_aiger(filename: str, n_inputs: int) -> Optional[Circuit]:
    """Read circuit from binary AIGER format (.aig)."""
    with open(filename, 'rb') as f:
        raw = f.read()

    header_end = raw.index(b'\n')
    header = raw[:header_end].decode().split()

    if header[0] == 'aag':
        return _read_aiger_ascii(raw.decode())
    if header[0] != 'aig':
        return None

    M = int(header[1])
    I = int(header[2])
    L = int(header[3])
    O = int(header[4])
    A = int(header[5])

    pos = header_end + 1

    output_lits = []
    for _ in range(O):
        line_end = raw.index(b'\n', pos)
        output_lits.append(int(raw[pos:line_end].decode()))
        pos = line_end + 1

    and_gates = []
    for i in range(A):
        out_lit = 2 * (I + 1 + i)
        d0, pos = _decode_delta(raw, pos)
        d1, pos = _decode_delta(raw, pos)
        in0 = out_lit - d0
        in1 = in0 - d1
        and_gates.append((out_lit, in0, in1))

    circuit = Circuit.new(I)

    aiger_to_node = {0: 0}
    for i in range(I):
        aiger_to_node[i + 1] = circuit.inputs[i]

    def aiger_lit_to_circuit(aiger_lit: int) -> int:
        var = aiger_lit // 2
        inv = aiger_lit & 1
        if var not in aiger_to_node:
            return 0
        nid = aiger_to_node[var]
        return -nid if inv else nid

    for out_lit, in0_lit, in1_lit in and_gates:
        out_var = out_lit // 2
        fanin0 = aiger_lit_to_circuit(in0_lit)
        fanin1 = aiger_lit_to_circuit(in1_lit)
        new_id = circuit.add_and(fanin0, fanin1)
        aiger_to_node[out_var] = new_id

    outputs = [aiger_lit_to_circuit(lit) for lit in output_lits]
    circuit.set_outputs(outputs)
    return circuit


def _read_aiger_ascii(content: str) -> Optional[Circuit]:
    """Read circuit from ASCII AIGER format (aag)."""
    lines = content.strip().split('\n')
    if not lines:
        return None
    header = lines[0].split()
    if header[0] != 'aag':
        return None

    I = int(header[2])
    L = int(header[3])
    O = int(header[4])
    A = int(header[5])

    idx = 1
    input_lits = [int(lines[idx + i]) for i in range(I)]
    idx += I + L
    output_lits = [int(lines[idx + i]) for i in range(O)]
    idx += O

    and_gates = []
    for i in range(A):
        parts = lines[idx + i].split()
        and_gates.append((int(parts[0]), int(parts[1]), int(parts[2])))

    circuit = Circuit.new(I)
    aiger_to_node = {0: 0}
    for i, lit in enumerate(input_lits):
        aiger_to_node[lit // 2] = circuit.inputs[i]

    def aiger_lit_to_circuit(aiger_lit: int) -> int:
        var = aiger_lit // 2
        inv = aiger_lit & 1
        if var not in aiger_to_node:
            return 0
        nid = aiger_to_node[var]
        return -nid if inv else nid

    for out_lit, in0_lit, in1_lit in and_gates:
        fanin0 = aiger_lit_to_circuit(in0_lit)
        fanin1 = aiger_lit_to_circuit(in1_lit)
        new_id = circuit.add_and(fanin0, fanin1)
        aiger_to_node[out_lit // 2] = new_id

    outputs = [aiger_lit_to_circuit(lit) for lit in output_lits]
    circuit.set_outputs(outputs)
    return circuit


def abc_optimize(circuit: Circuit, tt: TruthTable,
                 script: str = 'resyn2') -> Optional[Circuit]:
    """Run ABC optimization on a circuit and return the improved version."""
    if not os.path.exists(ABC_PATH):
        return None

    scripts = {
        'resyn2': 'b; rw; rf; b; rw; rwz; b; rfz; rwz; b',
        'resyn2rs': 'b; rs -K 6; rw; rs -K 6 -N 2; rf; rs -K 8; b; rs -K 8 -N 2; rw; rs -K 10; rwz; rs -K 10 -N 2; b; rs -K 12; rfz; rs -K 12 -N 2; rwz; b',
        'compress2': 'b -l; rw -l; rf -l; b -l; rw -l; rwz -l; b -l; rfz -l; rwz -l; b -l',
    }
    abc_script = scripts.get(script, scripts['resyn2'])

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, 'input.aig')
        output_file = os.path.join(tmpdir, 'output.aig')

        write_aiger(circuit, input_file)

        abc_cmd = f"source -s {ABC_RC}; read_aiger {input_file}; strash; {abc_script}; write_aiger {output_file}"

        try:
            result = subprocess.run(
                [ABC_PATH, '-c', abc_cmd],
                capture_output=True, text=True, timeout=30,
                cwd=os.path.dirname(ABC_RC)
            )

            if not os.path.exists(output_file):
                return None

            opt_circuit = read_aiger(output_file, len(circuit.inputs))
            if opt_circuit is None:
                return None

            if verify_equivalence(opt_circuit, tt):
                return opt_circuit

        except (subprocess.TimeoutExpired, Exception):
            pass

    return None


def abc_polish(circuit: Circuit, tt: TruthTable, max_rounds: int = 5) -> Circuit:
    """Iteratively apply ABC optimization scripts until convergence."""
    best = circuit

    for _ in range(max_rounds):
        improved = False
        for script in ['resyn2', 'resyn2rs', 'compress2']:
            try:
                result = abc_optimize(best, tt, script)
                if result is not None and result.gate_count() < best.gate_count():
                    best = result
                    improved = True
            except Exception:
                continue
        if not improved:
            break

    return best


def abc_synthesize_single(tt_bits: int, n_inputs: int, n_outputs: int) -> Optional[Circuit]:
    """Synthesize a single-output function using ABC's read_truth + optimization."""
    if not os.path.exists(ABC_PATH):
        return None

    n_chars = max(1, (1 << n_inputs) // 4)
    hex_str = format(tt_bits, f'0{n_chars}x')

    scripts = [
        'b; rw; rf; b; rw; rwz; b; rfz; rwz; b',
        'b; rs -K 6; rw; rs -K 6 -N 2; rf; rs -K 8; b; rs -K 8 -N 2; rw; rs -K 10; rwz; rs -K 10 -N 2; b; rs -K 12; rfz; rs -K 12 -N 2; rwz; b',
    ]

    best = None
    best_gates = float('inf')

    for script in scripts:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = os.path.join(tmpdir, 'output.aig')
            abc_cmd = (f"source -s {ABC_RC}; read_truth {hex_str}; strash; "
                       f"{script}; {script}; write_aiger {output_file}")
            try:
                result = subprocess.run(
                    [ABC_PATH, '-c', abc_cmd],
                    capture_output=True, text=True, timeout=30,
                    cwd=os.path.dirname(ABC_RC)
                )
                if os.path.exists(output_file):
                    circ = read_aiger(output_file, n_inputs)
                    if circ and circ.gate_count() < best_gates:
                        best = circ
                        best_gates = circ.gate_count()
            except Exception:
                pass

    return best


def abc_synthesize_multi(tt: TruthTable) -> Optional[Circuit]:
    """Synthesize multi-output function by per-output ABC synthesis + shared builder."""
    if not os.path.exists(ABC_PATH):
        return None

    from solver import AIGBuilder, _embed_circuit

    builder = AIGBuilder(tt.n_inputs)
    outputs = []

    for j in range(tt.n_outputs):
        sub = abc_synthesize_single(tt.table[j], tt.n_inputs, 1)
        if sub is not None:
            lit = _embed_circuit(sub, list(range(tt.n_inputs)), builder)
            outputs.append(lit)
        else:
            return None

    return builder.build(outputs)
