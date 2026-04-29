"""Mixed-gate-aware cut-based rewriting engine.

Optimizes circuits in the {AND2, OR2, XOR2, NOT1} gate library where each gate
costs 1. Unlike ABC which converts everything to AIG (where XOR costs 4 AND gates),
this engine keeps XOR as a first-class primitive.

Key idea: enumerate k-input cuts for each gate, compute the local truth table,
and try to resynthesize the function with fewer mixed gates.
"""

from __future__ import annotations

import itertools
from typing import Optional

from theories.blif_io import read_blif, blif_to_truth_table


# ---------------------------------------------------------------------------
# Circuit representation for mixed gates
# ---------------------------------------------------------------------------

class MixedCircuit:
    """A circuit using {AND, OR, XOR, NOT} gates, each costing 1."""

    def __init__(self, inputs: list[str], outputs: list[str]):
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        # signal -> (gate_type, [input_signals])
        self.gates: dict[str, tuple[str, list[str]]] = {}
        self._next_id = 0

    def copy(self) -> MixedCircuit:
        c = MixedCircuit(self.inputs, self.outputs)
        c.gates = {k: (gt, list(ins)) for k, (gt, ins) in self.gates.items()}
        c._next_id = self._next_id
        return c

    def new_signal(self, prefix='r') -> str:
        self._next_id += 1
        return f'{prefix}_{self._next_id}'

    def gate_count(self) -> int:
        return len(self.gates)

    def add_gate(self, gate_type: str, inputs: list[str], name: str = None) -> str:
        if name is None:
            name = self.new_signal()
        self.gates[name] = (gate_type, inputs)
        return name

    def topological_order(self) -> list[str]:
        """Return gates in topological order."""
        evaluated = set(self.inputs)
        topo = []
        queue = list(self.gates.keys())
        max_iter = len(queue) * 3
        iteration = 0
        while queue and iteration < max_iter:
            iteration += 1
            next_queue = []
            for sig in queue:
                if sig in evaluated:
                    continue
                gt, ins = self.gates[sig]
                if all(inp in evaluated for inp in ins):
                    topo.append(sig)
                    evaluated.add(sig)
                else:
                    next_queue.append(sig)
            queue = next_queue
        return topo

    def compute_truth_tables(self, n_inputs: int) -> dict[str, int]:
        """Compute truth tables for all signals (as bitmasks over 2^n_inputs patterns)."""
        tt = {}
        for i, inp in enumerate(self.inputs):
            # Input i: bit pattern where bit p is set if (p >> i) & 1
            mask = 0
            for p in range(1 << n_inputs):
                if (p >> i) & 1:
                    mask |= (1 << p)
            tt[inp] = mask

        full_mask = (1 << (1 << n_inputs)) - 1

        for sig in self.topological_order():
            gt, ins = self.gates[sig]
            if gt == 'AND':
                tt[sig] = tt[ins[0]] & tt[ins[1]]
            elif gt == 'OR':
                tt[sig] = tt[ins[0]] | tt[ins[1]]
            elif gt == 'XOR':
                tt[sig] = tt[ins[0]] ^ tt[ins[1]]
            elif gt == 'NOT':
                tt[sig] = tt[ins[0]] ^ full_mask
            elif gt == 'BUF':
                tt[sig] = tt[ins[0]]
            elif gt == 'NAND':
                tt[sig] = (tt[ins[0]] & tt[ins[1]]) ^ full_mask
            elif gt == 'NOR':
                tt[sig] = (tt[ins[0]] | tt[ins[1]]) ^ full_mask
            elif gt == 'XNOR':
                tt[sig] = (tt[ins[0]] ^ tt[ins[1]]) ^ full_mask
            else:
                raise ValueError(f"Unknown gate type: {gt}")

        return tt

    def fanout_count(self) -> dict[str, int]:
        """Count how many gates each signal fans out to."""
        fo = {inp: 0 for inp in self.inputs}
        for sig in self.gates:
            fo[sig] = 0
        for sig, (gt, ins) in self.gates.items():
            for inp in ins:
                fo[inp] = fo.get(inp, 0) + 1
        for out in self.outputs:
            fo[out] = fo.get(out, 0) + 1
        return fo

    def simulate_pattern(self, sig_vals: dict[str, int]) -> dict[str, int]:
        """Evaluate all gates given input signal values."""
        vals = dict(sig_vals)
        for sig in self.topological_order():
            gt, ins = self.gates[sig]
            iv = [vals[i] for i in ins]
            if gt == 'AND':
                vals[sig] = iv[0] & iv[1]
            elif gt == 'OR':
                vals[sig] = iv[0] | iv[1]
            elif gt == 'XOR':
                vals[sig] = iv[0] ^ iv[1]
            elif gt == 'NOT':
                vals[sig] = 1 - iv[0]
            elif gt == 'BUF':
                vals[sig] = iv[0]
            elif gt == 'NAND':
                vals[sig] = 1 - (iv[0] & iv[1])
            elif gt == 'NOR':
                vals[sig] = 1 - (iv[0] | iv[1])
            elif gt == 'XNOR':
                vals[sig] = 1 - (iv[0] ^ iv[1])
        return vals

    def verify(self, reference_tt) -> bool:
        """Verify the circuit matches a reference truth table."""
        n = len(self.inputs)
        tts = self.compute_truth_tables(n)
        for j, out in enumerate(self.outputs):
            if tts.get(out, 0) != reference_tt.table[j]:
                return False
        return True

    def remove_dead_gates(self):
        """Remove gates not reachable from outputs."""
        needed = set()
        stack = list(self.outputs)
        while stack:
            sig = stack.pop()
            if sig in needed or sig in self.inputs:
                continue
            needed.add(sig)
            if sig in self.gates:
                gt, ins = self.gates[sig]
                stack.extend(ins)

        dead = [s for s in self.gates if s not in needed]
        for s in dead:
            del self.gates[s]

    def write_blif(self, filename: str, model_name: str = 'optimized'):
        """Write the circuit to a BLIF file."""
        with open(filename, 'w') as f:
            f.write(f'.model {model_name}\n')
            f.write('.inputs ' + ' '.join(self.inputs) + '\n')
            f.write('.outputs ' + ' '.join(self.outputs) + '\n')
            for sig in self.topological_order():
                gt, ins = self.gates[sig]
                if gt == 'AND':
                    f.write(f'.gate AND2 A={ins[0]} B={ins[1]} O={sig}\n')
                elif gt == 'OR':
                    f.write(f'.gate OR2 A={ins[0]} B={ins[1]} O={sig}\n')
                elif gt == 'XOR':
                    f.write(f'.gate XOR2 A={ins[0]} B={ins[1]} O={sig}\n')
                elif gt == 'NOT':
                    f.write(f'.gate NOT1 A={ins[0]} O={sig}\n')
            f.write('.end\n')


