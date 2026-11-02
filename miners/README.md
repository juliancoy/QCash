# QCash mining implementations

This directory provides a runnable, bounded conventional CPU controller and a
hardware-neutral work interface. Both use the same SHA3-256 proof and full-node
validation. A portal account does not grant mining authority. These are
experimental testnet coins, with no stated monetary value.

## Build the conventional implementation

Linux is the tested host platform. CPU code uses OpenSSL SHA3 on the host;
there is no GPU, ASIC or QPU backend hidden behind the CPU label.

```sh
git clone https://github.com/juliancoy/QCash.git
cd QCash
./tools/bootstrap-openssl.sh
cmake --preset local
cmake --build --preset local --parallel 2
ctest --preset local
./build/qcash init node-home artifacts/devnet-genesis/genesis.bin
./build/qcash init miner-home artifacts/devnet-genesis/genesis.bin
./build/qcash identity node-home
```

Record the exact chain ID from `init` and the peer fingerprint from `identity`.
Get a **public** QCash reward address from your wallet; a miner does not need its
seed, private signing key, browser session or OrgPortal account token. A local
CLI wallet can also be created with `qcash wallet reward.pem`; that development
PEM is unencrypted and should remain owner-only and outside source control.

Run a validating node in terminal A:

```sh
./build/qcash node node-home 127.0.0.1:29600
```

In terminal B, replace the three uppercase values with independently verified
64-character lowercase hex values. The controller has a wall-clock deadline,
per-round hash budget and cooldown. It does not start automatically on login.

```sh
python3 miners/cpu_worker.py --binary ./build/qcash --home miner-home \
  --peer 127.0.0.1:29600 --peer-key PEER_FINGERPRINT \
  --chain-id EXACT_CHAIN_ID --address YOUR_PUBLIC_REWARD_ADDRESS \
  --rounds 10 --attempts 1000000 --seconds 60 --cooldown 1
```

Ctrl-C stops the controller and its active child. Events distinguish exhausted
work, stale jobs, peer acceptance and uncertain/rejected submissions. Peer
acceptance is not finality: rewards can be immature or removed by a reorg. A
failed or ambiguous submission is not blindly retried. Temporary work files are
private and removed. The controller is single-worker; use OS resource controls
when sharing a machine. No hashrate is invented from its attempt budget.

For standalone continuous node mining, the existing `node ... --mine-to ADDRESS`
path remains available. It validates, synchronizes and mines without this helper.

## Attach another conventional or quantum implementation

The native peer protocol now supports command 8: exact chain ID plus a 32-byte
reward address yields one canonical candidate block. It uses the existing pinned
hybrid TLS transport, frame limits and full-node validation. Each template gets a
fresh random extra nonce. It commits to the miner reward, parent, transactions,
state, timestamp, target and chain. No consensus rule or easier quantum target is
introduced.

```sh
./build/qcash trust miner-home HOST:PORT PEER_FINGERPRINT
./build/qcash work miner-home HOST:PORT YOUR_PUBLIC_REWARD_ADDRESS work.bin
./build/qcash inspect-work work.bin > work.json
```

`work.json` is the `qcash-work-v1` backend contract:

- `header`: hex-encoded canonical header bytes, including the domain label.
- `target`: 32-byte **big-endian** inclusive SHA3-256 target.
- `chainId`, `parent`, `rewardAddress`: identities to verify before spending compute.
- `nonceOffset`: start of an eight-byte big-endian unsigned nonce.
- `extraNonceOffset`: start of the preceding 32-byte extra nonce.

A hardware backend searches for `SHA3-256(header) <= target`. It may change only
the eight nonce bytes and the 32 extra-nonce bytes, preserving all other bytes.
SHA3-256 is not Ethereum's legacy Keccak-256. The transaction body in `work.bin`
must remain unchanged. Split extra-nonce ranges between simultaneous workers to
avoid duplicated searches. Chain parameters and body encoding are in `POW.md`.

