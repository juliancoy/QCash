# QCash: permissionless proof of work in a quantum-computing world

The current development direction is native C++23 proof of work, with no fixed
validator roster, central identity provider or key-access server. The earlier
Go/CometBFT network is retained as a separate reference experiment; it does not
participate in this PoW chain. This is a new development protocol, not a migration
of CometBFT blocks and not Bitcoin wire-compatible software.

**Security review:** [SECURITY_REVIEW.md](SECURITY_REVIEW.md) records a native
reproduction of the retarget/fork-work cost mismatch. New chains use fixed
difficulty (`interval=0`); old chains keep their committed rules. Neither
profile has an established all-quantum consensus security proof.

## Build and exercise it

```bash
cd /home/julian/Documents/Organization/pqcoin
# Run this once if the local OpenSSL dependency is not already installed:
./tools/bootstrap-openssl.sh
cmake --preset local
cmake --build --preset local --parallel 4
ctest --preset local
python3 tests/pow_network.py
```

A persistent demonstration chain and wallet on this computer are recorded in
`artifacts/pow/demo.json`. To inspect or extend it while its node is stopped:

```bash
./build/qcash status .deps/pow-demo
./build/qcash balance .deps/pow-demo ADDRESS_FROM_DEMO_JSON
./build/qcash mine .deps/pow-demo ADDRESS_FROM_DEMO_JSON 1
```

Create a new fixed-difficulty development network (different chain ID from the legacy demo):

```bash
./build/qcash init .deps/my-pow-node artifacts/devnet-genesis/genesis.bin
./build/qcash wallet .deps/my-wallet.pem
./build/qcash identity .deps/my-pow-node
./build/qcash node .deps/my-pow-node 127.0.0.1:29601 \
  --peer 127.0.0.1:29600
```

To provide the other peer at port 29600 in a separate terminal:

```bash
./build/qcash init .deps/my-pow-peer artifacts/devnet-genesis/genesis.bin
./build/qcash node .deps/my-pow-peer 127.0.0.1:29600
```

Add `--mine-to YOUR_ADDRESS` to a node command to mine continuously; Ctrl-C stops
it. Without that flag it only validates and synchronizes. Full nodes need no
wallet and no mining hardware. Newly initialized nodes have no funds. `init`,
`wallet`, and transaction-file creation refuse existing paths.

A wallet key and a node transport key are separate ML-DSA-65 keys. Private PEM
files are currently **unencrypted development keys**, protected by owner-only
permissions. The experimental browser wallet supports encrypted seed backups;
production recovery and hardware isolation remain work for a real wallet. Never publish or commit `.deps/` or these keys.

To sign a transfer after rewards mature (integers are atomic units):

```bash
./build/qcash sign .deps/pow-demo .deps/pow-wallet.pem \
  RECIPIENT_ADDRESS AMOUNT FEE CURRENT_NONCE .deps/payment.bin
./build/qcash mine .deps/pow-demo MINER_ADDRESS 1 .deps/payment.bin
```

A running node holds an exclusive home lock. Offline `mine`, `sign`, `balance`
and `status` use the same lock. For running-node status use
`./build/qcash query CLIENT_HOME HOST:PORT` instead. The optional testnet wallet gateway now provides live balance/history queries and
a bounded transaction-gossip queue; see `../OrgPortal/docs/finance/QCASH_TESTNET.md`.
Offline mining can also feed a signed transfer file directly
to the local miner. The block-validation library supports up to 128 transfers.

## Consensus rules

A chain's identity is SHA3-256 of its canonical configuration, which commits to
the previously mined bootstrap block hash, genesis timestamp, target limits,
initial target, target spacing, retarget interval, subsidy, maturity and halving
interval. There is no validator list. All nodes using the same configuration
independently enforce the same rules, and transfer signatures bind this identity.
The original bootstrap artifact is preserved and still has zero issuance. Its
hash is an anchor in the new configuration; the new protocol's height-zero root
is the configuration commitment, not the old block under a different encoding.

* Blocks use SHA3-256; their big-endian hash must be **at most** the expected
  256-bit target. Hashing cost and mining difficulty are separate parameters.