def load_mixed_circuit(blif_path: str) -> MixedCircuit:
    """Load a BLIF file into a MixedCircuit."""
    inputs, outputs, gates = read_blif(blif_path)
    circ = MixedCircuit(inputs, outputs)
    circ.gates = {k: (gt, list(ins)) for k, (gt, ins) in gates.items()}
    # Set _next_id higher than any existing signal
    max_id = 0
    for sig in gates:
        parts = sig.split('_')
        if len(parts) == 2 and parts[1].isdigit():
            max_id = max(max_id, int(parts[1]))
    circ._next_id = max_id + 100
    return circ


# ---------------------------------------------------------------------------
# Optimal synthesis for small functions (using {AND, OR, XOR, NOT})
# ---------------------------------------------------------------------------

def _make_input_tts(k: int) -> list[int]:
    """Create truth tables for k input variables."""
    tts = []
    for i in range(k):
        mask = 0
        for p in range(1 << k):
            if (p >> i) & 1:
                mask |= (1 << p)
        tts.append(mask)
    return tts


def _is_trivial(target_tt: int, k: int, input_tts: list[int]) -> Optional[tuple]:
    """Check if the function is a constant, variable, or negated variable."""
    full = (1 << (1 << k)) - 1
    if target_tt == 0:
        return ('CONST0',)
    if target_tt == full:
        return ('CONST1',)
    for i, itt in enumerate(input_tts):
        if target_tt == itt:
            return ('VAR', i)
        if target_tt == (itt ^ full):
            return ('NVAR', i)
    return None


def synthesize_optimal(target_tt: int, k: int, max_gates: int = None) -> Optional[list]:
    """Find an optimal circuit for a k-input single-output function.

    Uses BFS over all possible circuits. Returns a list of
    (gate_type, (operand_a, operand_b)) where operands are indices:
        0..k-1 = input variables
        k, k+1, ... = gate outputs (in order)
    Negative index = negated version.
    Returns None if no circuit found within max_gates.
    """
    full = (1 << (1 << k)) - 1
    input_tts = _make_input_tts(k)

    triv = _is_trivial(target_tt, k, input_tts)
    if triv is not None:
        return []  # 0 gates needed

    if max_gates is None:
        max_gates = 10  # Safety limit

    # BFS: each state is a tuple of truth tables available
    # Start with input truth tables
    base = tuple(input_tts)
    # Also include negated inputs as "free" (NOT costs 1 gate though)
    # Actually NOT costs 1 gate, so we need to track gate count

    # Use iterative deepening
    for num_gates in range(1, max_gates + 1):
        result = _synth_exact(target_tt, k, input_tts, full, num_gates)
        if result is not None:
            return result

    return None


def _synth_exact(target_tt: int, k: int, input_tts: list[int], full: int,
                 num_gates: int) -> Optional[list]:
    """Try to synthesize target with exactly num_gates gates.

    Uses DFS with pruning. Returns list of (gate_type, operands) or None.
    """
    # Available signals: inputs (0..k-1) plus gate outputs (k, k+1, ...)
    available_tts = list(input_tts)  # truth tables of available signals
    gates = []

    def solve(depth):
        if depth == 0:
            # Check if target is among available signals
            for i, tt in enumerate(available_tts):
                if tt == target_tt:
                    return True
                if (tt ^ full) == target_tt:
                    # Need a NOT gate but we're out of gates
                    return False
            return False

        n = len(available_tts)

        # Try NOT gate
        for i in range(n):
            new_tt = available_tts[i] ^ full
            if new_tt in available_tts:
                continue  # Already exists
            available_tts.append(new_tt)
            gates.append(('NOT', (i,)))
            if new_tt == target_tt and depth == 1:
                return True
            if depth > 1:
                if solve(depth - 1):
                    return True
            gates.pop()
            available_tts.pop()

        # Try binary gates
        for i in range(n):
            for j in range(i, n):
                for op in ['AND', 'OR', 'XOR']:
                    a, b = available_tts[i], available_tts[j]
                    if op == 'AND':
                        new_tt = a & b
                    elif op == 'OR':
                        new_tt = a | b
                    elif op == 'XOR':
                        new_tt = a ^ b

                    if new_tt == 0 or new_tt == full:
                        continue  # Trivial
                    if new_tt in available_tts:
                        continue  # Already exists

                    available_tts.append(new_tt)
                    gates.append((op, (i, j)))

                    if new_tt == target_tt and depth == 1:
                        return True
                    if depth > 1:
                        if solve(depth - 1):
                            return True

                    gates.pop()
                    available_tts.pop()

        return False

    if solve(num_gates):
        return list(gates)
    return None


# Precomputed database for common functions
_NPN_CACHE = {}


def _popcount(x: int) -> int:
    c = 0
    while x:
        c += 1
        x &= x - 1
    return c


def _compute_npn_class(tt: int, k: int) -> int:
    """Compute a canonical NPN representative for a truth table.
    Returns the minimum over all input permutations + complement.
    """
    full = (1 << (1 << k)) - 1
    perms = list(itertools.permutations(range(k)))
    best = tt
    for perm in perms:
        # Permute inputs
        new_tt = 0
        for p in range(1 << k):
            if (tt >> p) & 1:
                new_p = 0
                for i in range(k):
                    if (p >> perm[i]) & 1:
                        new_p |= (1 << i)
                new_tt |= (1 << new_p)
        best = min(best, new_tt, new_tt ^ full)
    return best


# ---------------------------------------------------------------------------
# Cut enumeration
# ---------------------------------------------------------------------------