The reference bounded solver is executable immediately:

```sh
./build/qcash solve work.bin solved.bin 1000000
```

Exit 0 writes a solved block; 3 means the budget was exhausted and writes no
solution; 130 means cancellation. Output paths must not already exist. After an
exhausted budget, request a fresh template for another independently randomized
range. A custom GPU, FPGA, ASIC, simulator or QPU implementation instead returns
an extra nonce and nonce, which are verified locally before serialization:

```sh
./build/qcash complete-work work.bin EXTRA_NONCE_HEX NONCE_DECIMAL solved.bin
./build/qcash query miner-home HOST:PORT
./build/qcash submit miner-home HOST:PORT solved.bin
```

Check the current chain ID and tip before submitting; discard stale templates.
Every submitted block still undergoes ordinary consensus validation. An invalid
proof, wrong reward/state, bad transaction, duplicate, wrong chain or invalid
timestamp cannot bypass validation through this interface. Templates are not
reserved jobs and do not guarantee rewards. Avoid logging provider credentials;
keep QPU service authentication in the operator's local/provider environment.

### Quantum status

The backend contract, native nonce verification and an optional local Qiskit
simulator are implemented and tested. The simulator uses a classically
precomputed oracle; full reversible SHA3 reference components are separately
basis-tested. **Full-hash quantum statevector mining, a provider integration and
a successful quantum-hardware mining run are not supplied or claimed.** A real
QPU backend must execute the full canonical-header hash/target predicate;
classically precomputing winners is not evidence of quantum mining advantage. See the resource-model limitations in `SECURITY_REVIEW.md` and
IBM's [Grover tutorial](https://quantum.cloud.ibm.com/docs/en/tutorials/grovers-algorithm).
Provider queue delay and circuit execution can make a template stale; cancel
obsolete work and impose an explicit provider spending budget.

## Connectivity and scope

The current hosted deployment exposes its wallet HTTPS adapter, **not** a public
native mining peer. The portal displays that limitation. Use your own loopback
node or an explicitly operated private/lab peer with independently verified pins.
An HTTPS wallet gateway URL is not a native peer endpoint. Public seed discovery,
NAT traversal, scalable peer admission and a hardened public mining service are
not implemented by this addition; do not expose the experimental peer directly
to the Internet and describe it as production hardened.

## Local QPU simulation

Use Python 3.12 and an isolated environment. No quantum-provider account, cloud
credential, GPU or physical QPU is needed. Nothing runs in the browser.

```sh
python3 -m venv .deps/quantum-venv
.deps/quantum-venv/bin/python -m pip install -r miners/requirements-simulator.txt
```

With your separately running private node and verified identity values from the
conventional setup, opt into the simulator explicitly:

```sh
.deps/quantum-venv/bin/python miners/cpu_worker.py --backend qpu-simulator \
  --binary ./build/qcash --home miner-home --peer 127.0.0.1:29600 \
  --peer-key PEER_FINGERPRINT --chain-id EXACT_CHAIN_ID \
  --address YOUR_PUBLIC_REWARD_ADDRESS --rounds 4 --seconds 60 \
  --attempts 4096 --simulator-bits 12 --simulator-shots 128
```

The controller retains peer pinning, chain/reward checks, deadline cancellation,
stale-parent checks and native full-node submission. Cancellation terminates its
whole child process group, including the simulator's isolated worker. The CPU
backend remains the default. An exhausted simulator window is not a failed node
and does not produce a fabricated solution.

### What is actually simulated

1. Classically hash every nonce in a bounded window of the **real canonical
   QCash header**. Count every hash and record the window and marked-state count.
2. Construct a small phase oracle from that precomputed truth table, then run
   Hadamard preparation, Grover iterations and measurements in **Qiskit Aer**.
3. Select a candidate only from measured outcomes. Never silently fall back to
   an unmeasured nonce already known from classical enumeration.
