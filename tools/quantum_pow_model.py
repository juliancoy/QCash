#!/usr/bin/env python3
"""Illustrative resource model, not a quantum-device benchmark or security proof."""
import argparse
import json
import math
from pathlib import Path


def estimate(bits, rate, processors, coherence, logical, overhead):
    """Repeated independent Grover runs with a finite coherent iteration budget.

    Each run uses the textbook success probability sin²((2k+1)asin(sqrt(p))).
    Independent devices combine successes, not classical hash-rate equivalents.
    The iteration rate must include reversible oracle, uncomputation, diffusion,
    and fault-tolerance overhead. Network competition and setup are excluded.
    """
    probability = 2.0 ** (-bits)
    theta = math.asin(math.sqrt(probability))
    iterations = max(1, int(math.pi / (4 * theta * math.sqrt(processors)) - 0.5))
    iterations = min(iterations, max(1, int(rate * coherence)))
    success_one = min(1.0, math.sin((2 * iterations + 1) * theta) ** 2)
    success_all = 1.0 if success_one == 1 else -math.expm1(processors * math.log1p(-success_one))
    expected = (iterations / rate) / success_all
    return {
        "effective_difficulty_bits": bits,
        "classical_expected_hashes": str(2 ** bits),
        "iterations_per_run_per_processor": iterations,
        "success_probability_per_parallel_run": success_all,
        "expected_seconds": expected,
        "estimated_physical_qubits": processors * logical * overhead,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration-rate", type=float, required=True, help="complete Grover iterations/second/device; assumed, not measured")
    parser.add_argument("--processors", type=int, required=True)
    parser.add_argument("--coherence-seconds", type=float, required=True, help="maximum fault-tolerant coherent run time")
    parser.add_argument("--logical-qubits", type=int, required=True, help="assumed logical qubits/device")
    parser.add_argument("--physical-per-logical", type=int, required=True)
    parser.add_argument("--budget-seconds", type=float, default=600)
    parser.add_argument("--attacker-processors", type=int, default=1)
    parser.add_argument("--rewrite-blocks", type=int, default=6)
    parser.add_argument("--target-spacing", type=float, default=600)
    parser.add_argument("--retarget-interval", type=int, default=2016)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--initial-bits", type=float, default=48)
    parser.add_argument("--bits", type=int, nargs="+", default=[12, 32, 48, 64, 80, 96, 128])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for value in [args.iteration_rate, args.coherence_seconds, args.budget_seconds]:
        if not math.isfinite(value) or value <= 0:
            parser.error("rates, coherence and budget must be positive finite numbers")
    if not math.isfinite(args.iteration_rate * args.coherence_seconds):
        parser.error("iteration-rate times coherence must be finite")
    if args.iteration_rate * args.coherence_seconds < 1:
        parser.error("coherence budget must allow at least one full iteration")
    if not 1 <= args.processors <= 10**12 or not 1 <= args.logical_qubits or not 1 <= args.physical_per_logical:
        parser.error("positive qubit counts and 1..10^12 processors required")
    if any(not 1 <= bits <= 256 for bits in args.bits):
        parser.error("effective difficulty bits must be 1..256")
    if not 1 <= args.attacker_processors <= 10**12 or not 1 <= args.rewrite_blocks <= 1000000:
        parser.error("positive bounded attacker and rewrite counts required")
    if not math.isfinite(args.target_spacing) or args.target_spacing <= 0 or not 1 <= args.retarget_interval <= 1000000 or not 1 <= args.epochs <= 1000 or not 1 <= args.initial_bits <= 256:
        parser.error("invalid retarget model parameters")
    rows = []
    for bits in args.bits:
        row = estimate(bits, args.iteration_rate, args.processors, args.coherence_seconds,
                       args.logical_qubits, args.physical_per_logical)
        row["expected_time_within_budget"] = row["expected_seconds"] <= args.budget_seconds
        attacker = estimate(bits, args.iteration_rate, args.attacker_processors, args.coherence_seconds,
                            args.logical_qubits, args.physical_per_logical)
        row["attacker_fixed_target_private_suffix_expected_seconds"] = attacker["expected_seconds"] * args.rewrite_blocks
        row["attacker_physical_qubits"] = attacker["estimated_physical_qubits"]
        rows.append(row)
    trajectory = []
    difficulty = args.initial_bits
    for epoch in range(args.epochs):
        sample = estimate(difficulty, args.iteration_rate, args.processors, args.coherence_seconds,
                          args.logical_qubits, args.physical_per_logical)
        elapsed = sample["expected_seconds"] * args.retarget_interval
        adjustment = min(4.0, max(0.25, sample["expected_seconds"] / args.target_spacing))
        next_bits = min(256.0, max(1.0, difficulty - math.log2(adjustment)))
        trajectory.append({"epoch": epoch, "effective_difficulty_bits": difficulty,
                           "expected_block_seconds": sample["expected_seconds"],
                           "expected_epoch_seconds": elapsed, "next_effective_difficulty_bits": next_bits})
        difficulty = next_bits
    report = {
        "status": "illustrative_model_only",
        "assumptions": {k: v for k, v in vars(args).items() if k not in ["out", "bits"]},
        "limitations": ["No measured quantum hardware", "Rate/qubit counts are supplied assumptions, not derived circuit estimates",
                        "Excludes state preparation, reset latency, network competition and adversarial strategies",
                        "Private suffix estimate holds difficulty fixed and does not race a growing honest chain",
                        "Retarget trajectory is expected-value feedback, not a stochastic network simulation; an epoch must finish before adjustment",
                        "Expected time is not a deadline guarantee or a consensus security bound"],
        "scenarios": rows,
        "expected_value_retarget_trajectory": trajectory,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    for row in rows:
        print(f"{row['effective_difficulty_bits']:3d} bits: {row['expected_seconds']:.6g} expected seconds; "
              f"within budget: {row['expected_time_within_budget']}")


if __name__ == "__main__":
    main()