def enumerate_cuts(circ: MixedCircuit, max_cut_size: int = 6) -> dict[str, list[frozenset]]:
    """For each gate, enumerate k-input cuts (k <= max_cut_size).

    A cut of signal s is a set of signals C such that every path from
    primary inputs to s passes through some signal in C.
    The trivial cut is {s} itself.
    """
    cuts: dict[str, list[frozenset]] = {}

    # Primary inputs have trivial cuts
    for inp in circ.inputs:
        cuts[inp] = [frozenset([inp])]

    for sig in circ.topological_order():
        gt, ins = circ.gates[sig]
        own_cuts = [frozenset([sig])]  # trivial cut

        if gt == 'NOT' or gt == 'BUF':
            # Unary: cuts of input
            for c in cuts.get(ins[0], [frozenset([ins[0]])]):
                if len(c) <= max_cut_size and c not in own_cuts:
                    own_cuts.append(c)
        else:
            # Binary: cross product of cuts of two inputs
            cuts_a = cuts.get(ins[0], [frozenset([ins[0]])])
            cuts_b = cuts.get(ins[1], [frozenset([ins[1]])])
            for ca in cuts_a:
                for cb in cuts_b:
                    merged = ca | cb
                    if len(merged) <= max_cut_size and merged not in own_cuts:
                        own_cuts.append(merged)

        # Limit number of cuts per node to avoid explosion
        # Prioritize smaller cuts
        own_cuts.sort(key=len)
        cuts[sig] = own_cuts[:50]

    return cuts


# ---------------------------------------------------------------------------
# Local truth table computation for cuts
# ---------------------------------------------------------------------------

def compute_cut_truth_table(circ: MixedCircuit, root: str, cut: frozenset,
                            global_tts: dict[str, int], n_global: int) -> tuple[int, int]:
    """Compute the truth table of `root` relative to the cut inputs.

    Returns (local_tt, k) where local_tt is the truth table of the function
    from cut inputs to root output, and k is the number of cut inputs.
    """
    cut_inputs = sorted(cut)  # deterministic ordering
    k = len(cut_inputs)

    if k > 8:
        return None, k

    # Build local truth table by simulation
    # For each of the 2^k patterns of cut inputs, determine root's value
    # We use the global truth tables to project

    # Each global truth table has 2^n_global bits
    # We need to find which patterns of the n_global inputs correspond to
    # each combination of cut input values

    local_tt = 0
    n = n_global
    full = (1 << (1 << n)) - 1

    # Get global TTs for cut inputs and root
    cut_global_tts = [global_tts[ci] for ci in cut_inputs]
    root_global_tt = global_tts[root]

    for local_pattern in range(1 << k):
        # Find global patterns where each cut input matches local_pattern
        match_mask = full
        for i in range(k):
            if (local_pattern >> i) & 1:
                match_mask &= cut_global_tts[i]
            else:
                match_mask &= cut_global_tts[i] ^ full

        # If root is 1 for ANY of these global patterns, set local_tt bit
        if match_mask == 0:
            continue  # This cut input combination is impossible (don't care)
        if root_global_tt & match_mask:
            # Check if root is consistently 0 or 1 for these patterns
            if (root_global_tt & match_mask) == match_mask:
                local_tt |= (1 << local_pattern)
            else:
                # Root is not a pure function of cut inputs alone!
                # This shouldn't happen for valid cuts
                # Set the bit if majority is 1
                ones = _popcount(root_global_tt & match_mask)
                zeros = _popcount((root_global_tt ^ full) & match_mask)
                # Actually this means the cut is invalid
                return None, k

    return local_tt, k


# ---------------------------------------------------------------------------
# Count gates in a sub-circuit (between cut and root)
# ---------------------------------------------------------------------------

def count_subcirc_gates(circ: MixedCircuit, root: str, cut: frozenset) -> int:
    """Count the number of gates in the sub-circuit from cut to root."""
    if root in cut:
        return 0
    count = 0
    visited = set()
    stack = [root]
    while stack:
        sig = stack.pop()
        if sig in visited or sig in cut or sig in circ.inputs:
            continue
        visited.add(sig)
        if sig in circ.gates:
            count += 1
            gt, ins = circ.gates[sig]
            stack.extend(ins)
    return count


def get_subcirc_signals(circ: MixedCircuit, root: str, cut: frozenset) -> set:
    """Get all gate signals in the sub-circuit from cut to root."""
    if root in cut:
        return set()
    signals = set()
    visited = set()
    stack = [root]
    while stack:
        sig = stack.pop()
        if sig in visited or sig in cut or sig in circ.inputs:
            continue
        visited.add(sig)
        if sig in circ.gates:
            signals.add(sig)
            gt, ins = circ.gates[sig]
            stack.extend(ins)
    return signals


def subcirc_is_replaceable(circ: MixedCircuit, root: str, cut: frozenset,
                            fanout: dict[str, int]) -> bool:
    """Check if we can replace the subcircuit without affecting other signals.

    The subcircuit is replaceable if no internal signal (other than root)
    has fanout outside the subcircuit.
    """
    interior = get_subcirc_signals(circ, root, cut)
    if not interior:
        return False

    # Check that no interior signal (except root) has external fanout
    for sig in interior:
        if sig == root:
            continue
        # Check if sig is used by gates outside the subcircuit
        external_fanout = 0
        for other_sig, (gt, ins) in circ.gates.items():
            if other_sig in interior:
                continue
            if sig in ins:
                external_fanout += 1
        if sig in circ.outputs:
            external_fanout += 1
        if external_fanout > 0:
            return False

    return True


# ---------------------------------------------------------------------------
# Build replacement subcircuit
# ---------------------------------------------------------------------------

def build_replacement(circ: MixedCircuit, root: str, cut: frozenset,
                      synthesis_result: list, cut_inputs: list[str]) -> dict:
    """Build replacement gates from synthesis result.

    Returns dict of new_signal -> (gate_type, [input_signals]).
    """
    k = len(cut_inputs)
    # Map synthesis operand indices to signal names
    signal_map = {}
    for i, ci in enumerate(cut_inputs):
        signal_map[i] = ci

    new_gates = {}
    for idx, (gate_type, operands) in enumerate(synthesis_result):
        new_sig = circ.new_signal('opt')
        gate_idx = k + idx
        signal_map[gate_idx] = new_sig

        if gate_type == 'NOT':
            op_a = operands[0]
            new_gates[new_sig] = ('NOT', [signal_map[op_a]])
        else:
            op_a, op_b = operands
            new_gates[new_sig] = (gate_type, [signal_map[op_a], signal_map[op_b]])

    return new_gates, signal_map[k + len(synthesis_result) - 1] if synthesis_result else root


# ---------------------------------------------------------------------------
# XOR-aware decomposition for larger functions
# ---------------------------------------------------------------------------

