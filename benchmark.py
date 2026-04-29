"""Core data structures and evaluation infrastructure for global functional resynthesis."""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# TruthTable
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class TruthTable:
    """Boolean function represented as bitmask truth tables.

    For n_inputs inputs and n_outputs outputs:
    - table[j] is a Python int with 2^n_inputs bits
    - Bit i of table[j] = output j when the input pattern is i
    - Input pattern i: bit k of i = value of input variable k
    """
    n_inputs: int
    n_outputs: int
    table: tuple[int, ...]

    def __post_init__(self):
        assert len(self.table) == self.n_outputs
        mask = (1 << (1 << self.n_inputs)) - 1
        for t in self.table:
            assert 0 <= t <= mask

    def evaluate(self, pattern: int) -> int:
        result = 0
        for j in range(self.n_outputs):
            if (self.table[j] >> pattern) & 1:
                result |= (1 << j)
        return result

    def cofactor(self, var: int, value: int) -> TruthTable:
        n = self.n_inputs
        assert 0 <= var < n
        new_n = n - 1
        new_size = 1 << new_n
        new_table = []
        for t in self.table:
            bits = 0
            for i in range(new_size):
                lo = i & ((1 << var) - 1)
                hi = (i >> var) << (var + 1)
                orig_idx = hi | (value << var) | lo
                if (t >> orig_idx) & 1:
                    bits |= (1 << i)
            new_table.append(bits)
        return TruthTable(new_n, self.n_outputs, tuple(new_table))

    def negative_cofactor(self, var: int) -> TruthTable:
        return self.cofactor(var, 0)

    def positive_cofactor(self, var: int) -> TruthTable:
        return self.cofactor(var, 1)

    @staticmethod
    def from_function(n_inputs: int, func) -> TruthTable:
        size = 1 << n_inputs
        bits = 0
        for i in range(size):
            inputs = tuple((i >> k) & 1 for k in range(n_inputs))
            if func(*inputs):
                bits |= (1 << i)
        return TruthTable(n_inputs, 1, (bits,))

    @staticmethod
    def from_multi_output_function(n_inputs: int, n_outputs: int, func) -> TruthTable:
        size = 1 << n_inputs
        tables = [0] * n_outputs
        for i in range(size):
            inputs = tuple((i >> k) & 1 for k in range(n_inputs))
            outputs = func(*inputs)
            for j in range(n_outputs):
                if (outputs >> j) & 1 if isinstance(outputs, int) else outputs[j]:
                    tables[j] |= (1 << i)
        return TruthTable(n_inputs, n_outputs, tuple(tables))

    def is_constant(self, output_idx: int = 0) -> Optional[int]:
        t = self.table[output_idx]
        if t == 0:
            return 0
        if t == (1 << (1 << self.n_inputs)) - 1:
            return 1
        return None

    def depends_on(self, var: int, output_idx: int = 0) -> bool:
        t = self.table[output_idx]
        n = self.n_inputs
        step = 1 << var
        mask_lo = 0
        for block in range(1 << (n - var - 1)):
            base = block << (var + 1)
            for i in range(step):
                mask_lo |= (1 << (base + i))
        lo_bits = t & mask_lo
        hi_bits = (t >> step) & mask_lo
        return lo_bits != hi_bits

    def __eq__(self, other):
        if not isinstance(other, TruthTable):
            return NotImplemented
        return (self.n_inputs == other.n_inputs and
                self.n_outputs == other.n_outputs and
                self.table == other.table)

    def __hash__(self):
        return hash((self.n_inputs, self.n_outputs, self.table))


# ---------------------------------------------------------------------------
# Circuit (AIG)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AIGNode:
    """A node in an AND-Inverter Graph.

    Types:
        'CONST0': Constant false (id=0, fanins unused)
        'INPUT': Primary input (fanins unused)
        'AND': AND gate with two fanins (negative id = inverted)
    """
    id: int
    type: str
    fanin0: int = 0
    fanin1: int = 0


