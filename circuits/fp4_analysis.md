# FP4 Multiplier Circuit Analysis

## 1. Mathematical Function

The circuit computes the 9-bit two's complement integer product of two
FP4 E2M1 encoded values, scaled by 4:

    output = sigma(a) * sigma(b)

where `sigma` maps each 4-bit code `(x3,x2,x1,x0)` to a signed integer:

    sigma(code) = (-1)^x0 * magnitude(x3,x2,x1)

The magnitude encoding is a scrambled FP4 E2M1 representation scaled by 2:

| Code | (x3,x2,x1,x0) | sigma(code) | FP4 value |
|------|----------------|-------------|-----------|
|  0   | 0000           |    0        |  0.0      |
|  1   | 0001           |    0        | -0.0      |
|  2   | 0010           |    8        |  4.0      |
|  3   | 0011           |   -8        | -4.0      |
|  4   | 0100           |    2        |  1.0      |
|  5   | 0101           |   -2        | -1.0      |
|  6   | 0110           |    4        |  2.0      |
|  7   | 0111           |   -4        | -2.0      |
|  8   | 1000           |    1        |  0.5      |
|  9   | 1001           |   -1        | -0.5      |
| 10   | 1010           |   12        |  6.0      |
| 11   | 1011           |  -12        | -6.0      |
| 12   | 1100           |    3        |  1.5      |
| 13   | 1101           |   -3        | -1.5      |
| 14   | 1110           |    6        |  3.0      |
| 15   | 1111           |   -6        | -3.0      |

The output is `4 * FP4_E2M1(a) * FP4_E2M1(b)` stored as a 9-bit two's
complement integer (y8=MSB/sign, y0=LSB). Range: -144 to +144.

### FP4 Bit Decoding

The input bits map to standard FP4 E2M1 fields as follows:

    sign = x0 (= a0 for operand a)
    mantissa m = x3 (= a3)
    exponent e1 = x1 (= a1)
    exponent e0 = x1 XOR x2 (= a1 XOR a2)

Exponent e = 2*a1 + (a1 XOR a2):

| a1 | a2 | e  | Description          |
|----|----|----|----------------------|
|  0 |  0 |  0 | Zero/subnormal       |
|  0 |  1 |  1 | Normal, exp=1        |
|  1 |  1 |  2 | Normal, exp=2        |
|  1 |  0 |  3 | Normal, exp=3        |

Magnitude formula:
- e=0: `mag = a3` (subnormal: 0 or 1)
- e>0: `mag = 2^e + a3 * 2^(e-1) = 2^(e-1) * (2+a3)` (normal)

### Product Table

```
       b=0  b=1  b=2  b=3  b=4  b=5  b=6  b=7  b=8  b=9 b=10 b=11 b=12 b=13 b=14 b=15
a= 0:    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0
a= 1:    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0
a= 2:    0    0   64  -64   16  -16   32  -32    8   -8   96  -96   24  -24   48  -48
a= 3:    0    0  -64   64  -16   16  -32   32   -8    8  -96   96  -24   24  -48   48
a= 4:    0    0   16  -16    4   -4    8   -8    2   -2   24  -24    6   -6   12  -12
a= 5:    0    0  -16   16   -4    4   -8    8   -2    2  -24   24   -6    6  -12   12
a= 6:    0    0   32  -32    8   -8   16  -16    4   -4   48  -48   12  -12   24  -24
a= 7:    0    0  -32   32   -8    8  -16   16   -4    4  -48   48  -12   12  -24   24
a= 8:    0    0    8   -8    2   -2    4   -4    1   -1   12  -12    3   -3    6   -6
a= 9:    0    0   -8    8   -2    2   -4    4   -1    1  -12   12   -3    3   -6    6
a=10:    0    0   96  -96   24  -24   48  -48   12  -12  144 -144   36  -36   72  -72
a=11:    0    0  -96   96  -24   24  -48   48  -12   12 -144  144  -36   36  -72   72
a=12:    0    0   24  -24    6   -6   12  -12    3   -3   36  -36    9   -9   18  -18
a=13:    0    0  -24   24   -6    6  -12   12   -3    3  -36   36   -9    9  -18   18
a=14:    0    0   48  -48   12  -12   24  -24    6   -6   72  -72   18  -18   36  -36
a=15:    0    0  -48   48  -12   12  -24   24   -6    6  -72   72  -18   18  -36   36
```

## 2. Symmetries and Structure