def xor_decompose(target_tt: int, k: int, input_tts: list[int],
                  max_gates: int) -> Optional[list]:
    """Try XOR-aware Shannon/Davio decomposition.

    For each variable x_i, try:
    1. Shannon: f = x_i * f1 + x_i' * f0  (costs: 2 AND + 1 OR + 1 NOT + recursive)
    2. Positive Davio: f = f0 ^ (x_i * (f0 ^ f1))  (costs: 1 XOR + 1 AND + recursive for f0^f1)
    3. Negative Davio: f = f1 ^ (x_i' * (f0 ^ f1))  (costs: 1 XOR + 1 AND + 1 NOT + recursive)

    With XOR costing 1, Davio is often cheaper.
    """
    full = (1 << (1 << k)) - 1

    triv = _is_trivial(target_tt, k, input_tts)
    if triv is not None:
        return []

    if max_gates <= 0:
        return None

    best = None

    for var in range(k):
        # Compute cofactors
        step = 1 << var
        f0 = 0  # cofactor when var=0
        f1 = 0  # cofactor when var=1

        for p in range(1 << k):
            if (target_tt >> p) & 1:
                if (p >> var) & 1:
                    # var=1, map to pattern without var
                    lo = p & ((1 << var) - 1)
                    hi = (p >> (var + 1)) << var
                    f1 |= (1 << (hi | lo))
                else:
                    lo = p & ((1 << var) - 1)
                    hi = (p >> (var + 1)) << var
                    f0 |= (1 << (hi | lo))

        # Reduced truth tables are (k-1) inputs
        new_k = k - 1
        new_full = (1 << (1 << new_k)) - 1

        # Create new input TTs for k-1 variables
        new_input_tts = []
        for i in range(k):
            if i == var:
                continue
            new_input_tts.append(_make_input_tts(new_k)[len(new_input_tts)])

        f_xor = f0 ^ f1

        # Positive Davio: f = f0 XOR (x_var AND (f0 XOR f1))
        # Cost: synth(f0) + synth(f_xor) + 1(AND) + 1(XOR) = synth(f0)+synth(f_xor)+2
        # But f0 and f_xor may share structure...

        # For now, just try if f0 or f1 is trivial
        triv_f0 = _is_trivial(f0, new_k, new_input_tts)
        triv_f1 = _is_trivial(f1, new_k, new_input_tts)
        triv_fxor = _is_trivial(f_xor, new_k, new_input_tts)

        # If f_xor is trivial (f0 == f1), function doesn't depend on var
        if f0 == f1:
            # f = f0 (doesn't depend on var)
            result = xor_decompose(f0, new_k, new_input_tts, max_gates)
            if result is not None:
                if best is None or len(result) < len(best):
                    best = result  # Need to remap variables
            continue

    return best


# ---------------------------------------------------------------------------
# Heuristic multi-output rewriting
# ---------------------------------------------------------------------------

def find_shared_xor_terms(circ: MixedCircuit, tts: dict[str, int], n: int) -> list:
    """Find XOR combinations of existing signals that equal other needed signals."""
    full = (1 << (1 << n)) - 1
    improvements = []

    # Collect all signal truth tables
    sig_tts = {}
    for sig in list(circ.inputs) + list(circ.gates.keys()):
        if sig in tts:
            sig_tts[sig] = tts[sig]

    # For each pair of existing signals, check if their XOR/AND/OR
    # equals another signal that currently takes more gates to compute
    existing_sigs = list(sig_tts.keys())

    for i in range(len(existing_sigs)):
        for j in range(i + 1, len(existing_sigs)):
            a, b = existing_sigs[i], existing_sigs[j]
            ta, tb = sig_tts[a], sig_tts[b]

            for op, func in [('XOR', lambda x, y: x ^ y),
                             ('AND', lambda x, y: x & y),
                             ('OR', lambda x, y: x | y)]:
                result_tt = func(ta, tb)
                # Check if result equals any gate output
                for sig, tt in sig_tts.items():
                    if tt == result_tt and sig != a and sig != b:
                        if sig in circ.gates:
                            improvements.append((sig, op, a, b))

                # Also check NOT of result
                not_result = result_tt ^ full
                for sig, tt in sig_tts.items():
                    if tt == not_result and sig != a and sig != b:
                        if sig in circ.gates:
                            improvements.append((sig, f'NOT_{op}', a, b))

    return improvements


# ---------------------------------------------------------------------------
# Window-based optimization
# ---------------------------------------------------------------------------

def optimize_window(circ: MixedCircuit, window_roots: list[str],
                    window_inputs: frozenset, global_tts: dict[str, int],
                    n: int, max_synth_gates: int = 8) -> Optional[dict]:
    """Try to resynthesize a multi-output window with fewer gates.

    window_roots: output signals of the window
    window_inputs: input signals of the window (cut)
    """
    cut_inputs = sorted(window_inputs)
    k = len(cut_inputs)

    if k > 6:
        return None

    # Compute local truth tables for each root
    full_n = (1 << (1 << n)) - 1
    cut_global_tts = [global_tts[ci] for ci in cut_inputs]

    root_local_tts = []
    for root in window_roots:
        local_tt = 0
        root_tt = global_tts[root]
        valid = True
        for local_pattern in range(1 << k):
            match_mask = (1 << (1 << n)) - 1
            for i in range(k):
                if (local_pattern >> i) & 1:
                    match_mask &= cut_global_tts[i]
                else:
                    match_mask &= cut_global_tts[i] ^ full_n

            if match_mask == 0:
                continue
            root_val = root_tt & match_mask
            if root_val == match_mask:
                local_tt |= (1 << local_pattern)
            elif root_val != 0:
                valid = False
                break

        if not valid:
            return None
        root_local_tts.append(local_tt)

    # Try multi-output synthesis
    return _multi_output_synth(root_local_tts, k, max_synth_gates)


def _multi_output_synth(target_tts: list[int], k: int,
                        max_gates: int) -> Optional[list]:
    """Synthesize a multi-output function with shared gates.

    Returns list of (gate_type, operands, [output_indices_this_realizes]) or None.
    """
    full = (1 << (1 << k)) - 1
    input_tts = _make_input_tts(k)

    # Check which outputs are trivial
    trivial_map = {}
    non_trivial = []
    for idx, tt in enumerate(target_tts):
        triv = _is_trivial(tt, k, input_tts)
        if triv is not None:
            trivial_map[idx] = triv
        else:
            non_trivial.append((idx, tt))

    if not non_trivial:
        return []

    # For each non-trivial output, try independent synthesis
    total_gates = 0
    results = []
    for idx, tt in non_trivial:
        result = synthesize_optimal(tt, k, max_gates=max_gates - total_gates)
        if result is None:
            return None
        total_gates += len(result)
        results.append((idx, result))
        if total_gates > max_gates:
            return None

    return results


# ---------------------------------------------------------------------------
# Main rewriting loop
# ---------------------------------------------------------------------------