4. Rehash it and invoke native `complete-work`. Submit only after checking that
   the parent is still current; full-node consensus checks remain unchanged.

This is a **precomputed-oracle quantum-circuit demonstration**, not SHA3 executed
inside Grover search, competitive mining, a quantum speedup, or hardware evidence.
The classical lookup already discovers winning nonces; it is deliberately not
hidden in timing or telemetry. Receipts identify the mode, classical hash count,
qubits, marked states, iterations, shot counts, seeds, dependency versions,
source hash, transpilation and simulation timings separately. A window with no
winner reports `empty_window`, zero executed shots and `simulation_executed:false`.
Shot sampling can also miss: no solution is written in that case.

The simulator is capped at 1–12 search qubits, 1–1024 shots, 0–64 iterations and
4096 phase-pattern applications. It uses one CPU thread and a separate process
with a wall-clock limit; oversized requests are rejected before building a
circuit. Aer receives a 128 MiB state allocation ceiling; this is **not** a claim
that the entire Python process uses at most 128 MiB. The controller's attempt
budget must cover the whole classical lookup window.

### Offline candidate and receipt

After exporting `work.bin`, run a single bounded simulation without connecting to
any peer or submitting a block:

```sh
.deps/quantum-venv/bin/python miners/quantum_simulator.py \
  --binary ./build/qcash --work work.bin --output simulated-solution.bin \
  --chain-id EXACT_CHAIN_ID --address YOUR_PUBLIC_REWARD_ADDRESS \
  --bits 12 --shots 128 --seed 7 --seconds 30 \
  --verify-reversible --receipt simulation.json
```

Exit 0 means a measured nonce passed native verification and a new solution file
was written. Exit 3 means an empty/missed window or elapsed budget; exit 130 is
cancellation; exit 2 is invalid work, missing dependencies or another failure.
Output and receipt paths must not already exist. `--nonce-start` selects another
bounded 64-bit nonce window without changing the extra nonce or any commitments.
A timeout or cancellation does not assert that no partial file could have been
written during the final native output operation: inspect it with native tools
before any deliberate submission. The managed controller uses temporary files
and always removes them when it stops.

### Actual reversible hash-oracle components

`reversible_sha3.py` provides X/CX/CCX gate programs for all 24 rounds of
Keccak-f[1600], SHA3-256 absorption/domain padding, an inclusive big-endian target
comparator, Z phase marking, and inverse cleanup. These are **real reversible
hash computations**, not a truth-table replacement. Independent tests compare
hashes with `hashlib` for empty input, `abc`, rate-boundary messages and the actual
268-byte QCash header length. Comparator truth tables are checked exhaustively
at small widths; small Qiskit statevectors independently check relative phase
and clean ancillas in superposition.

The full hash reference intentionally retains intermediate rounds to make
compute/uncompute straightforward. It is qubit-heavy and not an optimized mining
oracle. Full SHA3 is executed only on classical basis states in these tests,
**not as a dense full-circuit quantum simulation**. Qiskit export is capped at 20
qubits for small components. `--verify-reversible` performs a separate full-header
basis-gate verification of the sampled candidate and reports that distinction.
Do not substitute these trace-heavy counts for the different literature-derived
resource scenarios in `tools/quantum_circuit_estimate.py`.

```sh
.deps/quantum-venv/bin/python tests/reversible_sha3_test.py
.deps/quantum-venv/bin/python tests/quantum_simulator_test.py
# Optional CTest integration; ordinary builds do not install Python packages.
cmake --preset local -DQCASH_SIMULATOR_PYTHON="$PWD/.deps/quantum-venv/bin/python"
ctest --preset local
```

The simulator integration test uses an isolated loopback full node, chooses a
small fixture window classically, runs real Aer measurements, verifies the nonce
with the native executable and gets the block accepted by that local node. It
does not mine on or submit anything to the hosted testnet. See
[Qiskit Aer](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.AerSimulator.html)
for simulator semantics and supported methods.