@dataclasses.dataclass
class Circuit:
    """AND-Inverter Graph circuit.

    Convention:
        - Node 0 is always CONST0
        - Input nodes have sequential positive IDs
        - AND nodes have IDs after inputs
        - Output list entries: positive = non-inverted, negative = inverted
        - Fanin entries: positive = non-inverted, negative = inverted
    """
    inputs: list[int]
    outputs: list[int]
    nodes: dict[int, AIGNode]
    _next_id: int = 1

    @staticmethod
    def new(n_inputs: int) -> Circuit:
        nodes = {0: AIGNode(0, 'CONST0')}
        inputs = []
        for i in range(n_inputs):
            nid = i + 1
            nodes[nid] = AIGNode(nid, 'INPUT')
            inputs.append(nid)
        return Circuit(inputs=inputs, outputs=[], nodes=nodes, _next_id=n_inputs + 1)

    def add_and(self, fanin0: int, fanin1: int) -> int:
        nid = self._next_id
        self._next_id += 1
        self.nodes[nid] = AIGNode(nid, 'AND', fanin0, fanin1)
        return nid

    def add_or(self, a: int, b: int) -> int:
        return -self.add_and(-a, -b)

    def add_xor(self, a: int, b: int) -> int:
        return self.add_or(self.add_and(a, -b), self.add_and(-a, b))

    def add_mux(self, sel: int, then_: int, else_: int) -> int:
        return self.add_or(self.add_and(sel, then_), self.add_and(-sel, else_))

    def set_outputs(self, outputs: list[int]):
        self.outputs = outputs

    def gate_count(self) -> int:
        return sum(1 for n in self.nodes.values() if n.type == 'AND')

    def depth(self) -> int:
        memo: dict[int, int] = {}

        def _depth(lit: int) -> int:
            nid = abs(lit)
            if nid in memo:
                return memo[nid]
            node = self.nodes[nid]
            if node.type in ('CONST0', 'INPUT'):
                memo[nid] = 0
                return 0
            d = 1 + max(_depth(node.fanin0), _depth(node.fanin1))
            memo[nid] = d
            return d

        if not self.outputs:
            return 0
        return max(_depth(o) for o in self.outputs)

    def simulate(self, pattern: int) -> int:
        values: dict[int, int] = {0: 0}
        for i, inp_id in enumerate(self.inputs):
            values[inp_id] = (pattern >> i) & 1

        def _eval(lit: int) -> int:
            nid = abs(lit)
            if nid in values:
                v = values[nid]
            else:
                node = self.nodes[nid]
                v = _eval(node.fanin0) & _eval(node.fanin1)
                values[nid] = v
            return v ^ (1 if lit < 0 else 0)

        result = 0
        for j, out in enumerate(self.outputs):
            if _eval(out):
                result |= (1 << j)
        return result

    def to_truth_table(self) -> TruthTable:
        n = len(self.inputs)
        n_out = len(self.outputs)
        tables = [0] * n_out
        for pattern in range(1 << n):
            out = self.simulate(pattern)
            for j in range(n_out):
                if (out >> j) & 1:
                    tables[j] |= (1 << pattern)
        return TruthTable(n, n_out, tuple(tables))

    def simulate_all_numpy(self) -> np.ndarray:
        n = len(self.inputs)
        n_patterns = 1 << n
        n_out = len(self.outputs)

        vals: dict[int, np.ndarray] = {}
        vals[0] = np.zeros(n_patterns, dtype=np.uint8)
        for i, inp_id in enumerate(self.inputs):
            vals[inp_id] = np.array(
                [((p >> i) & 1) for p in range(n_patterns)], dtype=np.uint8
            )

        sorted_nodes = sorted(
            (n for n in self.nodes.values() if n.type == 'AND'),
            key=lambda n: n.id
        )

        def get_val(lit: int) -> np.ndarray:
            nid = abs(lit)
            v = vals[nid]
            if lit < 0:
                return 1 - v
            return v

        for node in sorted_nodes:
            vals[node.id] = get_val(node.fanin0) & get_val(node.fanin1)

        result = np.zeros((n_out, n_patterns), dtype=np.uint8)
        for j, out in enumerate(self.outputs):
            result[j] = get_val(out)
        return result

    def copy(self) -> Circuit:
        new_nodes = {nid: AIGNode(n.id, n.type, n.fanin0, n.fanin1)
                     for nid, n in self.nodes.items()}
        return Circuit(
            inputs=list(self.inputs),
            outputs=list(self.outputs),
            nodes=new_nodes,
            _next_id=self._next_id
        )

    def to_dict(self) -> dict:
        return {
            'inputs': self.inputs,
            'outputs': self.outputs,
            'nodes': [
                {'id': n.id, 'type': n.type, 'fanin0': n.fanin0, 'fanin1': n.fanin1}
                for n in sorted(self.nodes.values(), key=lambda n: n.id)
            ],
            '_next_id': self._next_id,
        }

    @staticmethod
    def from_dict(d: dict) -> Circuit:
        nodes = {}
        for nd in d['nodes']:
            nodes[nd['id']] = AIGNode(nd['id'], nd['type'], nd['fanin0'], nd['fanin1'])
        return Circuit(
            inputs=d['inputs'],
            outputs=d['outputs'],
            nodes=nodes,
            _next_id=d['_next_id'],
        )


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Benchmark:
    name: str
    truth_table: TruthTable
    baseline_circuit: Circuit
    optimal_gate_count: Optional[int] = None
    optimal_depth: Optional[int] = None
    category: str = 'misc'
    tier: int = 1

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'n_inputs': self.truth_table.n_inputs,
            'n_outputs': self.truth_table.n_outputs,
            'truth_table': [hex(t) for t in self.truth_table.table],
            'baseline_circuit': self.baseline_circuit.to_dict(),
            'baseline_gates': self.baseline_circuit.gate_count(),
            'baseline_depth': self.baseline_circuit.depth(),
            'optimal_gate_count': self.optimal_gate_count,
            'optimal_depth': self.optimal_depth,
            'category': self.category,
            'tier': self.tier,
        }

    @staticmethod
    def from_dict(d: dict) -> Benchmark:
        tt = TruthTable(
            d['n_inputs'], d['n_outputs'],
            tuple(int(t, 16) if t.startswith('0x') else int(t) for t in d['truth_table'])
        )
        circ = Circuit.from_dict(d['baseline_circuit'])
        return Benchmark(
            name=d['name'],
            truth_table=tt,
            baseline_circuit=circ,
            optimal_gate_count=d.get('optimal_gate_count'),
            optimal_depth=d.get('optimal_depth'),
            category=d.get('category', 'misc'),
            tier=d.get('tier', 1),
        )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def verify_equivalence(circuit: Circuit, tt: TruthTable) -> bool:
    n = tt.n_inputs
    if n <= 20:
        sim = circuit.simulate_all_numpy()
        for j in range(tt.n_outputs):
            expected = np.array(
                [((tt.table[j] >> p) & 1) for p in range(1 << n)],
                dtype=np.uint8
            )
            if not np.array_equal(sim[j], expected):
                return False
        return True
    else:
        for _ in range(100000):
            import random
            pattern = random.getrandbits(n)
            if circuit.simulate(pattern) != tt.evaluate(pattern):
                return False
        return True


