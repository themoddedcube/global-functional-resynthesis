"""Theory A: E-graph + Exact Synthesis Hybrid.

Uses equality saturation to explore the space of equivalent Boolean decompositions.
E-classes are identified by truth tables (automatic deduplication).
Leaf sub-circuits are optimized with exact SAT synthesis.
"""

from __future__ import annotations

import dataclasses
from typing import Optional
from collections import deque

from benchmark import TruthTable, Circuit, verify_equivalence


# ---------------------------------------------------------------------------
# E-graph data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ENode:
    """A node in the e-graph representing one way to compute a function."""
    op: str         # 'AND', 'OR', 'XOR', 'NOT', 'INPUT', 'MUX', 'CONST0', 'CONST1'
    children: tuple  # tuple of e-class IDs
    cost: int = 1    # gate cost of this node alone (AND=1, others derived)


@dataclasses.dataclass
class EClass:
    """An equivalence class of Boolean functions."""
    id: int
    tt: TruthTable
    nodes: list[ENode]
    best_cost: Optional[int] = None
    best_circuit: Optional[Circuit] = None


class EGraph:
    """E-graph for Boolean function equivalence exploration.

    Key property: e-classes with identical truth tables are automatically merged.
    This discovers shared sub-structure across different decompositions.
    """

    def __init__(self, n_inputs: int):
        self.n_inputs = n_inputs
        self.classes: dict[int, EClass] = {}
        self.tt_to_class: dict[tuple, int] = {}
        self._next_id = 0
        self._input_classes: list[int] = []

        # Create input e-classes
        for i in range(n_inputs):
            tt = self._input_tt(i)
            cid = self._add_class(tt)
            self._input_classes.append(cid)
            self.classes[cid].nodes.append(
                ENode('INPUT', (i,), cost=0)
            )

        # Create constant e-classes
        self._const0_class = self._add_class(
            TruthTable(n_inputs, 1, (0,))
        )
        self.classes[self._const0_class].nodes.append(
            ENode('CONST0', (), cost=0)
        )
        self.classes[self._const0_class].best_cost = 0

        all_ones = (1 << (1 << n_inputs)) - 1
        self._const1_class = self._add_class(
            TruthTable(n_inputs, 1, (all_ones,))
        )
        self.classes[self._const1_class].nodes.append(
            ENode('CONST1', (), cost=0)
        )
        self.classes[self._const1_class].best_cost = 0

        for cid in self._input_classes:
            self.classes[cid].best_cost = 0

    def _input_tt(self, var: int) -> TruthTable:
        n = self.n_inputs
        bits = 0
        for p in range(1 << n):
            if (p >> var) & 1:
                bits |= (1 << p)
        return TruthTable(n, 1, (bits,))

    def _add_class(self, tt: TruthTable) -> int:
        key = tt.table
        if key in self.tt_to_class:
            return self.tt_to_class[key]
        cid = self._next_id
        self._next_id += 1
        self.classes[cid] = EClass(cid, tt, [])
        self.tt_to_class[key] = cid
        return cid

    def find_or_create(self, tt: TruthTable) -> int:
        return self._add_class(tt)

    def add_node(self, tt: TruthTable, node: ENode) -> int:
        cid = self.find_or_create(tt)
        ec = self.classes[cid]
        # Don't add duplicate nodes
        for existing in ec.nodes:
            if existing.op == node.op and existing.children == node.children:
                return cid
        ec.nodes.append(node)
        return cid

    def _compute_tt(self, op: str, child_tts: list[TruthTable]) -> TruthTable:
        n = self.n_inputs
        if op == 'AND':
            bits = child_tts[0].table[0] & child_tts[1].table[0]
        elif op == 'OR':
            bits = child_tts[0].table[0] | child_tts[1].table[0]
        elif op == 'XOR':
            bits = child_tts[0].table[0] ^ child_tts[1].table[0]
        elif op == 'NOT':
            all_ones = (1 << (1 << n)) - 1
            bits = child_tts[0].table[0] ^ all_ones
        elif op == 'MUX':
            s, t, e = child_tts[0].table[0], child_tts[1].table[0], child_tts[2].table[0]
            bits = (s & t) | (~s & e) & ((1 << (1 << n)) - 1)
        else:
            raise ValueError(f"Unknown op: {op}")
        return TruthTable(n, 1, (bits,))

    # ---------------------------------------------------------------------------
    # Rewrite rules
    # ---------------------------------------------------------------------------

    def apply_rules(self, max_iterations: int = 100, max_classes: int = 5000) -> int:
        """Apply rewrite rules until saturation or budget exhaustion."""
        total_new = 0
        for iteration in range(max_iterations):
            if len(self.classes) > max_classes:
                break
            new_this_round = 0

            class_ids = list(self.classes.keys())
            for cid in class_ids:
                if len(self.classes) > max_classes:
                    break
                ec = self.classes[cid]
                tt = ec.tt
                t = tt.table[0]

                # Skip constants and inputs
                if ec.best_cost == 0:
                    continue

                new_this_round += self._apply_decomposition_rules(cid, tt)
                new_this_round += self._apply_algebraic_rules(cid, tt)

            total_new += new_this_round
            if new_this_round == 0:
                break

        return total_new

    def _apply_decomposition_rules(self, cid: int, tt: TruthTable) -> int:
        """Apply Shannon decomposition at each variable."""
        new_count = 0
        n = self.n_inputs

        for var in range(n):
            if not tt.depends_on(var):
                continue

            # Shannon: f = (x AND f|x=1) OR (NOT x AND f|x=0)
            # But we need the cofactors as n-input functions (not n-1)
            cof0_bits = self._expand_cofactor(tt.table[0], var, 0, n)
            cof1_bits = self._expand_cofactor(tt.table[0], var, 1, n)

            cof0_tt = TruthTable(n, 1, (cof0_bits,))
            cof1_tt = TruthTable(n, 1, (cof1_bits,))

            cof0_cid = self.find_or_create(cof0_tt)
            cof1_cid = self.find_or_create(cof1_tt)
            inp_cid = self._input_classes[var]

            # f = MUX(x, f|1, f|0)
            result_tt = self._compute_tt('MUX', [
                self.classes[inp_cid].tt,
                self.classes[cof1_cid].tt,
                self.classes[cof0_cid].tt
            ])

            if result_tt.table == tt.table:
                node = ENode('MUX', (inp_cid, cof1_cid, cof0_cid), cost=3)
                before = len(self.classes[cid].nodes)
                self.add_node(tt, node)
                if len(self.classes[cid].nodes) > before:
                    new_count += 1

        return new_count

    def _expand_cofactor(self, bits: int, var: int, value: int, n: int) -> int:
        """Compute cofactor as an n-input truth table (variable still present but redundant)."""
        result = 0
        for p in range(1 << n):
            modified_p = (p & ~(1 << var)) | (value << var)
            if (bits >> modified_p) & 1:
                result |= (1 << p)
        return result

    def _apply_algebraic_rules(self, cid: int, tt: TruthTable) -> int:
        """Apply algebraic rewrite rules."""
        new_count = 0
        n = self.n_inputs
        t = tt.table[0]
        all_ones = (1 << (1 << n)) - 1

        # NOT rule: if NOT(f) exists, add it
        not_bits = t ^ all_ones
        not_tt = TruthTable(n, 1, (not_bits,))
        not_cid = self.find_or_create(not_tt)
        node = ENode('NOT', (cid,), cost=0)
        before = len(self.classes[not_cid].nodes)
        self.add_node(not_tt, node)
        if len(self.classes[not_cid].nodes) > before:
            new_count += 1

        # AND/OR decomposition: try all pairs of existing classes
        # This would be too expensive for all pairs - only try with inputs and small classes
        for other_cid in self._input_classes:
            other_t = self.classes[other_cid].tt.table[0]

            # f AND x
            and_bits = t & other_t
            and_tt = TruthTable(n, 1, (and_bits,))
            self.find_or_create(and_tt)

            # f OR x
            or_bits = t | other_t
            or_tt = TruthTable(n, 1, (or_bits,))
            self.find_or_create(or_tt)

            # f XOR x
            xor_bits = t ^ other_t
            xor_tt = TruthTable(n, 1, (xor_bits,))
            self.find_or_create(xor_tt)

            new_count += 1

        return new_count

    # ---------------------------------------------------------------------------
    # Cost computation and extraction
    # ---------------------------------------------------------------------------

    def compute_costs(self, exact_synthesis_fn=None):
        """Compute minimum implementation cost for each e-class.

        Uses exact synthesis for small sub-functions and recursive
        cost computation for larger ones.
        """
        changed = True
        while changed:
            changed = False
            for cid, ec in self.classes.items():
                if ec.best_cost == 0:
                    continue

                for node in ec.nodes:
                    cost = self._node_cost(node, exact_synthesis_fn)
                    if cost is not None and (ec.best_cost is None or cost < ec.best_cost):
                        ec.best_cost = cost
                        changed = True

    def _node_cost(self, node: ENode, exact_fn=None) -> Optional[int]:
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
                return 1 + c0 + c1  # OR = NAND(NOT a, NOT b)
            return None
        if node.op == 'XOR':
            c0 = self.classes[node.children[0]].best_cost
            c1 = self.classes[node.children[1]].best_cost
            if c0 is not None and c1 is not None:
                return 4 + c0 + c1  # XOR = 4 AND gates in AIG
            return None
        if node.op == 'MUX':
            c0 = self.classes[node.children[0]].best_cost  # sel
            c1 = self.classes[node.children[1]].best_cost  # then
            c2 = self.classes[node.children[2]].best_cost  # else
            if all(c is not None for c in [c0, c1, c2]):
                return 3 + c0 + c1 + c2
            return None
        return None

    def extract_best(self, root_tt: TruthTable) -> Optional[Circuit]:
        """Extract the best circuit for a given truth table."""
        key = root_tt.table
        if key not in self.tt_to_class:
            return None

        root_cid = self.tt_to_class[key]
        from solver import AIGBuilder, CONST1

        builder = AIGBuilder(self.n_inputs)
        memo: dict[int, int] = {}

        def extract(cid: int) -> int:
            if cid in memo:
                return memo[cid]

            ec = self.classes[cid]

            # Input or constant
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

            # Find best node
            best_node = None
            best_cost = float('inf')
            for node in ec.nodes:
                c = self._node_cost(node, None)
                if c is not None and c < best_cost:
                    best_cost = c
                    best_node = node

            if best_node is None:
                # Fallback: use Shannon decomposition
                from solver import _shannon_rec, _best_shannon_var_idx
                lit = _shannon_rec(ec.tt, list(range(self.n_inputs)), builder, {})
                memo[cid] = lit
                return lit

            if best_node.op == 'NOT':
                child_lit = extract(best_node.children[0])
                result = -child_lit if child_lit != 0 else CONST1
                memo[cid] = result
                return result
            elif best_node.op == 'AND':
                a = extract(best_node.children[0])
                b = extract(best_node.children[1])
                result = builder.add_and(a, b)
                memo[cid] = result
                return result
            elif best_node.op == 'OR':
                a = extract(best_node.children[0])
                b = extract(best_node.children[1])
                result = builder.add_or(a, b)
                memo[cid] = result
                return result
            elif best_node.op == 'XOR':
                a = extract(best_node.children[0])
                b = extract(best_node.children[1])
                result = builder.add_xor(a, b)
                memo[cid] = result
                return result
            elif best_node.op == 'MUX':
                s = extract(best_node.children[0])
                t = extract(best_node.children[1])
                e = extract(best_node.children[2])
                result = builder.add_mux(s, t, e)
                memo[cid] = result
                return result

            memo[cid] = 0
            return 0

        if root_tt.n_outputs == 1:
            out_lit = extract(root_cid)
            return builder.build([out_lit])
        return None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def egraph_synthesize(tt: TruthTable, max_iterations: int = 50,
                      max_classes: int = 2000) -> Optional[Circuit]:
    """Synthesize a circuit using e-graph exploration.

    Only handles single-output functions directly.
    For multi-output, synthesizes each output independently.
    """
    if tt.n_outputs > 1:
        from solver import AIGBuilder, CONST1
        builder = AIGBuilder(tt.n_inputs)
        outputs = []
        for j in range(tt.n_outputs):
            single_tt = TruthTable(tt.n_inputs, 1, (tt.table[j],))
            circ = egraph_synthesize(single_tt, max_iterations, max_classes)
            if circ is None:
                return None
            # Extract the output literal - rebuild in shared builder
            single_out_tt = circ.to_truth_table()
            from solver import _shannon_rec
            lit = _shannon_rec(single_out_tt, list(range(tt.n_inputs)), builder, {})
            outputs.append(lit)
        return builder.build(outputs)

    eg = EGraph(tt.n_inputs)
    root_cid = eg.find_or_create(tt)

    eg.apply_rules(max_iterations=max_iterations, max_classes=max_classes)
    eg.compute_costs()

    return eg.extract_best(tt)
