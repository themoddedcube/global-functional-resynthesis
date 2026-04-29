"""Theory D: Progressive Hierarchical Resynthesis.

Start with small optimization windows (k=3), find optimal sub-circuit
implementations, progressively expand window size. Each level captures
more global structure.
"""

from __future__ import annotations

import itertools
from typing import Optional

from benchmark import TruthTable, Circuit, AIGNode, verify_equivalence


def progressive_resynthesis(circuit: Circuit, tt: TruthTable,
                            max_cut_size: int = 5) -> Circuit:
    """Progressively optimize circuit with increasing window sizes."""
    current = circuit
    for cut_size in range(3, max_cut_size + 1):
        improved = True
        max_passes = 10
        passes = 0
        while improved and passes < max_passes:
            improved = False
            passes += 1
            nodes = [n for n in current.nodes.values() if n.type == 'AND']
            nodes.sort(key=lambda n: n.id)

            for node in nodes:
                if node.id not in current.nodes:
                    continue
                cuts = find_k_cuts(current, node.id, cut_size)
                for cut_inputs, cut_nodes in cuts:
                    if len(cut_nodes) <= 1:
                        continue
                    sub_tt = extract_cut_truth_table(current, node.id, cut_inputs)
                    if sub_tt is None:
                        continue
                    optimal = find_optimal_circuit(sub_tt)
                    if optimal is None:
                        continue
                    if optimal.gate_count() < len(cut_nodes):
                        new_circuit = replace_cut(current, node.id, cut_inputs,
                                                  cut_nodes, optimal)
                        if new_circuit is not None and verify_equivalence(new_circuit, tt):
                            current = new_circuit
                            improved = True
                            break

    return current


def find_k_cuts(circuit: Circuit, node_id: int, k: int) -> list[tuple[list[int], list[int]]]:
    """Find k-feasible cuts for a node.

    Returns list of (cut_inputs, cut_interior_nodes) where:
    - cut_inputs: list of node IDs that are inputs to the cut
    - cut_interior_nodes: list of AND node IDs inside the cut
    """
    if node_id not in circuit.nodes:
        return []
    node = circuit.nodes[node_id]
    if node.type != 'AND':
        return []

    cuts = []

    # BFS/DFS to enumerate cuts
    def _enum_cuts(root_id: int, max_inputs: int):
        frontier = {root_id}
        interior = set()
        inputs = set()

        queue = [root_id]
        while queue:
            nid = queue.pop()
            if nid in interior:
                continue
            n = circuit.nodes.get(nid)
            if n is None or n.type != 'AND':
                inputs.add(nid)
                continue
            interior.add(nid)
            for fanin in [n.fanin0, n.fanin1]:
                fid = abs(fanin)
                if fid not in interior:
                    fn = circuit.nodes.get(fid)
                    if fn is None or fn.type != 'AND':
                        inputs.add(fid)
                    else:
                        queue.append(fid)

            if len(inputs) > max_inputs:
                return

        if len(inputs) <= max_inputs and interior:
            cuts.append((sorted(inputs), sorted(interior)))

    _enum_cuts(node_id, k)

    # Also try partial cuts: stop expanding at some AND nodes
    node = circuit.nodes[node_id]
    if node.type == 'AND':
        for depth_limit in range(1, 4):
            _enum_partial_cuts(circuit, node_id, k, depth_limit, cuts)

    return cuts


def _enum_partial_cuts(circuit: Circuit, root_id: int, k: int,
                       depth_limit: int, result: list):
    """Enumerate cuts with limited expansion depth."""
    interior = set()
    inputs = set()

    def expand(nid: int, depth: int):
        if nid in interior:
            return
        n = circuit.nodes.get(nid)
        if n is None or n.type != 'AND' or depth >= depth_limit:
            inputs.add(nid)
            return
        interior.add(nid)
        for fanin in [n.fanin0, n.fanin1]:
            expand(abs(fanin), depth + 1)

    expand(root_id, 0)
    if len(inputs) <= k and interior:
        cut = (sorted(inputs), sorted(interior))
        if cut not in result:
            result.append(cut)


