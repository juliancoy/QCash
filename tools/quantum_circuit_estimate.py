#!/usr/bin/env python3
"""Literature-derived logical resource scenarios, not a compiled mining circuit.

Amy et al., Table 2: optimized Keccak-p[1600,24] uses 3200 logical qubits,
499200 T/Tdagger gates, T-depth 432, total depth 11040. Our 268-byte header
requires two SHA3 rate blocks; its 320 variable nonce bits lie in block two.
The fixed first-block midstate can be precomputed outside Grover search.
"""
import argparse
import json
import math
from pathlib import Path

SOURCE = 'https://www.mathstat.dal.ca/~mamy/Papers/breakingsha.pdf'

def resources(target):
    if not 0 < target < 2**256:
        raise ValueError('target must be in 1..2^256-1')
    # Constructive ripple lexicographic <= predicate: compute equal prefixes,
    # toggle flag on a first-less bit or equality; phase flag; reverse predicate.
    comparator_toffolis = 2 * (256 + target.bit_count())
    # 320-controlled phase via 318 AND ancillas, compute/uncompute + final CZ.
    diffusion_toffolis = 2 * (320 - 2)
    toffolis = comparator_toffolis + diffusion_toffolis
    return dict(hash_t_count=2*499200, hash_t_depth=2*432,
                hash_circuit_depth=2*11040,
                predicate_toffolis=comparator_toffolis,
                diffusion_toffolis=diffusion_toffolis,
                t_count=2*499200+7*toffolis,
                # Serial 7-T Toffoli schedule, deliberately not depth-optimized.
                scheduled_t_depth=2*432+7*toffolis,
                logical_qubit_budget=3200+258+318+320,
                success_fraction=(target+1)/2**256)

def scenario(bits, t_layer_ns, magic_states_per_second):
    target=2**(256-bits)-1
    counts=resources(target)
    theta=math.asin(math.sqrt(counts['success_fraction']))
    k=max(0, round(math.pi/(4*theta)-.5))
    success=math.sin((2*k+1)*theta)**2
    # A lower bound for this selected logical schedule under supplied rates;
    # omitted Clifford/routing/factory/QEC costs can increase elapsed time.
    per_iteration=max(counts['scheduled_t_depth']*t_layer_ns*1e-9,
                      counts['t_count']/magic_states_per_second)
    return dict(effective_difficulty_bits=bits, target_hex=f'{target:064x}',
                **counts, iterations=k, ideal_success_probability=success,
                total_t_gates=str(k*counts['t_count']),
                conditional_run_seconds_floor=k*per_iteration,
                iteration_seconds_floor=per_iteration)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--t-layer-ns',type=float,required=True)
    p.add_argument('--magic-states-per-second',type=float,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if any(not math.isfinite(v) or v<=0 for v in (a.t_layer_ns,a.magic_states_per_second)):
        p.error('positive finite hardware assumptions required')
    report=dict(status='literature_derived_conditional_logical_resource_model',source=SOURCE,
                source_location='Table 2 and Section 7.2',
                header_bytes=268,sha3_rate_bytes=136,variable_nonce_bits=320,
                precomputed_permutations=1,variable_permutations=1,
                assumptions=dict(t_layer_ns=a.t_layer_ns,magic_states_per_second=a.magic_states_per_second),
                limitations=['Not a compiled or independently verified quantum mining circuit',
                'Published permutation is adapted by resource accounting; absorption and layout need compilation',
                'Constructive comparator and diffusion accounting; no quantum phase simulation performed',
                'Timing is a conditional floor for this schedule, not measured runtime or an attacker lower bound',
                'Logical budget excludes routing, magic-state factories and error correction; no physical qubit estimate',
                'No QEC distance or total failure budget chosen; arbitrary rates do not imply feasible hardware',
                'No network race, interruption, synchronization, all-quantum tie-breaking or attack probability modeled',
                'Newer or better circuits can reduce these costs; this is not a security proof'],
                scenarios=[scenario(b,a.t_layer_ns,a.magic_states_per_second) for b in (12,32,48,64,80,96,128)])
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(a.out)
if __name__=='__main__':main()
