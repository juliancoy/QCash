# Security review and independent audit handoff

Status: internal engineering review; **independent audit not performed**. No
reviewer has been commissioned or contacted. This protocol has no established
all-quantum consensus security proof and is not ready to secure public funds.

## Scope and evidence

Reviewed native crypto, transfer application, PoW validation, fork selection,
peer TLS/TOFU, journal recovery and parser boundaries. The earlier Go/CometBFT
experiment is separate and outside this review. OpenSSL internals, hardware side
channels, compiler supply chain and wallet recovery have not been audited here.
No quantum hardware or quantum circuit compiler was used.

Reproduce from the repository root:

```bash
./tools/bootstrap-openssl.sh
cmake --preset local
cmake --build --preset local --parallel 4
ctest --preset local
./build/pqcoin-consensus-adversary
cmake --preset sanitize
cmake --build --preset sanitize --parallel 4
ctest --preset sanitize
python3 tests/pow_network.py
PQCOIN_POW_BINARY="$PWD/build-sanitize/pqcoin-pow" \
PQCOIN_POW_REPORT="$PWD/artifacts/pow/network-report-sanitize.json" \
  python3 tests/pow_network.py
python3 tools/quantum_circuit_estimate.py --t-layer-ns 1000 \
  --magic-states-per-second 100000000 \
  --out artifacts/pow/quantum-circuit-resources.json
```

## Adversarial consensus result

`tests/consensus_adversary.cpp` constructs both forks through the real `Chain`
validator with real mined SHA3 proofs. Genesis time 1000, spacing 10, retarget
interval 4, initial work 4096. Honest timestamps are `1000+floor(40*i/3)`;
private timestamps are `1000+i`. Both satisfy median time and future bounds.

| Profile / branch | Blocks | Accepted cumulative work | Sum of square roots of block work | Wins |
|---|---:|---:|---:|---|
| Retarget / honest | 16 | 65536 | 1024 | No |
| Retarget / private | 8 | 143360 | 960 | Yes |
| Fixed / honest | 16 | 65536 | 1024 | Yes |
| Fixed / private | 8 | 32768 | 512 | No |

The cost column is a Grover query proxy, with common constants suppressed. It is
not measured elapsed time, a race success probability or a demonstrated live
quantum attack. Targets in the retargeting private fork increase work 4x at
heights 4 and 8, within the existing clamp. This establishes a concrete mismatch
between the fork score and the assumed search cost, not a complete attack model.
The selected branch is replayed into a fresh chain and its state hash checked.