def extract_cut_truth_table(circuit: Circuit, root_id: int,
                            cut_inputs: list[int]) -> Optional[TruthTable]:
    """Extract truth table for the sub-circuit defined by a cut."""
    n_inputs = len(cut_inputs)
    if n_inputs > 10:
        return None

    input_map = {nid: i for i, nid in enumerate(cut_inputs)}

    # For each output node (root_id with its polarity from parent)
    # we need to simulate the sub-circuit
    root_node = circuit.nodes.get(root_id)
    if root_node is None:
        return None

    bits = 0
    for pattern in range(1 << n_inputs):
        values = {}
        for nid, idx in input_map.items():
            values[nid] = (pattern >> idx) & 1

        val = _sim_node(circuit, root_id, values, cut_inputs)
        if val:
            bits |= (1 << pattern)

    return TruthTable(n_inputs, 1, (bits,))


def _sim_node(circuit: Circuit, nid: int, values: dict, cut_inputs: list) -> int:
    """Simulate a node given input values, stopping at cut boundary."""
    if nid in values:
        return values[nid]
    node = circuit.nodes.get(nid)
    if node is None:
        return 0
    if node.type == 'CONST0':
        values[nid] = 0
        return 0
    if node.type == 'INPUT':
        return values.get(nid, 0)
    if node.type == 'AND':
        def eval_lit(lit):
            fid = abs(lit)
            v = _sim_node(circuit, fid, values, cut_inputs)
            return v ^ (1 if lit < 0 else 0)
        val = eval_lit(node.fanin0) & eval_lit(node.fanin1)
        values[nid] = val
        return val
    return 0


# Cache for optimal circuits of small truth tables
_optimal_cache: dict[tuple, Optional[Circuit]] = {}


def find_optimal_circuit(tt: TruthTable) -> Optional[Circuit]:
    """Find optimal AIG for a small truth table."""
    if tt.n_inputs > 5:
        return None

    key = tt.table
    if key in _optimal_cache:
        return _optimal_cache[key]

    from solver import _exact_single_output
    result = _exact_single_output(tt, max_gates=10)
    _optimal_cache[key] = result
    return result


def replace_cut(circuit: Circuit, root_id: int, cut_inputs: list[int],
                cut_interior: list[int], replacement: Circuit) -> Optional[Circuit]:
    """Replace a cut in the circuit with a new sub-circuit."""
    new_circuit = circuit.copy()

    # Remove interior nodes
    for nid in cut_interior:
        if nid in new_circuit.nodes:
            del new_circuit.nodes[nid]

    # Add replacement nodes, remapping IDs
    remap = {}
    for i, inp_id in enumerate(replacement.inputs):
        remap[inp_id] = cut_inputs[i]

    sorted_nodes = sorted(
        [n for n in replacement.nodes.values() if n.type == 'AND'],
        key=lambda n: n.id
    )

    for node in sorted_nodes:
        def remap_lit(lit):
            fid = abs(lit)
            mapped = remap.get(fid, fid)
            return -mapped if lit < 0 else mapped

        new_id = new_circuit._next_id
        new_circuit._next_id += 1
        new_circuit.nodes[new_id] = AIGNode(
            new_id, 'AND', remap_lit(node.fanin0), remap_lit(node.fanin1)
        )
        remap[node.id] = new_id

    # Remap the output of the replacement to replace root_id references
    if replacement.outputs:
        out_lit = replacement.outputs[0]
        out_fid = abs(out_lit)
        mapped_out = remap.get(out_fid, out_fid)
        if out_lit < 0:
            mapped_out = -mapped_out
    else:
        return None

    # Replace all references to root_id with the replacement output
    for nid, node in new_circuit.nodes.items():
        if node.type == 'AND':
            if abs(node.fanin0) == root_id:
                sign = -1 if node.fanin0 < 0 else 1
                new_lit = mapped_out if sign > 0 else -mapped_out
                if mapped_out < 0:
                    new_lit = -mapped_out if sign > 0 else mapped_out
                node.fanin0 = mapped_out * sign
            if abs(node.fanin1) == root_id:
                sign = -1 if node.fanin1 < 0 else 1
                node.fanin1 = mapped_out * sign

    new_circuit.outputs = [
        (mapped_out if abs(o) == root_id and o > 0 else
         -mapped_out if abs(o) == root_id and o < 0 else o)
        for o in new_circuit.outputs
    ]

    return new_circuit