def rewrite_mixed_circuit(blif_path: str, verbose: bool = True) -> MixedCircuit:
    """Main entry point: load a BLIF and try to reduce gate count.

    Returns the optimized MixedCircuit.
    """
    # Load circuit
    circ = load_mixed_circuit(blif_path)
    ref_tt = blif_to_truth_table(blif_path)
    n = len(circ.inputs)

    initial_gates = circ.gate_count()
    if verbose:
        print(f"Loaded circuit: {initial_gates} gates, {n} inputs, "
              f"{len(circ.outputs)} outputs")
        gt_counts = {}
        for sig, (gt, ins) in circ.gates.items():
            gt_counts[gt] = gt_counts.get(gt, 0) + 1
        print(f"Gate types: {gt_counts}")

    # Verify initial circuit
    assert circ.verify(ref_tt), "Initial circuit doesn't match truth table!"

    best_circ = circ.copy()
    best_count = initial_gates

    # Strategy 1: algebraic identities
    if verbose:
        print("\n--- Strategy 1: Algebraic simplification ---")
    improved = True
    while improved:
        improved = False
        result = _algebraic_simplify(best_circ, ref_tt, n, verbose)
        if result is not None and result.gate_count() < best_count:
            best_circ = result
            best_count = result.gate_count()
            improved = True
            if verbose:
                print(f"  After algebraic: {best_count} gates")

    # Strategy 2: Signal equivalence / replacement
    if verbose:
        print("\n--- Strategy 2: Signal equivalence ---")
    result = _signal_equivalence(best_circ, ref_tt, n, verbose)
    if result is not None and result.gate_count() < best_count:
        best_circ = result
        best_count = result.gate_count()
        if verbose:
            print(f"  After signal equivalence: {best_count} gates")

    # Strategy 3: Cut-based rewriting with exact synthesis
    if verbose:
        print("\n--- Strategy 3: Cut-based rewriting ---")
    for max_cut in [3, 4, 5, 6]:
        improved = True
        while improved:
            improved = False
            result = _cut_rewrite_pass(best_circ, ref_tt, n, max_cut, verbose)
            if result is not None and result.gate_count() < best_count:
                best_circ = result
                best_count = result.gate_count()
                improved = True
                if verbose:
                    print(f"  After cut-{max_cut} rewrite: {best_count} gates")

    # Strategy 4: Window-based multi-output optimization
    if verbose:
        print("\n--- Strategy 4: Window-based optimization ---")
    improved = True
    while improved:
        improved = False
        result = _window_rewrite(best_circ, ref_tt, n, verbose)
        if result is not None and result.gate_count() < best_count:
            best_circ = result
            best_count = result.gate_count()
            improved = True
            if verbose:
                print(f"  After window rewrite: {best_count} gates")

    # Strategy 5: Global re-decomposition
    if verbose:
        print("\n--- Strategy 5: Global re-decomposition ---")
    result = _global_redecompose(best_circ, ref_tt, n, verbose)
    if result is not None and result.gate_count() < best_count:
        best_circ = result
        best_count = result.gate_count()
        if verbose:
            print(f"  After global re-decomposition: {best_count} gates")

    # Strategy 6: Exhaustive pair/triple merge
    if verbose:
        print("\n--- Strategy 6: Exhaustive merge ---")
    improved = True
    while improved:
        improved = False
        result = _exhaustive_merge(best_circ, ref_tt, n, verbose)
        if result is not None and result.gate_count() < best_count:
            best_circ = result
            best_count = result.gate_count()
            improved = True
            if verbose:
                print(f"  After exhaustive merge: {best_count} gates")

    # Final verification
    assert best_circ.verify(ref_tt), "Final circuit verification failed!"

    if verbose:
        print(f"\n=== Result: {initial_gates} -> {best_count} gates "
              f"(saved {initial_gates - best_count}) ===")
        gt_counts = {}
        for sig, (gt, ins) in best_circ.gates.items():
            gt_counts[gt] = gt_counts.get(gt, 0) + 1
        print(f"Gate types: {gt_counts}")

    return best_circ


# ---------------------------------------------------------------------------
# Strategy 1: Algebraic simplification
# ---------------------------------------------------------------------------

def _algebraic_simplify(circ: MixedCircuit, ref_tt, n: int,
                        verbose: bool) -> Optional[MixedCircuit]:
    """Apply algebraic identities to simplify the circuit."""
    new_circ = circ.copy()
    changed = False

    # Compute truth tables
    tts = new_circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1
    fanout = new_circ.fanout_count()

    # Check for constant signals
    for sig in list(new_circ.gates.keys()):
        if sig not in new_circ.gates:
            continue
        tt = tts.get(sig, None)
        if tt is None:
            continue
        if tt == 0 or tt == full:
            # Signal is constant - could eliminate but need to rewire
            # For now skip (constants are rare in arithmetic)
            pass

    # Check for equivalent signals (same truth table)
    sig_list = list(new_circ.inputs) + list(new_circ.gates.keys())
    tt_to_sig = {}
    replacements = {}

    for sig in sig_list:
        tt = tts.get(sig)
        if tt is None:
            continue
        # Check positive and negative polarity
        if tt in tt_to_sig:
            existing = tt_to_sig[tt]
            if existing != sig and sig in new_circ.gates:
                # sig can be replaced by existing
                replacements[sig] = existing
        elif (tt ^ full) in tt_to_sig:
            existing = tt_to_sig[tt ^ full]
            if existing != sig and sig in new_circ.gates:
                # sig can be replaced by NOT(existing)
                replacements[sig] = ('NOT', existing)
        else:
            tt_to_sig[tt] = sig

    if replacements:
        changed = True
        if verbose:
            print(f"  Found {len(replacements)} equivalent signals")

        # Apply replacements
        for sig, replacement in replacements.items():
            if isinstance(replacement, tuple):
                # Need a NOT gate
                not_sig = new_circ.new_signal('not')
                new_circ.gates[not_sig] = ('NOT', [replacement[1]])
                _replace_signal(new_circ, sig, not_sig)
            else:
                _replace_signal(new_circ, sig, replacement)

        new_circ.remove_dead_gates()

    # Idempotent law: AND(a,a)=a, OR(a,a)=a, XOR(a,a)=0
    for sig in list(new_circ.gates.keys()):
        if sig not in new_circ.gates:
            continue
        gt, ins = new_circ.gates[sig]
        if len(ins) == 2 and ins[0] == ins[1]:
            if gt in ('AND', 'OR'):
                _replace_signal(new_circ, sig, ins[0])
                del new_circ.gates[sig]
                changed = True
            elif gt == 'XOR':
                # XOR(a,a) = 0, need constant 0
                pass  # Skip for now

    # Double negation: NOT(NOT(a)) = a
    for sig in list(new_circ.gates.keys()):
        if sig not in new_circ.gates:
            continue
        gt, ins = new_circ.gates[sig]
        if gt == 'NOT' and ins[0] in new_circ.gates:
            inner_gt, inner_ins = new_circ.gates[ins[0]]
            if inner_gt == 'NOT':
                _replace_signal(new_circ, sig, inner_ins[0])
                del new_circ.gates[sig]
                changed = True

    if changed:
        new_circ.remove_dead_gates()
        if new_circ.verify(ref_tt):
            return new_circ

    return None if not changed else None