### Commutativity
- **f(a,b) = f(b,a)**: VERIFIED for all 256 patterns.

### Sign Factorization
- Output sign = `a0 XOR b0` (when product is nonzero)
- Magnitude only depends on `(a3,a2,a1)` and `(b3,b2,b1)` -- 6 inputs
- The sign bit (a0, b0) only participates in the conditional negate

### Output Dependencies

| Output | Depends on                      | #Inputs | Minterms |
|--------|---------------------------------|---------|----------|
| y0     | a3, a1, b3, b1                  |    4    |    16    |
| y1     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |    40    |
| y2     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |    72    |
| y3     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |    96    |
| y4     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |   104    |
| y5     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |   104    |
| y6     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |   100    |
| y7     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |    98    |
| y8     | a3,a2,a1,a0,b3,b2,b1,b0        |    8    |    98    |

### Special Output Properties

- **y0 = a3 AND NOT(a1) AND b3 AND NOT(b1)**
  Only 1 minterm in the effective 4-input space.
  Product is odd iff both magnitudes are exactly 1 (both subnormal 0.5 values).
  Needs 3 AIG gates (or 5 mixed gates with explicit NOT).

- **y8 = (a0 XOR b0) AND (a3 OR a2 OR a1) AND (b3 OR b2 OR b1)**
  This is the output sign bit: negative iff signs differ AND both nonzero.

- **y7 XOR y8** has only 4 minterms. These correspond to |product| = 144
  (both operands have magnitude 12, i.e., code 10 or 11).
  `y7 XOR y8 = (a3 AND a1 AND NOT(a2)) AND (b3 AND b1 AND NOT(b2))`

### Unsigned Magnitude Product Properties

The unsigned magnitude product P = mag(a) * mag(b) has only 19 distinct values:
{0, 1, 2, 3, 4, 6, 8, 9, 12, 16, 18, 24, 32, 36, 48, 64, 72, 96, 144}

**Critical property: Every non-zero product has AT MOST 2 bits set.**

This follows from the FP4 mantissa structure:
- Mantissa product `(2+a3)(2+b3)` is 4 (=0100, 1 bit), 6 (=0110, 2 adjacent bits),
  or 9 (=1001, 2 bits with gap 3)
- Left-shifting preserves the number of set bits

Product type distribution (over 64 magnitude input combinations):
- Zero: 15 patterns
- Power of 2 (1 bit set): 16 patterns
- Adjacent pair (2 adjacent bits): 24 patterns
- 3-apart pair (2 bits, gap=3): 9 patterns

### Mutual Exclusivity of Product Bits

Many product bit pairs can never both be 1 simultaneously:

| Pair       | Overlap |
|------------|---------|
| P[0],P[2]  | 0 (exclusive) |
| P[0],P[4]  | 0 (exclusive) |
| P[0],P[5]  | 0 (exclusive) |
| P[0],P[6]  | 0 (exclusive) |
| P[0],P[7]  | 0 (exclusive) |
| P[1],P[3]  | 0 (exclusive) |
| P[1],P[5]  | 0 (exclusive) |
| P[1],P[6]  | 0 (exclusive) |
| P[1],P[7]  | 0 (exclusive) |
| P[2],P[4]  | 0 (exclusive) |
| P[2],P[6]  | 0 (exclusive) |
| P[2],P[7]  | 0 (exclusive) |
| P[3],P[5]  | 0 (exclusive) |
| P[3],P[7]  | 0 (exclusive) |
| P[4],P[6]  | 0 (exclusive) |
| P[5],P[7]  | 0 (exclusive) |
| P[6],P[7]  | 0 (exclusive) |

## 3. Existing Circuit Architecture (63 mixed gates)

The existing 63-gate circuit decomposes cleanly into three parts:

### Part 1: Unsigned Magnitude Product (41 gates)

Computes P[7:0] = mag(a3,a2,a1) * mag(b3,b2,b1) using only the 6
magnitude-related inputs. Gate mix: 16 AND + 6 OR + 14 XOR + 5 NOT = 41 gates.

Key intermediate signals with high fanout:
- w_36 (4 uses): Combines exponent comparison signals
- w_46 (4 uses): Exponent sum parity
- w_47 (4 uses): Exponent-mantissa interaction
- w_34 (3 uses): Exponent equality XOR
- w_44 (3 uses): Exponent sum carry-related

