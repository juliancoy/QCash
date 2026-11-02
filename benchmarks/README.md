# QCash mining speed

## CPU, Vulkan GPU and simulator comparison

![CPU and GPU mining comparison](mining-speed-ryzen5800x-rtx3060.png)

The latest same-workload run used an AMD Ryzen 7 5800X host with one logical
CPU and an NVIDIA GeForce RTX 3060 (driver 580.142). Five runs measured:

- Vulkan kernel: **486.81 million header attempts/s** from GPU timestamps.
- Vulkan dispatch plus fence wait: **339.55 million attempts/s**.
- Native single-thread CPU baseline: **1.14 million attempts/s**.
- Python/hashlib loop: **0.74 million attempts/s**.

The measured GPU dispatch throughput is about **298 times** the single-thread
CPU baseline. Both use SHA3-256, but the GPU also caches the first fixed rate
block; the CPU baseline recomputes both blocks. This is not a comparison against
an optimized multicore or SIMD midstate CPU miner. GPU throughput excludes
context/pipeline startup, uses persistent mapped buffers, and performs no
per-candidate transfers. Individual samples and GPU timestamp receipts are saved
in [the raw comparison](mining-speed-ryzen5800x-rtx3060.json).

For a new process on the same known-winning 256-nonce fixture, median proof
latencies were **7.67 ms CPU**, **66.06 ms Python**, **267.55 ms GPU**, and
**653.89 ms simulator**. Vulkan setup dominates this tiny job. The library
reuses its context across dispatches, while the command-line controller currently
starts a new process for each work round. Keeping that context across work rounds
is a further optimization; these results do not conceal the current startup cost.
Driver caches may already be warm, so these are new-process timings, not a claim
of completely cold driver caches.

Throughput uses 262,144 complete attempts per round, the same hard-target header
for all CPU/GPU paths, and alternating measurement order. CPU CLI timing includes
process startup; Python hashing runs warm in the benchmark process. The GPU
benchmark verifies 256 shader hashes natively before timing and uses workgroups
of 64 with a 262,144-candidate dispatch limit. Kernel timing brackets the compute
command, while dispatch wall time also includes command recording, submission,
synchronization and readback. The comparison window took 0.0104 seconds to select
classically; CPU miners stop at its first winner after 120 attempts, while the GPU
and simulator evaluate the full window. These are conditional proof latencies,
not expected network block times.

Background workloads were not stopped (host load average approximately 14–15
across 16 logical CPUs). Min/max whiskers show sample spread. Telemetry snapshots
are retained, but they do not measure steady-state miner power or energy per hash.
All 12 CTest suites passed, including 3,084 GPU/OpenSSL digest comparisons,
nonce-boundary/batch checks, real loopback block acceptance and the GPU controller.
The shader also passed SPIR-V validation and the Vulkan validation-layer run
reported no errors. [Validation receipt](vulkan-validation-rtx3060.json) and
[workgroup tuning samples](vulkan-workgroup-tuning-rtx3060.json) are retained. No GPU result is estimated from ML-KEM measurements.

Reproduce with the optional Vulkan build enabled:

```sh
python3 tools/benchmark_mining.py --vulkan-binary ./build/qcash-vulkan \
  --output benchmarks/my-gpu.json
python3 tools/plot_mining.py benchmarks/my-gpu.json --output benchmarks/my-gpu.png
```

See [GPU setup and controller commands](../miners/README.md#vulkan-gpu-mining).

## Historical CPU-only comparison

The earlier results below compare the native miner, an independent Python/hashlib
reference, and the optional Qiskit Aer Grover demonstration. They do not compare
different consensus hash algorithms: all use QCash's SHA3-256 block header rule.

![Mining implementation comparison](mining-speed-ryzen5800x.png)

### Earlier measured result

AMD Ryzen 7 5800X, one logical CPU, five alternating-order repetitions:

| Implementation | Header attempts/s, median | Verified proof latency, median |
| --- | ---: | ---: |
| Native C++ miner | 684,323 | 45.04 ms |
| Python/hashlib reference | 403,891 | 98.87 ms |
| Qiskit Aer Grover demo | Not a quantum hash-throughput measurement | 1,187.87 ms |

Native throughput was about **1.69 times** the Python reference. The simulator's
conditional proof latency was about **26.4 times** native latency. Background
workloads remained active (host load average approximately 32–36 across 16 logical
CPUs); min/max whiskers and individual samples expose the considerable variation.
These are measurements under load, not peak hardware limits.

Throughput uses 262,144 attempts per repetition against the same 268-byte header
with target 1. Every attempt is performed; neither path finds a proof. Native
includes CLI startup, while the Python loop runs warm in the benchmark process.
Python's hash primitive is native OpenSSL through hashlib, not Python SHA3 code.
Native uses OpenSSL 3.5.9; Python uses OpenSSL 3.0.13, so this is an implementation
stack comparison, not an isolated language benchmark.

Proof latency uses separate processes on the same classically preselected
256-nonce window containing one winner. CPU searches stop after 111 attempts.
The simulator hashes all 256 candidates classically, builds the truth-table
oracle, runs an eight-qubit circuit with 12 Grover iterations and 128 shots, then
passes a measured winning nonce through native proof verification. Timing includes
process startup, simulator imports, transpilation, simulation and native proof
construction/verification. Network submission and template setup are excluded.
Each resulting proof is independently checked with hashlib outside the timer.

Finding the fixture window took 0.755 seconds of classical native search, excluded
from all conditional proof latencies and recorded in the raw results. Consequently
these latencies are **not expected network block times**. The simulator has already
found every winner during classical precomputation; the subsequent quantum circuit
is an educational demonstration and supplies no mining speedup.

In that earlier run, no physical QPU, full reversible SHA3 statevector, Vulkan,
WebGPU or WASM mining backend was timed. ML-KEM encryption benchmarks are unrelated to mining throughput.
The reversible SHA3 reference is for circuit correctness/resource exploration,
not a competitive CPU miner.

## Reproduce locally

Build the native release binary and install the optional simulator dependencies
as described in [the miner guide](../miners/README.md#local-qpu-simulation).
Then, from the repository root:

```sh
python3 tools/benchmark_mining.py --output benchmarks/my-machine.json
python3 tools/plot_mining.py benchmarks/my-machine.json --output benchmarks/my-machine.png
```

The plotting script requires Matplotlib. The benchmark defaults to five rounds,
262,144 hash attempts per throughput round, and an eight-qubit search window.
`--cpu` selects a permitted logical CPU (default: the first allowed CPU). It uses
an isolated temporary loopback node and disposable keys, never the hosted chain.
Its output file must not already exist. Raw samples, simulator receipts, public
fixture bytes, binary digest, versions, affinity and host load are retained in
[the JSON result](mining-speed-ryzen5800x.json).

The native source commit in that result identifies the binary's source; the dirty
flag records that the newly added benchmark scripts had not yet been committed.
No consensus or native mining code was changed for this measurement.
