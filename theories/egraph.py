"""E-graph equality saturation for Boolean synthesis.

Uses truth-table-keyed e-classes to discover shared sub-structure.
Multi-output circuits share a single e-graph so common sub-functions merge automatically.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from benchmark import TruthTable, Circuit, verify_equivalence


@dataclasses.dataclass
class ENode:
    """One way to compute an e-class's function."""
    op: str         # 'AND', 'OR', 'XOR', 'NOT', 'INPUT', 'MUX', 'CONST0', 'CONST1'
    children: tuple  # tuple of e-class IDs
    aig_cost: int = 1


@dataclasses.dataclass
class EClass:
    """Equivalence class — all nodes compute the same truth table."""
    id: int
    tt: int  # single-output truth table bits (raw int for speed)
    nodes: list[ENode]
    best_cost: Optional[int] = None


class EGraph:
    """E-graph for multi-output Boolean function equivalence exploration.

    Key invariant: e-classes with identical truth tables are the same class.
    """

    def __init__(self, n_inputs: int):
        self.n_inputs = n_inputs
        self.n_rows = 1 << n_inputs
        self.all_ones = (1 << self.n_rows) - 1
        self.classes: dict[int, EClass] = {}
        self.tt_to_class: dict[int, int] = {}  # tt_bits -> class_id
        self._next_id = 0
        self._input_classes: list[int] = []

        for i in range(n_inputs):
            bits = self._input_tt_bits(i)
            cid = self._get_or_create(bits)
            self._input_classes.append(cid)
            self.classes[cid].nodes.append(ENode('INPUT', (i,), aig_cost=0))
            self.classes[cid].best_cost = 0

        self._const0_class = self._get_or_create(0)
        self.classes[self._const0_class].nodes.append(ENode('CONST0', (), aig_cost=0))
        self.classes[self._const0_class].best_cost = 0

        self._const1_class = self._get_or_create(self.all_ones)
        self.classes[self._const1_class].nodes.append(ENode('CONST1', (), aig_cost=0))
        self.classes[self._const1_class].best_cost = 0

    def _input_tt_bits(self, var: int) -> int:
        bits = 0
        for p in range(self.n_rows):
            if (p >> var) & 1:
                bits |= (1 << p)
        return bits

    def _get_or_create(self, tt_bits: int) -> int:
        if tt_bits in self.tt_to_class:
            return self.tt_to_class[tt_bits]
        cid = self._next_id
        self._next_id += 1
        self.classes[cid] = EClass(cid, tt_bits, [])
        self.tt_to_class[tt_bits] = cid
        return cid

    def add_node(self, tt_bits: int, node: ENode) -> int:
        cid = self._get_or_create(tt_bits)
        ec = self.classes[cid]
        for existing in ec.nodes:
            if existing.op == node.op and existing.children == node.children:
                return cid
        ec.nodes.append(node)
        return cid

    # ------------------------------------------------------------------
    # Rewrite rules (saturation)
    # ------------------------------------------------------------------

    def saturate(self, max_iterations: int = 80, max_classes: int = 5000) -> int:
        """Apply rewrite rules until saturation or budget."""
        total_new = 0
        for _ in range(max_iterations):
            if len(self.classes) > max_classes:
                break
            new_this_round = self._one_round(max_classes)
            total_new += new_this_round
            if new_this_round == 0:
                break
        return total_new

    def _one_round(self, max_classes: int) -> int:
        new_count = 0
        class_ids = list(self.classes.keys())
        for cid in class_ids:
            if len(self.classes) > max_classes:
                break
            ec = self.classes[cid]
            if ec.best_cost == 0:
                continue
            new_count += self._decompose(cid, ec.tt)
            new_count += self._algebraic(cid, ec.tt)
        return new_count

    def _cofactor(self, bits: int, var: int, value: int) -> int:
        """Compute cofactor of bits w.r.t. variable var set to value."""
        result = 0
        step = 1 << var
        for p in range(self.n_rows):
            modified_p = (p & ~(1 << var)) | (value << var)
            if (bits >> modified_p) & 1:
                result |= (1 << p)
        return result

    def _depends_on(self, bits: int, var: int) -> bool:
        cof0 = self._cofactor(bits, var, 0)
        cof1 = self._cofactor(bits, var, 1)
        return cof0 != cof1

    def _decompose(self, cid: int, tt_bits: int) -> int:
        """Shannon decomposition at each variable + XOR decomposition."""
        new_count = 0
        for var in range(self.n_inputs):
            if not self._depends_on(tt_bits, var):
                continue

            cof0 = self._cofactor(tt_bits, var, 0)
            cof1 = self._cofactor(tt_bits, var, 1)
            cof0_cid = self._get_or_create(cof0)
            cof1_cid = self._get_or_create(cof1)
            inp_cid = self._input_classes[var]

            # MUX(var, cof1, cof0) = 3 AND gates
            node = ENode('MUX', (inp_cid, cof1_cid, cof0_cid), aig_cost=3)
            before = len(self.classes[cid].nodes)
            self.add_node(tt_bits, node)
            if len(self.classes[cid].nodes) > before:
                new_count += 1

            # XOR decomposition: f = cof0 XOR (var AND (cof0 XOR cof1))
            diff = cof0 ^ cof1
            if diff != 0 and diff != self.all_ones:
                diff_cid = self._get_or_create(diff)
                inp_bits = self.classes[inp_cid].tt
                and_bits = diff & inp_bits
                and_cid = self._get_or_create(and_bits)
                self.add_node(and_bits, ENode('AND', (diff_cid, inp_cid), aig_cost=1))
                xor_result = cof0 ^ and_bits
                if xor_result == tt_bits:
                    node = ENode('XOR', (cof0_cid, and_cid), aig_cost=4)
                    before2 = len(self.classes[cid].nodes)
                    self.add_node(tt_bits, node)
                    if len(self.classes[cid].nodes) > before2:
                        new_count += 1

        return new_count

    def _algebraic(self, cid: int, tt_bits: int) -> int:
        """AND/OR/XOR with inputs and their complements."""
        new_count = 0
        not_bits = tt_bits ^ self.all_ones
        not_cid = self._get_or_create(not_bits)
        before = len(self.classes[not_cid].nodes)
        self.add_node(not_bits, ENode('NOT', (cid,), aig_cost=0))
        if len(self.classes[not_cid].nodes) > before:
            new_count += 1

        for inp_cid in self._input_classes:
            inp_bits = self.classes[inp_cid].tt

            # AND with input
            and_bits = tt_bits & inp_bits
            if and_bits != 0 and and_bits != tt_bits:
                self._get_or_create(and_bits)

            # AND with NOT input
            ninp_bits = inp_bits ^ self.all_ones
            and_ninp = tt_bits & ninp_bits
            if and_ninp != 0 and and_ninp != tt_bits:
                self._get_or_create(and_ninp)

            # XOR with input
            xor_bits = tt_bits ^ inp_bits
            if xor_bits != 0 and xor_bits != self.all_ones:
                self._get_or_create(xor_bits)

        # Try AND/OR decomposition of existing small classes
        small_classes = [c for c in self.classes.values()
                         if c.best_cost is not None and 0 < c.best_cost <= 3]
        for sc in small_classes[:20]:
            and_bits = tt_bits & sc.tt
            if and_bits != 0 and and_bits != tt_bits and and_bits != sc.tt:
                a_cid = self._get_or_create(and_bits)
                # Check if tt = and_bits OR something
                remainder = tt_bits & ~sc.tt
                if remainder | and_bits == tt_bits:
                    rem_cid = self._get_or_create(remainder)
                    self.add_node(tt_bits, ENode('OR', (a_cid, rem_cid), aig_cost=1))
                    new_count += 1

        return new_count

    # ------------------------------------------------------------------
    # Cost computation — the key fix
    # ------------------------------------------------------------------

    def compute_costs(self):
        """Propagate costs bottom-up.

        After propagation, any class still without a cost gets synthesized
        directly (ABC for small functions, then Shannon fallback).
        """
        # Phase 1: propagate from known costs
        changed = True
        while changed:
            changed = False
            for cid, ec in self.classes.items():
                if ec.best_cost == 0:
                    continue
                for node in ec.nodes:
                    cost = self._node_cost(node)
                    if cost is not None and (ec.best_cost is None or cost < ec.best_cost):
                        ec.best_cost = cost
                        changed = True

        # Phase 2: for classes with no cost, try direct synthesis
        uncosted = [cid for cid, ec in self.classes.items() if ec.best_cost is None]
        if uncosted:
            self._synthesize_uncosted(uncosted)
            # Re-propagate after adding new costs
            changed = True
            while changed:
                changed = False
                for cid, ec in self.classes.items():
                    if ec.best_cost == 0:
                        continue
                    for node in ec.nodes:
                        cost = self._node_cost(node)
                        if cost is not None and (ec.best_cost is None or cost < ec.best_cost):
                            ec.best_cost = cost
                            changed = True

    def _synthesize_uncosted(self, class_ids: list[int]):
        """Assign costs to classes that couldn't get them from node propagation.

        Uses recursive Shannon cost estimation — no external tools.
        Processes in order of support size (smaller first) so costs cascade.
        """
        # Sort by support size so smaller functions get costed first
        def support_size(cid):
            bits = self.classes[cid].tt
            return sum(1 for v in range(self.n_inputs) if self._depends_on(bits, v))

        sorted_ids = sorted(class_ids, key=support_size)

        for cid in sorted_ids:
            ec = self.classes[cid]
            if ec.best_cost is not None:
                continue
            tt_bits = ec.tt
            support = [v for v in range(self.n_inputs) if self._depends_on(tt_bits, v)]

            if not support:
                ec.best_cost = 0
                continue

            # Try to find cost via cofactors (Shannon MUX decomposition)
            best_cost = None
            for var in support:
                cof0 = self._cofactor(tt_bits, var, 0)
                cof1 = self._cofactor(tt_bits, var, 1)
                cof0_cid = self._get_or_create(cof0)
                cof1_cid = self._get_or_create(cof1)
                c0 = self.classes[cof0_cid].best_cost
                c1 = self.classes[cof1_cid].best_cost
                if c0 is not None and c1 is not None:
                    cost = 3 + c0 + c1
                    if best_cost is None or cost < best_cost:
                        best_cost = cost

            if best_cost is not None:
                ec.best_cost = best_cost
            else:
                ec.best_cost = len(support) * 3

    def _node_cost(self, node: ENode) -> Optional[int]:
        if node.op in ('INPUT', 'CONST0', 'CONST1'):
            return 0
        if node.op == 'NOT':
            child_cost = self.classes[node.children[0]].best_cost
            return child_cost if child_cost is not None else None
        if node.op == 'AND':
            c0 = self.classes[node.children[0]].best_cost
            c1 = self.classes[node.children[1]].best_cost
            if c0 is not None and c1 is not None:
                return 1 + c0 + c1
            return None
        if node.op == 'OR':
            c0 = self.classes[node.children[0]].best_cost
            c1 = self.classes[node.children[1]].best_cost
            if c0 is not None and c1 is not None:
                return 1 + c0 + c1
            return None
        if node.op == 'XOR':
            c0 = self.classes[node.children[0]].best_cost
            c1 = self.classes[node.children[1]].best_cost
            if c0 is not None and c1 is not None:
                return 4 + c0 + c1
            return None
        if node.op == 'MUX':
            c0 = self.classes[node.children[0]].best_cost
            c1 = self.classes[node.children[1]].best_cost
            c2 = self.classes[node.children[2]].best_cost
            if all(c is not None for c in [c0, c1, c2]):
                return 3 + c0 + c1 + c2
            return None
        return None

    # ------------------------------------------------------------------
    # Extraction — builds actual AIG with sharing across outputs
    # ------------------------------------------------------------------

    def extract(self, output_tts: list[int]) -> Optional[Circuit]:
        """Extract a multi-output circuit from the e-graph.

        Uses a single AIGBuilder so gates are shared across outputs.
        """
        from solver import AIGBuilder, CONST1

        builder = AIGBuilder(self.n_inputs)
        memo: dict[int, int] = {}  # class_id -> AIG literal
        in_progress: set[int] = set()  # cycle detection

        def _extract_class(cid: int) -> int:
            if cid in memo:
                return memo[cid]

            ec = self.classes[cid]

            if cid in self._input_classes:
                idx = self._input_classes.index(cid)
                lit = builder.input(idx)
                memo[cid] = lit
                return lit
            if cid == self._const0_class:
                memo[cid] = 0
                return 0
            if cid == self._const1_class:
                memo[cid] = CONST1
                return CONST1

            # Check if complement of a known class
            not_bits = ec.tt ^ self.all_ones
            if not_bits in self.tt_to_class:
                not_cid = self.tt_to_class[not_bits]
                if not_cid in memo:
                    lit = -memo[not_cid] if memo[not_cid] != 0 else CONST1
                    memo[cid] = lit
                    return lit

            # Cycle detection — use fallback if we're already extracting this
            if cid in in_progress:
                lit = self._fallback_synthesize(ec, builder)
                memo[cid] = lit
                return lit

            in_progress.add(cid)

            # Find best non-NOT node first (NOT creates cycles easily)
            best_node = None
            best_cost = float('inf')
            for node in ec.nodes:
                if node.op == 'NOT':
                    continue
                c = self._node_cost(node)
                if c is not None and c < best_cost:
                    best_cost = c
                    best_node = node

            # If no non-NOT node, try NOT but only if child isn't in-progress
            if best_node is None:
                for node in ec.nodes:
                    if node.op == 'NOT' and node.children[0] not in in_progress:
                        c = self._node_cost(node)
                        if c is not None and c < best_cost:
                            best_cost = c
                            best_node = node

            if best_node is None:
                lit = self._fallback_synthesize(ec, builder)
                memo[cid] = lit
                in_progress.discard(cid)
                return lit

            lit = self._build_node(best_node, builder, _extract_class)
            memo[cid] = lit
            in_progress.discard(cid)
            return lit

        outputs = []
        for tt_bits in output_tts:
            if tt_bits == 0:
                outputs.append(0)
            elif tt_bits == self.all_ones:
                outputs.append(CONST1)
            elif tt_bits in self.tt_to_class:
                cid = self.tt_to_class[tt_bits]
                outputs.append(_extract_class(cid))
            else:
                # Not in e-graph — synthesize directly
                cid = self._get_or_create(tt_bits)
                self._synthesize_uncosted([cid])
                outputs.append(_extract_class(cid))

        return builder.build(outputs)

    def _build_node(self, node: ENode, builder, extract_fn) -> int:
        from solver import CONST1
        if node.op == 'NOT':
            child_lit = extract_fn(node.children[0])
            if child_lit == 0:
                return CONST1
            return -child_lit
        elif node.op == 'AND':
            a = extract_fn(node.children[0])
            b = extract_fn(node.children[1])
            return builder.add_and(a, b)
        elif node.op == 'OR':
            a = extract_fn(node.children[0])
            b = extract_fn(node.children[1])
            return builder.add_or(a, b)
        elif node.op == 'XOR':
            a = extract_fn(node.children[0])
            b = extract_fn(node.children[1])
            return builder.add_xor(a, b)
        elif node.op == 'MUX':
            s = extract_fn(node.children[0])
            t = extract_fn(node.children[1])
            e = extract_fn(node.children[2])
            return builder.add_mux(s, t, e)
        return 0

    def _fallback_synthesize(self, ec: EClass, builder) -> int:
        """Synthesize a class that has no usable nodes — use Shannon in the shared builder."""
        from solver import CONST1
        tt_bits = ec.tt
        if tt_bits == 0:
            return 0
        if tt_bits == self.all_ones:
            return CONST1

        # Pick best variable
        support = [v for v in range(self.n_inputs) if self._depends_on(tt_bits, v)]
        if not support:
            return CONST1 if tt_bits & 1 else 0

        # Use variable with most balanced cofactors
        best_var = support[0]
        best_diff = float('inf')
        for v in support:
            cof0 = self._cofactor(tt_bits, v, 0)
            cof1 = self._cofactor(tt_bits, v, 1)
            ones0 = bin(cof0).count('1')
            ones1 = bin(cof1).count('1')
            diff = abs(ones0 - ones1) + abs(ones0 - self.n_rows // 2)
            if diff < best_diff:
                best_diff = diff
                best_var = v

        cof0 = self._cofactor(tt_bits, best_var, 0)
        cof1 = self._cofactor(tt_bits, best_var, 1)

        # Recurse through e-graph extraction (which gives sharing)
        cof0_cid = self._get_or_create(cof0)
        cof1_cid = self._get_or_create(cof1)

        # Inline MUX build
        sel = builder.input(best_var)
        # Need to extract children — but avoid infinite recursion
        # Use direct Shannon for the cofactors via the builder
        then_lit = self._simple_synth(cof1, builder, set())
        else_lit = self._simple_synth(cof0, builder, set())
        return builder.add_mux(sel, then_lit, else_lit)

    def _simple_synth(self, tt_bits: int, builder, visited: set) -> int:
        """Simple recursive Shannon synthesis in the shared builder."""
        from solver import CONST1
        if tt_bits == 0:
            return 0
        if tt_bits == self.all_ones:
            return CONST1

        # Check if this truth table already exists as a class with known extraction
        if tt_bits in self.tt_to_class:
            cid = self.tt_to_class[tt_bits]
            if cid in visited:
                pass  # avoid recursion
            else:
                ec = self.classes[cid]
                if cid in self._input_classes:
                    idx = self._input_classes.index(cid)
                    return builder.input(idx)

        # Check complement
        not_bits = tt_bits ^ self.all_ones
        if not_bits in self.tt_to_class:
            not_cid = self.tt_to_class[not_bits]
            if not_cid in self._input_classes:
                idx = self._input_classes.index(not_cid)
                return -builder.input(idx)

        support = [v for v in range(self.n_inputs) if self._depends_on(tt_bits, v)]
        if not support:
            return CONST1 if tt_bits & 1 else 0

        var = support[0]
        cof0 = self._cofactor(tt_bits, var, 0)
        cof1 = self._cofactor(tt_bits, var, 1)
        sel = builder.input(var)
        then_lit = self._simple_synth(cof1, builder, visited | {self.tt_to_class.get(tt_bits, -1)})
        else_lit = self._simple_synth(cof0, builder, visited | {self.tt_to_class.get(tt_bits, -1)})
        return builder.add_mux(sel, then_lit, else_lit)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def egraph_synthesize(tt: TruthTable, max_iterations: int = 80,
                      max_classes: int = 5000) -> Optional[Circuit]:
    """Synthesize a circuit using e-graph equality saturation.

    Works for both single and multi-output functions.
    Multi-output functions share a single e-graph, so common sub-functions
    are automatically discovered and shared in the extracted circuit.
    """
    eg = EGraph(tt.n_inputs)

    # Add all output functions to the e-graph
    output_tts = list(tt.table)
    for bits in output_tts:
        eg._get_or_create(bits)

    # Saturate
    eg.saturate(max_iterations=max_iterations, max_classes=max_classes)

    # Compute costs
    eg.compute_costs()

    # Extract shared circuit
    circuit = eg.extract(output_tts)
    if circuit is None:
        return None

    # Verify
    if verify_equivalence(circuit, tt):
        return circuit
    return None