def _replace_signal(circ: MixedCircuit, old: str, new: str):
    """Replace all uses of old signal with new signal."""
    for sig in list(circ.gates.keys()):
        gt, ins = circ.gates[sig]
        new_ins = [new if i == old else i for i in ins]
        if new_ins != ins:
            circ.gates[sig] = (gt, new_ins)
    circ.outputs = [new if o == old else o for o in circ.outputs]


# ---------------------------------------------------------------------------
# Strategy 2: Signal equivalence
# ---------------------------------------------------------------------------

def _signal_equivalence(circ: MixedCircuit, ref_tt, n: int,
                        verbose: bool) -> Optional[MixedCircuit]:
    """Find signals that can be expressed more cheaply using existing signals."""
    tts = circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1

    # Build lookup: truth table -> signal name (prefer inputs and simple gates)
    tt_to_sig = {}
    for inp in circ.inputs:
        tt_to_sig[tts[inp]] = inp

    topo = circ.topological_order()

    # For each gate, check if its truth table can be computed from
    # existing earlier signals with fewer gates
    improvements = []

    all_sigs = list(circ.inputs) + topo
    all_tts = [(sig, tts[sig]) for sig in all_sigs if sig in tts]

    for gate_idx, sig in enumerate(topo):
        if sig not in circ.gates:
            continue
        target_tt = tts[sig]

        # Check 1-gate expressions from earlier signals
        earlier = [(s, t) for s, t in all_tts if s != sig]

        for s1, t1 in earlier:
            # NOT
            if (t1 ^ full) == target_tt:
                # sig = NOT(s1) - 1 gate
                improvements.append((sig, 'NOT', [s1], 1))
                break

        for i, (s1, t1) in enumerate(earlier):
            found = False
            for j, (s2, t2) in enumerate(earlier):
                if j <= i:
                    continue
                if s1 == sig or s2 == sig:
                    continue
                # AND
                if (t1 & t2) == target_tt:
                    improvements.append((sig, 'AND', [s1, s2], 1))
                    found = True
                    break
                # OR
                if (t1 | t2) == target_tt:
                    improvements.append((sig, 'OR', [s1, s2], 1))
                    found = True
                    break
                # XOR
                if (t1 ^ t2) == target_tt:
                    improvements.append((sig, 'XOR', [s1, s2], 1))
                    found = True
                    break
            if found:
                break

    # Apply improvements
    if not improvements:
        return None

    new_circ = circ.copy()
    changed = False

    for sig, new_gt, new_ins, cost in improvements:
        if sig not in new_circ.gates:
            continue
        old_gt, old_ins = new_circ.gates[sig]
        # The improvement replaces the gate for sig
        # But we need to check that this doesn't create cycles
        # and that the subcircuit feeding old sig can be removed

        # Count gates saved: old subcircuit gates - new cost
        old_cost = 1  # The gate itself
        new_cost = cost

        if new_cost < old_cost:
            # Direct improvement: fewer gates
            new_circ.gates[sig] = (new_gt, new_ins)
            changed = True
        elif new_cost == old_cost:
            # Same cost but might enable dead code elimination
            new_circ.gates[sig] = (new_gt, new_ins)
            changed = True

    if changed:
        new_circ.remove_dead_gates()
        if new_circ.verify(ref_tt):
            if new_circ.gate_count() < circ.gate_count():
                return new_circ

    return None


# ---------------------------------------------------------------------------
# Strategy 3: Cut-based rewriting
# ---------------------------------------------------------------------------

def _cut_rewrite_pass(circ: MixedCircuit, ref_tt, n: int,
                      max_cut_size: int, verbose: bool) -> Optional[MixedCircuit]:
    """One pass of cut-based rewriting."""
    tts = circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1
    fanout = circ.fanout_count()

    # Enumerate cuts
    cuts = enumerate_cuts(circ, max_cut_size)

    best_circ = circ
    best_count = circ.gate_count()
    found_improvement = False

    topo = circ.topological_order()

    for sig in topo:
        if sig not in circ.gates:
            continue

        for cut in cuts.get(sig, []):
            if sig in cut:
                continue  # Trivial cut

            k = len(cut)
            if k < 2 or k > max_cut_size:
                continue

            # Count current gates in subcircuit
            current_gates = count_subcirc_gates(circ, sig, cut)
            if current_gates <= 1:
                continue  # Nothing to optimize

            # Check if subcircuit is replaceable (no internal fanout)
            if not subcirc_is_replaceable(circ, sig, cut, fanout):
                continue

            # Compute local truth table
            local_tt, local_k = compute_cut_truth_table(circ, sig, cut, tts, n)
            if local_tt is None:
                continue

            # Try to synthesize with fewer gates
            max_synth = current_gates - 1
            if max_synth <= 0:
                continue

            # For small functions, use exact synthesis
            if local_k <= 4:
                synth = synthesize_optimal(local_tt, local_k, max_gates=max_synth)
            else:
                # For larger, limit search depth
                synth = synthesize_optimal(local_tt, local_k, max_gates=min(max_synth, 4))

            if synth is not None and len(synth) < current_gates:
                # Found improvement! Build replacement
                cut_inputs = sorted(cut)
                new_circ = circ.copy()

                # Remove old subcircuit signals
                old_sigs = get_subcirc_signals(circ, sig, cut)

                # Build new gates
                new_gates, new_root = build_replacement(new_circ, sig, cut,
                                                         synth, cut_inputs)

                # Remove old gates
                for old_sig in old_sigs:
                    if old_sig in new_circ.gates:
                        del new_circ.gates[old_sig]

                # Add new gates
                new_circ.gates.update(new_gates)

                # Rewire: replace sig with new_root
                if new_root != sig:
                    _replace_signal(new_circ, sig, new_root)

                new_circ.remove_dead_gates()

                # Verify
                if new_circ.verify(ref_tt):
                    new_count = new_circ.gate_count()
                    if new_count < best_count:
                        if verbose:
                            print(f"    Cut-{k} at {sig}: {current_gates} -> "
                                  f"{len(synth)} gates (total: {new_count})")
                        best_circ = new_circ
                        best_count = new_count
                        found_improvement = True
                        break  # Move to next signal

        if found_improvement:
            break  # Restart from beginning with new circuit

    return best_circ if found_improvement else None