The magnitude computation produces:
- P[0] = y0 (product bit 0)
- P[2] through P[7] as internal signals (w_56, w_67, w_58, w_65, w_48, w_50)
- w_22 and w_45 as helper signals for the negate chain
- P[1] is NOT computed explicitly; it is derived as `w_22 AND NOT(w_45)` in the negate

### Part 2: Sign Computation (1 gate)

    sign = a0 XOR b0  (= w_30)

This signal has 7 downstream users in the negate chain.

### Part 3: Conditional Negate (21 gates)

Computes `output = sign ? -P : P` using an OR-based carry accumulation chain.
Gate mix: 8 AND + 6 OR + 7 XOR = 21 gates.

Structure (for y2..y6):
```
acc = sign & (P[0] | y1 | P[2] | P[3] | ...)   -- OR accumulation gated by sign
y[k] = P[k] XOR acc                              -- XOR with accumulated carry
```

This is 2 gates cheaper than the standard XOR-based carry chain (23 gates)
because it replaces XOR(P[k], sign) + XOR(result, carry) with direct
OR accumulation + single XOR.

## 4. ABC Synthesis Comparison

| Method                              | AIG gates | Mixed gates |
|-------------------------------------|-----------|-------------|
| Original circuit (from BLIF)        |    102    |     63      |
| ABC resyn2+resyn2rs (from structure)|     86    |     --      |
| ABC best AIG (aggressive)           |     86    |     --      |
| ABC amap to mixed library           |     --    |     77      |
| ABC from SOP (no structure)         |    232    |    243      |

The hand-crafted circuit outperforms ABC by 18% in mixed gates (63 vs 77).
ABC cannot match the human-designed structure because:
1. ABC does not natively optimize for XOR-rich circuits
2. The sign-magnitude decomposition requires domain-specific insight
3. The FP4 mantissa/exponent factoring is not discoverable by local rewriting

## 5. Alternative Decompositions

### 5a. Sign-Magnitude with Binary Multiplier

    Step 1: Encode mag_a[3:0] from (a3,a2,a1)       -- ~8 mixed gates per operand
    Step 2: 4x4 unsigned multiply P = mag_a * mag_b  -- ~36-40 mixed gates
    Step 3: sign = a0 XOR b0                          -- 1 gate
    Step 4: Conditional negate output = sign ? -P : P  -- 23 gates

**Estimated total: ~76-80 mixed gates. WORSE than 63.**

The binary multiplier doesn't exploit the FP4 constraints (only 8 valid
magnitude values out of 16 possible 4-bit values).

### 5b. FP-Domain Decomposition

    Step 1: Mantissa product (2+a3)*(2+b3) -- 2 gates (AND + XOR)
    Step 2: Exponent sum ea+eb             -- ~7 gates (2-bit adder)
    Step 3: Barrel shift by exponent sum   -- ~24+ gates (very expensive!)
    Step 4: Handle subnormal/zero cases    -- ~6 gates
    Step 5: Conditional negate             -- ~23 gates

**Estimated total: ~62+ gates but the barrel shifter may need significantly more.**

The barrel shift is the bottleneck. A 5-way MUX for each of 8 output bits
would need ~40 gates, making total ~80+.

### 5c. Shannon Expansion on (a1, b1)

Split into 4 sub-circuits based on `(a1, b1)`:
- (0,0): Small products 0..9, needs 4 bits
- (0,1)/(1,0): Medium products 0..36, needs 6 bits
- (1,1): Large products 16..144, needs 8 bits

Each sub-circuit is simpler, but the 4-to-1 MUX overhead (~7 gates per
output bit * 9 = 63 gates) negates the savings.

**Estimated total: ~80+ mixed gates. WORSE.**

### 5d. Direct Per-Output Synthesis

Synthesize each output bit independently with SAT-based exact synthesis,
then rely on structural hashing to share gates.

Per-output AIG gate counts (ABC optimized):
y0=3, y1=23, y2=42, y3=63, y4=61, y5=47, y6=32, y7=17, y8=9

Sum = 297 AIG gates without sharing. With sharing, ABC achieves 86 AIG.
The 3.45x compression from sharing shows heavy structural overlap.

## 6. Gate Count Lower Bounds

### Per-output Lower Bounds (AIG)
- y0: 3 AIG gates (proven by SAT, confirmed by ABC)
- y8: 9 AIG gates (ABC optimized)
- y7 XOR y8: 5 AIG gates (6-input AND with one inversion)