* The canonical header contains the zero-terminated `pqcoin/pow-block/v1` label,
  chain identity[32], parent[32], u64 height, u64 timestamp, target[32], miner[32],
  transaction commitment[32], state commitment[32], extra nonce[32], and u64
  nonce. Integers are big-endian. The 320-bit combined nonce space avoids a
  64-bit nonce ceiling for studies of difficult puzzles.
* Body encoding is u32 transaction count followed by exactly 5472 bytes per v2
  signed transfer; trailing bytes, over 128 transfers and oversized blocks are
  rejected. The transaction commitment hashes a domain-separated ordered list
  of transaction lengths and SHA3-256 digests, not a Bitcoin Merkle tree.
* Each block must reference a known valid parent at the previous height. Its
  timestamp must exceed the median of up to 11 preceding timestamps and be no
  more than two hours ahead of local time. Unknown-parent blocks are not stored;
  ancestor-first sync retrieves them before submission.
* With `interval=0`, every block must use the committed initial target; timestamp
  changes cannot increase fork weight. Automatic block-time adjustment is disabled.
* In the explicitly experimental nonzero-interval profile, at each retarget boundary, target is multiplied by elapsed/expected time using
  exact integer arithmetic. Elapsed time is clamped to one-quarter through four
  times the target span; target is bounded by the chain's maximum and at least 1.
  The rule follows Bitcoin's window-style retarget approach, using full-width
  targets rather than Bitcoin's compact encoding. Timestamp manipulation and
  retarget economics still require adversarial review before public deployment.
* Fork choice is greatest cumulative `floor(2^256/(target+1))`, using arbitrary
  precision arithmetic. Equal-work forks retain the first seen tip. This is a
  conventional work score, **not a measurement of quantum gate cost**. It needs
  dedicated analysis for quantum mining strategies and variable difficulty.
* All transfers execute in order after this block's matured rewards are released.
  Amounts, nonces, identity binding, signatures and arithmetic are validated.
  Fees are removed from the payer and added to the miner's immature reward.
* Subsidy is `initial_reward >> floor((height-1)/halving_interval)`, becoming zero
  after 64 halvings. Reward plus fees becomes spendable at `height + maturity`.
  The miner cannot mint an arbitrary amount: nodes derive it from these rules.
* Reorganizations select the winning branch's complete account/reward state,
  rolling back transfers, nonces and immature rewards from the losing branch.
  Detached/attached block IDs are exposed by the chain library.

The deliberately easy **development configuration** has a fixed 12-bit work
requirement (4096 expected classical hashes), `interval=0`, two-block reward
maturity, reward of 50 atomic units and a 210000-block halving interval. Spacing
remains committed metadata; it does not regulate fixed-target block production.
These parameters do not constitute a production security budget.

`init HOME BOOTSTRAP --experimental-retarget` opts into the earlier eight-block
retarget profile. Existing nonzero-interval configurations still replay unchanged;
there is no automatic migration and peers with different chain IDs cannot sync.
The persistent `.deps/pow-demo` remains an earlier retargeting chain. The network
regression now tests fixed difficulty; adversarial/unit tests cover both profiles.
Fixed difficulty removes the reproduced timestamp-driven difficulty increase,
not quantum mining advantage, withholding, synchronization attacks or economic
centralization. Deployment remains blocked on protocol review and capacity work.

## Node-to-node protection

Peer sockets require TLS 1.3, AES-256-GCM, X25519MLKEM768 hybrid key exchange and
ML-DSA-65 handshake signatures/certificates. Classical-only negotiation is
rejected. This uses native OpenSSL 3.5.9, without a custom key-exchange protocol.
Self-signed peer credentials prove key possession; they do not grant membership,
mining privileges or authority over transactions. Every received block is still
validated locally. Node identities use separate persisted ML-DSA keys. Certificates currently
expire 24 hours after startup; restarting issues a new certificate for the same
persisted key and preserves its fingerprint. Automatic renewal remains work.

Outbound connections pin each endpoint's peer public-key fingerprint. First use
is trust-on-first-use; it does not authenticate the initial endpoint against an
active impersonator. To bootstrap with an independently obtained fingerprint:

```bash
./build/qcash trust CLIENT_HOME HOST:PORT PEER_FINGERPRINT
```