This follows the mechanism in Bailey and Sattath, [51% Attack via Difficulty
Increase with a Small Quantum Miner](https://arxiv.org/html/2403.08023v2), including
bounded adjustments in section 2.3. Their main setting has a quantum attacker and
classical honest miners. We do **not** import its security claims into an
all-quantum network: relative hardware, interruption policies and tie behavior
need separate analysis. The native counterexample is our narrower result.

New development configurations commit `interval=0`, requiring the same initial
target for every block. This removes this timestamp-driven target-boost mechanism
at the cost of losing automatic block-time regulation. It does not solve general
quantum consensus. Existing configurations, bytes, chain identities and fork rules
remain unchanged. `init ... --experimental-retarget` explicitly creates the old
interval-8 research profile; no old data is migrated. Old software rejects the
new zero-interval profile; this is not a rolling network upgrade.

Do not replace cumulative work with square-root scoring without analysis:
classical/mixed-device incentives, splitting work across easy blocks, timestamp
constraints, network delay and adaptive adversaries change the security model.

## Circuit-derived resource accounting

`tools/quantum_circuit_estimate.py` uses the optimized Keccak permutation counts
in Amy et al., [Estimating the cost of generic quantum pre-image attacks on
SHA-2 and SHA-3](https://www.mathstat.dal.ca/~mamy/Papers/breakingsha.pdf), Table 2.
For a forward permutation: 499200 T gates, T-depth 432, depth 11040 and 3200
logical qubits. These are published circuit results, not optimality bounds.

Our header is 268 bytes, SHA3-256's rate is 136 bytes, and the last 40 bytes are
the variable nonce. The first permutation has a constant input for a fixed block
template and can be precomputed. The second is variable. Resource accounting
therefore includes one forward and one inverse variable permutation, an explicit
256-bit threshold comparator and diffusion over 320 nonce bits. Comparator
compute/uncompute uses `2*(256+popcount(target))` Toffolis; diffusion uses 636.
A seven-T decomposition and a conservative serial schedule cost these additions.
The logical workspace budget is 4096 including separately budgeted scratch/input.
The C++ regression checks header size; accounting tests check formulas.

This is **literature-derived accounting**, not a measured full mining oracle.
Binding that optimized schedule to an actual quantum compiler remains necessary.
The separate `miners/reversible_sha3.py` trace-preserving reference now tests
FIPS-202 absorption/padding, bit order, threshold phase and inverse cleanup on
classical basis states. Small comparator components are phase-tested with Qiskit
statevectors. That reference is qubit-heavy and does not validate the optimized
resource counts above. `miners/quantum_simulator.py` runs small Grover circuits
with a classically precomputed SHA3 truth table; it is not a full-hash quantum
simulation or evidence of quantum mining advantage. Hardware parameters in the saved JSON
are illustrative. The timing is a floor for this selected schedule under those
parameters; it is not a lower bound on all attacks or an achievable benchmark.
It omits Clifford latency, routing, QEC and factory overhead. Physical qubit counts
are deliberately withheld: code distance and total failure budget must be chosen
for each complete run. Full-preimage physical estimates from the paper cannot
simply be applied to this low-difficulty mining puzzle.

The previous `quantum-feasibility.json` remains an older illustrative-rate model;
`quantum-circuit-resources.json` is the newer accounting report. Neither measures
security against a competing, growing honest chain. A total network cost,
energy budget, hardware availability assumption and stochastic adversarial race
are still required to answer production tractability.

## Checks performed

Seven CTest suites passed in both release and ASan/UBSan builds. Both native
peer-network regressions passed (release and sanitized), including signed transfer
replay rejection, restart, multi-page competing fork synchronization, rollback,
classical-only TLS rejection, changed-peer pin rejection and journal corruption.
CLI checks verified fixed/experimental configuration separation; the original
56-block demo retained its exact tip and state. Clang analyzer checks reported no
diagnostics across `pow.cpp`, `pow_main.cpp`, `crypto.cpp`, `ledger.cpp`, `app.cpp`.

A seeded Clang libFuzzer parser run completed 1,151,991 inputs in 61 seconds with
ASan/UBSan and no reported crash. This is a short smoke campaign, not exhaustive
coverage. The harness covers config, block and transfer decoding and canonical
round trips; it does not fuzz the TLS peer state machine or crash injection.
OpenSSL itself was linked from its optimized static build and was not sanitizer
instrumented. Build, network, static-analysis and fuzz evidence are included.

To repeat the bounded parser campaign:

```bash
cmake -S . -B build-fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ \
  -DOPENSSL_ROOT_DIR="$PWD/.deps/openssl" -DOPENSSL_USE_STATIC_LIBS=ON \
  -DBUILD_TESTING=OFF -DPQCOIN_FUZZ=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build build-fuzz --target pqcoin-parser-fuzz --parallel 4
mkdir -p .deps/fuzz-corpus
# Seed mode byte 0 + encoded config, 1 + encoded block, 2 + encoded transfer.
./build-fuzz/pqcoin-parser-fuzz .deps/fuzz-corpus -max_total_time=60 \
  -timeout=5 -max_len=1048577 -artifact_prefix=.deps/
clang-tidy -p build src/pow.cpp src/pow_main.cpp src/crypto.cpp \
  src/ledger.cpp src/app.cpp --checks='-*,clang-analyzer-*'
```

## Findings and disposition

| ID | Priority | Finding | Disposition |
|---|---|---|---|
| C01 | Critical for a quantum consensus claim | Timestamp retargeting rewards cumulative work faster than ideal Grover cost | Reproduced; new devnets fixed target, old profile explicitly experimental; general proof outstanding |
| C02 | High, availability | Full account/reward snapshot per block and retention of all valid forks allow memory growth; sync scans history | Open; require bounded persistence/indexing and adversarial fork/large-state load tests before public exposure |
| C03 | High, availability | Single inbound TLS worker and no per-origin admission budget allow repeated handshake/frame stalls | Open; deadlines limit each stall, not a distributed flood; require concurrency limits, quotas and DoS tests |
| C04 | High, operational | Private wallet PEM is unencrypted; no production backup/recovery or hardware signer | Open; owner-only permissions help local isolation but do not replace wallet security |
| C05 | Medium, identity | TOFU permits first-contact impersonation without independently obtained fingerprint | Pre-pinning supported/tested; bootstrap/discovery policy remains open |
| C06 | Medium, availability | Peer certificate generated at startup expires after 24h | Open; restart currently required; implement tested renewal before persistent deployment |
| C07 | Medium, storage | Journal is capped at 512MiB, replays all forks, and has no production compaction strategy | Open; checksums, torn-tail recovery and fail-closed corruption tested |
| C08 | Protocol research blocker | Quantum withholding, measurement/tie synchronization, resource concentration, dynamic difficulty and network races | No general proof; fixed target addresses only C01 mechanism |

C02/C03 are architectural exposure findings, not claims of an executed remote
exploit. Priority reflects public-network deployment, not the isolated local demo.
No new memory-safety finding was established by the bounded checks. Static
analysis, fuzzing and passing tests cannot certify their absence.

## Independent reviewer acceptance criteria

The public-source archive is generated by `tools/package_audit.py`; its embedded
manifest pins every included file. Private keys, homes, journals and dependencies
are excluded. Source is currently uncommitted, so the archive manifest, rather
than a fictitious commit hash, identifies the review snapshot. Reviewers must
rebuild pinned OpenSSL and check upstream advisories at review time.

An external reviewer should reproduce C01 and verify legacy replay, evaluate the
full-quantum threat model and fork choice formally, compile/verify a mining oracle
and fault-tolerance model, independently review signature/key/AEAD composition,
fuzz peer frames and storage crash paths, and exercise C02/C03/C06 under hostile
load. Require a written report, severity triage, remediation and retest before any
production claim. No independent report exists yet; the handoff package is not
an audit certificate.