### Theoretical Considerations
- 8 inputs, 9 outputs
- The function is not decomposable into independent sub-functions
  (outputs y1-y7 all share dependencies on all 8 inputs)
- Shannon entropy argument: with 82 minterms across 8 product bits
  (from 64 input patterns), the function has moderate complexity
- Estimated lower bound: **~50-55 mixed gates** (based on per-output
  minimum gate counts with realistic sharing factors)

### Why 63 May Be Near-Optimal

1. The magnitude computation (41 gates) produces 8 product bits plus 2
   helper signals from 6 inputs. Average: ~4.5 gates per output with
   sharing. Given the complexity of the individual functions (10-19
   minterms), this is very efficient.

2. The negate chain (21 gates) for 8-bit conditional negate is only 2
   gates above the theoretical minimum of ~19 gates (which would require
   zero-cost carry chain termination).

3. No dead signals, no redundant gates -- the circuit is fully utilized.

## 7. Concrete Suggestions for Beating 63 Gates

### Approach A: Micro-optimizations (target: 60-62 gates)

1. **Fold NOT gates into XNOR/NOR**: The 5 NOT gates in the magnitude
   part each feed into a single AND gate. If the gate library includes
   NAND (which acts as AND with one inverted input when structurally
   hashed), some NOT+AND pairs might collapse. However, since the NOT
   output is only used once while the non-inverted signal has other uses,
   this doesn't directly help.

2. **Optimize the y1 computation path**: y1 uses 3 negate gates plus
   relies on helper signals w_22 and not_25 from the magnitude part.
   An alternative encoding of P[1] that avoids the w_22/not_25
   intermediates could save 1-2 gates.

3. **Exploit the 2-bit product property in the negate**: Since every
   product has at most 2 bits set, the carry chain in the negate always
   terminates within a bounded distance. A truncated carry chain that
   handles only the relevant cases might save 1-2 gates.

### Approach B: SAT-based resynthesis of sub-circuits (target: 55-60 gates)

1. Partition the circuit into the magnitude sub-circuit (6 inputs, 9
   outputs including helpers) and the negate sub-circuit (sign + product
   bits -> 9 outputs).

2. Use SAT-based exact synthesis on 4-5 input sub-functions within the
   magnitude computation to find optimal implementations.

3. Re-combine with structural hashing to maximize sharing.

### Approach C: Joint sign-magnitude synthesis (target: 55-58 gates)

Instead of computing magnitude first and then negating, directly
synthesize the signed output bits. For each output bit y[k]:

    y[k] = P[k] XOR (sign AND [k > trailing_zeros(P)])

Since trailing_zeros(P) is a function of the magnitude inputs, and sign
is a0 XOR b0, we can potentially share the "trailing zeros detector"
across all output bits, avoiding the sequential carry chain entirely.

The trailing zeros function on our product takes values 0-6 (and 8 for
zero), which can be encoded in 3 bits. Computing these 3 bits from the
6 magnitude inputs and then generating the 8 comparators might be cheaper
than the 21-gate carry chain.

### Approach D: E-graph rewriting with FP4-specific rules

Add custom rewrite rules to the e-graph engine that capture:
- FP4 E2M1 decoding patterns
- Mantissa multiplication identities
- Exponent addition simplifications
- The 2-bit product invariant

These domain-specific rules could discover optimizations that neither
ABC nor generic AIG rewriting can find.

## 8. Summary

The FP4 multiplier circuit computing `sigma(a) * sigma(b)` in 63 mixed
gates is a highly optimized design that exploits the FP4 E2M1 floating
point structure. It decomposes cleanly into:

- **41 gates**: unsigned magnitude product (6 inputs -> 8 bits + helpers)
- **1 gate**: sign computation (a0 XOR b0)
- **21 gates**: conditional negate with OR-based carry chain

Key mathematical properties that enable this efficiency:
1. Sign-magnitude separation (sign depends only on a0, b0)
2. FP4 mantissa product has only 3 possible values {4, 6, 9}
3. Every unsigned product has at most 2 bits set
4. The function is commutative: f(a,b) = f(b,a)
5. Heavy signal sharing (4 signals with 4+ users)

Beating 63 gates will require either micro-optimizations saving 1-3
gates, or a fundamentally different decomposition strategy such as joint
sign-magnitude synthesis or SAT-based resynthesis of sub-circuits.
The theoretical lower bound is estimated at 50-55 mixed gates.