`identity HOME` displays that peer's fingerprint. Existing different pins are
rejected rather than silently replaced. Pinning is a local peer-choice mechanism,
not a shared allowlist or central CA. Incoming peers may use any valid ML-DSA
identity. Key rotation must be coordinated with each peer's stored pin.

The development protocol currently uses numeric IPv4 endpoints and configured
seed peers. It pages blocks using ancestor locators, including partial competing
branches, with bounded frame sizes and TLS I/O deadlines. Server I/O runs apart
from outbound synchronization to avoid cyclic connection stalls. Mining checks
for tip changes between bounded hashing batches. Automatic peer discovery,
transaction relay, connection pools and public-network DoS defenses remain work.

## Persistence and scalability

Each home is exclusively locked. Accepted blocks and their checksums are appended
to a journal and fsynced before success is acknowledged. Startup replays all valid
branches, preserving first-seen tie behavior and cumulative-work fork choice.
An incomplete final append is truncated to the last complete record. Corruption
of a complete record fails closed. Storage write failure stops the daemon rather
than continuing with an undurable best chain.

This first implementation retains an in-memory state snapshot for each indexed
block and replays a bounded (512 MiB) journal. That makes reorg behavior easy to
inspect and test, but is **not suitable for a long-lived public blockchain**.
Undo records, indexed disk storage, pruning/checkpoints, scalable account storage
and transactional persistence are required before that use. Snapshots are not a
claim of an optimized database backend.

## Quantum feasibility model

```bash
python3 tools/quantum_pow_model.py \
  --iteration-rate 1000000 --processors 16 --attacker-processors 4 \
  --coherence-seconds 600 --logical-qubits 20000 --physical-per-logical 1000 \
  --budget-seconds 600 --out artifacts/pow/quantum-feasibility.json
```

Every hardware number in this example is an **illustrative supplied assumption**,
not a measured device capability, forecast or derived SHA3 circuit estimate.
The iteration rate must already include reversible hashing, uncomputation,
diffusion and error-correction overhead. No quantum computer was used.

For effective difficulty d, a hash succeeds with probability 2^-d. For k Grover
iterations, the model uses `sin²((2k+1)*asin(sqrt(2^-d)))`. It combines independent
processor success probabilities and repeats unsuccessful runs, capped by the
coherent-run time budget. Its selected run length is a baseline strategy, not a
proof of an attacker's optimal strategy. Setup, reset and network latency are
excluded. Logical-to-physical qubit conversion is a supplied multiplier.

The report provides expected time versus a budget, a fixed-difficulty private
suffix estimate, and an expected-value trajectory of bounded retarget feedback.
The suffix estimate does **not** race an advancing honest chain or establish a
reorganization probability. Retargeting cannot help until enough blocks arrive
to complete an epoch. A forecast that starts with an intractable epoch is not a
working deployment plan. These calculations help reject infeasible parameters;
they do not establish a secure production target or quantum economic security.

Under the example's assumptions, effective difficulty 48 takes about 3.58 seconds
on average, while 64 takes about 836 seconds with the specified coherent-run cap.
Those figures change with the assumptions. Neither is a recommendation to set
this development chain to that difficulty.

## Evidence

* `artifacts/pow/network-report.json`: Release multi-process TLS synchronization,
  independently mined forks, signed spending, replay rejection, key-pin mismatch,
  classical-only TLS rejection, paged fork adoption, restart, reward/spend rollback,
  incomplete-tail recovery and complete-record corruption rejection.
* `artifacts/pow/network-report-sanitize.json`: the same network checks against the
  ASan/UBSan binary.
* CTest: existing crypto/genesis/application tests, PoW validation/retarget/reorg
  cases, nonce rollover, and five resource-model scaling/precision checks.

Bitcoin reference revision is recorded in `reference-sources.json`; the native
PoW code is newly implemented, informed by its retarget/work rules. The code does
not import Bitcoin's UTXO model or claim Bitcoin's security history.

Research basis for the resource model:
https://pmc.ncbi.nlm.nih.gov/articles/PMC8946996/ .
The implementation establishes a permissionless development chain and an explicit
quantum-resource model. It is not an audited cryptocurrency or proof that quantum
attackers cannot reorganize it.