# ---------------------------------------------------------------------------
# Strategy 4: Window-based rewriting
# ---------------------------------------------------------------------------

def _window_rewrite(circ: MixedCircuit, ref_tt, n: int,
                    verbose: bool) -> Optional[MixedCircuit]:
    """Try window-based multi-output optimization."""
    tts = circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1
    fanout = circ.fanout_count()
    topo = circ.topological_order()

    best_circ = circ
    best_count = circ.gate_count()
    found = False

    # Try windows of connected gates
    for start_idx in range(len(topo)):
        # Build a window of 3-8 connected gates
        for window_size in range(3, min(9, len(topo) - start_idx + 1)):
            window = set(topo[start_idx:start_idx + window_size])

            # Find window inputs and outputs
            window_inputs = set()
            window_outputs = set()

            for sig in window:
                gt, ins = circ.gates[sig]
                for inp in ins:
                    if inp not in window:
                        window_inputs.add(inp)
                # Check if sig is used outside window
                if sig in circ.outputs:
                    window_outputs.add(sig)
                else:
                    for other_sig, (ogt, oins) in circ.gates.items():
                        if other_sig not in window and sig in oins:
                            window_outputs.add(sig)
                            break

            if not window_outputs:
                continue

            k = len(window_inputs)
            if k > 6 or k < 2:
                continue

            # Check that no internal signal has external fanout
            internal = window - window_outputs
            replaceable = True
            for sig in internal:
                for other_sig, (ogt, oins) in circ.gates.items():
                    if other_sig not in window and sig in oins:
                        replaceable = False
                        break
                if sig in circ.outputs:
                    replaceable = False
                if not replaceable:
                    break

            if not replaceable:
                continue

            current_gates = len(window)

            # Compute local truth tables for window outputs
            cut_inputs = sorted(window_inputs)
            cut_global_tts = [tts[ci] for ci in cut_inputs]
            full_n = (1 << (1 << n)) - 1

            root_local_tts = []
            valid = True
            for root in sorted(window_outputs):
                local_tt = 0
                root_tt = tts[root]
                for local_pattern in range(1 << k):
                    match_mask = full_n
                    for i in range(k):
                        if (local_pattern >> i) & 1:
                            match_mask &= cut_global_tts[i]
                        else:
                            match_mask &= cut_global_tts[i] ^ full_n

                    if match_mask == 0:
                        continue
                    root_val = root_tt & match_mask
                    if root_val == match_mask:
                        local_tt |= (1 << local_pattern)
                    elif root_val != 0:
                        valid = False
                        break

                if not valid:
                    break
                root_local_tts.append(local_tt)

            if not valid:
                continue

            # Try to synthesize each output independently
            total_new_gates = 0
            all_synths = []
            ok = True
            for idx, (root, local_tt) in enumerate(zip(sorted(window_outputs),
                                                         root_local_tts)):
                max_g = current_gates - total_new_gates - (len(root_local_tts) - idx - 1)
                if max_g <= 0:
                    ok = False
                    break
                synth = synthesize_optimal(local_tt, k, max_gates=min(max_g, 6))
                if synth is None:
                    ok = False
                    break
                total_new_gates += len(synth)
                all_synths.append((root, synth))

            if not ok or total_new_gates >= current_gates:
                continue

            # Build replacement circuit
            new_circ = circ.copy()

            # Remove window gates
            for sig in window:
                if sig in new_circ.gates:
                    del new_circ.gates[sig]

            # Build new gates for each output
            for root, synth in all_synths:
                if not synth:
                    continue
                new_gates, new_root = build_replacement(new_circ, root,
                                                         frozenset(window_inputs),
                                                         synth, cut_inputs)
                new_circ.gates.update(new_gates)
                if new_root != root:
                    _replace_signal(new_circ, root, new_root)

            new_circ.remove_dead_gates()

            if new_circ.verify(ref_tt):
                new_count = new_circ.gate_count()
                if new_count < best_count:
                    if verbose:
                        print(f"    Window [{start_idx}:{start_idx+window_size}]: "
                              f"{current_gates} -> {total_new_gates} gates "
                              f"(total: {new_count})")
                    best_circ = new_circ
                    best_count = new_count
                    found = True
                    break

        if found:
            break

    return best_circ if found else None


# ---------------------------------------------------------------------------
# Strategy 5: Global re-decomposition
# ---------------------------------------------------------------------------

def _global_redecompose(circ: MixedCircuit, ref_tt, n: int,
                        verbose: bool) -> Optional[MixedCircuit]:
    """Try to resynthesize each output independently and combine with sharing."""
    tts = circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1

    # For each output, find a minimal circuit from primary inputs
    output_tts = []
    for out in circ.outputs:
        output_tts.append(tts[out])

    # Try per-output synthesis for outputs with small support
    input_tts = _make_input_tts(n)

    per_output_synths = []
    for idx, (out, out_tt) in enumerate(zip(circ.outputs, output_tts)):
        # Find essential inputs (variables the output depends on)
        support = []
        for var in range(n):
            step = 1 << var
            mask_lo = 0
            for block in range(1 << (n - var - 1)):
                base = block << (var + 1)
                for i in range(step):
                    mask_lo |= (1 << (base + i))
            lo_bits = out_tt & mask_lo
            hi_bits = (out_tt >> step) & mask_lo
            if lo_bits != hi_bits:
                support.append(var)

        k = len(support)
        if verbose:
            print(f"    Output {out}: depends on {k} of {n} inputs")

        if k <= 5:
            # Project to reduced truth table
            reduced_tt = 0
            for local_p in range(1 << k):
                # Map local pattern to global pattern
                global_p = 0
                for i, var in enumerate(support):
                    if (local_p >> i) & 1:
                        global_p |= (1 << var)
                if (out_tt >> global_p) & 1:
                    reduced_tt |= (1 << local_p)

            synth = synthesize_optimal(reduced_tt, k, max_gates=8)
            if synth is not None:
                per_output_synths.append((idx, out, support, synth))
                if verbose:
                    print(f"      -> can be synthesized in {len(synth)} gates")

    # This is informational - we can't easily combine independent per-output
    # circuits because they may share subexpressions. The cut-based rewriting
    # handles that better.

    return None