def evaluate(circuit: Circuit, benchmark: Benchmark) -> dict:
    correct = verify_equivalence(circuit, benchmark.truth_table)
    gc = circuit.gate_count()
    dep = circuit.depth()
    baseline_gc = benchmark.baseline_circuit.gate_count()
    opt_gc = benchmark.optimal_gate_count

    return {
        'name': benchmark.name,
        'correct': correct,
        'gate_count': gc,
        'depth': dep,
        'baseline_gates': baseline_gc,
        'optimal_gates': opt_gc,
        'reduction_ratio': gc / baseline_gc if baseline_gc > 0 else float('inf'),
        'optimality_gap': (gc / opt_gc - 1.0) if opt_gc and opt_gc > 0 else None,
        'n_inputs': benchmark.truth_table.n_inputs,
        'n_outputs': benchmark.truth_table.n_outputs,
        'tier': benchmark.tier,
        'category': benchmark.category,
    }


def load_benchmarks(path: str = 'benchmarks.json') -> list[Benchmark]:
    with open(path) as f:
        data = json.load(f)
    return [Benchmark.from_dict(d) for d in data]


def save_benchmarks(benchmarks: list[Benchmark], path: str = 'benchmarks.json'):
    data = [b.to_dict() for b in benchmarks]
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def run_evaluation(solver_func, benchmarks: list[Benchmark]) -> list[dict]:
    results = []
    for bm in benchmarks:
        t0 = time.time()
        circuit = solver_func(bm.truth_table)
        elapsed = time.time() - t0
        r = evaluate(circuit, bm)
        r['time_s'] = round(elapsed, 3)
        results.append(r)
    return results


def print_results(results: list[dict]):
    print(f"{'Benchmark':<20} {'In':>3} {'Out':>3} {'Base':>5} {'Ours':>5} "
          f"{'Ratio':>6} {'Opt':>5} {'Gap':>7} {'OK':>3} {'Time':>6}")
    print("-" * 80)

    for r in results:
        opt_str = str(r['optimal_gates']) if r['optimal_gates'] is not None else '-'
        gap_str = f"{r['optimality_gap']:.1%}" if r['optimality_gap'] is not None else '-'
        ok_str = 'Y' if r['correct'] else 'N'
        print(f"{r['name']:<20} {r['n_inputs']:>3} {r['n_outputs']:>3} "
              f"{r['baseline_gates']:>5} {r['gate_count']:>5} "
              f"{r['reduction_ratio']:>6.3f} {opt_str:>5} {gap_str:>7} "
              f"{ok_str:>3} {r['time_s']:>5.1f}s")

    correct_all = all(r['correct'] for r in results)
    avg_ratio = sum(r['reduction_ratio'] for r in results) / len(results)

    tier_ratios = {}
    for r in results:
        tier_ratios.setdefault(r['tier'], []).append(r['reduction_ratio'])

    print("-" * 80)
    print(f"Average reduction ratio: {avg_ratio:.3f}")
    for tier in sorted(tier_ratios):
        avg = sum(tier_ratios[tier]) / len(tier_ratios[tier])
        print(f"  Tier {tier}: {avg:.3f} ({len(tier_ratios[tier])} benchmarks)")
    print(f"All correct: {correct_all}")