# ---------------------------------------------------------------------------
# Strategy 6: Exhaustive merge (check all signal pairs/triples)
# ---------------------------------------------------------------------------

def _exhaustive_merge(circ: MixedCircuit, ref_tt, n: int,
                      verbose: bool) -> Optional[MixedCircuit]:
    """Check if any signal can be expressed as a simple function of other signals.

    More thorough than signal_equivalence: tries 2-gate expressions.
    """
    tts = circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1
    fanout = circ.fanout_count()

    all_sigs = list(circ.inputs) + list(circ.topological_order())
    sig_tts = {s: tts[s] for s in all_sigs if s in tts}

    best_circ = circ
    best_count = circ.gate_count()
    found = False

    # For each gate signal, try to build it from 2 other signals with 1-2 gates
    topo = circ.topological_order()

    for sig in topo:
        if sig not in circ.gates:
            continue
        target = sig_tts[sig]

        # Depth of subcircuit feeding sig (ignoring shared signals)
        gt, ins = circ.gates[sig]
        current_cost = 1  # Just this gate

        # Check if sig = op(a, NOT(b)) for any a, b - costs 2 gates total (NOT + op)
        # Only worth it if the subcircuit for sig costs >= 3 gates
        # and the NOT of b already exists

        # 1 gate expressions were already checked in signal_equivalence
        # Try 2-gate expressions: op1(a, op2(b, c)) where a, b, c are existing signals

        sigs_list = [(s, sig_tts[s]) for s in all_sigs if s in sig_tts and s != sig]

        # First check: can we compute sig with NOT(something)?
        for s, st in sigs_list:
            not_st = st ^ full
            if not_st == target:
                # sig = NOT(s)
                # Check if this would save gates
                # We'd add NOT but remove sig's current gate
                # Only saves if sig's subcircuit has > 1 gate exclusively for it
                pass  # Already handled

        # 2-gate: op(a, NOT(b))
        for i, (sa, ta) in enumerate(sigs_list):
            for j, (sb, tb) in enumerate(sigs_list):
                if i == j:
                    continue
                not_tb = tb ^ full
                for op, func in [('AND', lambda x, y: x & y),
                                 ('OR', lambda x, y: x | y),
                                 ('XOR', lambda x, y: x ^ y)]:
                    if func(ta, not_tb) == target:
                        # sig = op(a, NOT(b)) - 2 gates
                        # Check if NOT(b) already exists
                        not_exists = False
                        not_sig_name = None
                        for ns, nt in sigs_list:
                            if nt == not_tb:
                                not_exists = True
                                not_sig_name = ns
                                break

                        if not_exists:
                            # sig = op(a, not_sig_name) - 1 gate
                            # Replace sig's gate
                            new_circ = circ.copy()
                            new_circ.gates[sig] = (op, [sa, not_sig_name])
                            new_circ.remove_dead_gates()
                            if new_circ.verify(ref_tt):
                                new_count = new_circ.gate_count()
                                if new_count < best_count:
                                    if verbose:
                                        print(f"    {sig} = {op}({sa}, {not_sig_name}) "
                                              f"-> total {new_count}")
                                    best_circ = new_circ
                                    best_count = new_count
                                    found = True
                                    break
                if found:
                    break
            if found:
                break
        if found:
            break

    return best_circ if found else None


# ---------------------------------------------------------------------------
# Advanced: Exhaustive output-cone resynthesis
# ---------------------------------------------------------------------------

def _resynth_output_cone(circ: MixedCircuit, ref_tt, n: int,
                         verbose: bool) -> Optional[MixedCircuit]:
    """For each output, try to resynthesize its entire cone from primary inputs."""
    tts = circ.compute_truth_tables(n)
    full = (1 << (1 << n)) - 1

    new_circ = MixedCircuit(circ.inputs, circ.outputs)
    total_gates = 0

    for out in circ.outputs:
        out_tt = tts[out]

        # Find support
        support = []
        for var in range(n):
            step = 1 << var
            mask_lo = 0
            for block in range(1 << (n - var - 1)):
                base = block << (var + 1)
                for i in range(step):
                    mask_lo |= (1 << (base + i))
            lo_bits = out_tt & mask_lo
            hi_bits = (out_tt >> step) & mask_lo
            if lo_bits != hi_bits:
                support.append(var)

        k = len(support)
        if k > 5:
            return None  # Too large for exact synthesis

        # Project
        reduced_tt = 0
        for local_p in range(1 << k):
            global_p = 0
            for i, var in enumerate(support):
                if (local_p >> i) & 1:
                    global_p |= (1 << var)
            if (out_tt >> global_p) & 1:
                reduced_tt |= (1 << local_p)

        synth = synthesize_optimal(reduced_tt, k, max_gates=8)
        if synth is None:
            return None

        # Build gates
        input_names = [circ.inputs[v] for v in support]
        signal_map = {}
        for i, name in enumerate(input_names):
            signal_map[i] = name

        last_sig = None
        for idx, (gate_type, operands) in enumerate(synth):
            new_sig = new_circ.new_signal(f'o{circ.outputs.index(out)}')
            gate_idx = k + idx
            signal_map[gate_idx] = new_sig

            if gate_type == 'NOT':
                new_circ.gates[new_sig] = ('NOT', [signal_map[operands[0]]])
            else:
                op_a, op_b = operands
                new_circ.gates[new_sig] = (gate_type,
                                           [signal_map[op_a], signal_map[op_b]])
            last_sig = new_sig

        if last_sig is not None:
            # Rename last signal to output
            new_circ.outputs[circ.outputs.index(out)] = last_sig
        total_gates += len(synth)

    if verbose:
        print(f"    Full resynthesis: {total_gates} gates")

    # The per-output approach doesn't share gates
    # Only return if it's actually better
    if new_circ.gate_count() < circ.gate_count():
        # Verify
        # Need to set outputs correctly
        if new_circ.verify(ref_tt):
            return new_circ

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    blif_path = 'circuits/fp4_63gate.blif'
    if len(sys.argv) > 1:
        blif_path = sys.argv[1]

    result = rewrite_mixed_circuit(blif_path)

    # Save result
    out_path = blif_path.replace('.blif', '_opt.blif')
    result.write_blif(out_path)
    print(f"\nOptimized circuit written to {out_path}")
