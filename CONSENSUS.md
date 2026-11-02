# Earlier permissioned BFT experiment

The current permissionless implementation is documented in [POW.md](POW.md).
This fixed-roster CometBFT experiment is retained as a reference, not the target
consensus for the decentralized currency.

`pqcoind` runs the pinned, unmodified CometBFT consensus engine with a C++23
application behind its in-process ABCI interface. The Go executable hosts the
engine and a small C ABI adapter; cryptographic transfer verification, account
execution, canonical state hashing and durable application storage are native C++.
This deliberately reuses the reference implementation's consensus rather than
introducing a new port of its safety-critical round and locking rules.

## Run the tested four-validator development network

```bash
cd /home/julian/Documents/Organization/pqcoin
./tools/build-node.sh
python3 tests/consensus_network.py
cat artifacts/consensus-local/report.json
./build/pqcoind verify-commit \
  --genesis artifacts/consensus-local/genesis.json \
  --commit artifacts/consensus-local/commit.json
```

The automated test starts four separate processes on loopback TCP sockets, then
stops them on completion. It retains private runtime data under a unique ignored
`.deps/consensus-*` directory and writes public results/certificates under
`artifacts/consensus-local`. The generated fixture has a **separate test-chain
allocation of 1,000,000 integer units**, whose signing keys exist only in memory.
It does not alter or allocate funds in the previously mined zero-supply genesis.

For an ongoing zero-supply network anchored to that original bootstrap hash:

```bash
./build/pqcoind empty-genesis \
  --bootstrap-hash 0000543d663261cbbf2a622612e1c4ce26b9a825e29c3a005ddf1369b1a391b6 \
  --out .deps/empty-app-genesis.bin
./build/pqcoind init --root .deps/devnet \
  --app-genesis .deps/empty-app-genesis.bin --validators 4 --base-port 28650
python3 tools/run-devnet.py --network .deps/devnet
```

On this computer, `.deps/devnet` is already initialized and has committed block 1
with zero issued units. Its public genesis and verified certificate are saved in
[`artifacts/consensus-empty/`](artifacts/consensus-empty/). Run only the final
`run-devnet.py` command above to resume that existing network.

Initialization refuses existing output paths. Restart with the same `run-devnet`
command; never regenerate or delete validator signing state to restart a node.
RPC ports are 28651, 28653, 28655 and 28657; P2P ports are the preceding even
numbers. `config/runtime.json` is the authoritative port/peer configuration;
the stock TOML emitted by upstream initialization is not read by this launcher.
All endpoints bind to loopback. Ctrl-C stops the foreground network.

## Consensus and identity

The validator set is fixed at genesis, has 4–16 equal-power members and permits
only ML-DSA-65 (`ml_dsa_65`) voting keys. CometBFT supplies authenticated proposals,
prevotes, precommits, round changes, locks, evidence handling, block sync and
its write-ahead log. Its file validator persists signing state before releasing
a signature. The launcher also takes an exclusive OS lock on each node home,
refuses missing signing files and never silently generates replacement keys.

CometBFT requires strictly more than two-thirds of voting power to commit; with
four equal validators this is three votes. Safety/liveness assumptions are those
of the reused protocol: less than one-third Byzantine voting power, with liveness
requiring eventual synchrony. Two unavailable validators correctly stop this
four-node network. Validator membership changes and economic staking/slashing
are intentionally unavailable in this application.

Post-quantum scope: validator and transaction signatures use ML-DSA-65. The
stock CometBFT peer transport still uses conventional node identities and key
exchange; it is **not** the proposed ML-KEM hybrid transport. This development
network therefore does not claim post-quantum transport confidentiality. ML-KEM
payment notes remain available in the native library; this first wire format
carries their signed commitment, not the encrypted envelope itself.

The previous SHA3 proof-of-work block is preserved as a bootstrap hash in the
native genesis configuration. CometBFT has its own genesis document, validator
set, chain ID and block format; its blocks begin at height 1. It is a development
BFT chain anchored to that artifact, not a Bitcoin-compatible PoW continuation.
The exact CometBFT genesis document and its validator set are the trust anchor
for `verify-commit`; that command checks the header, its validator commitment,
the signed block identity, chain identity and the >2/3 signature certificate.
It is a single-height certificate verifier, not a full light-client sync protocol.

## Deterministic C++ application

Transfer **v2** adds a 32-byte chain identity to the previous signature input.
It is SHA3-256 of the canonical native application genesis, binding the bootstrap
hash, unique development chain name and initial account allocations. Version 1
prototype transfers are not accepted; no persisted transfer history existed to
migrate. The canonical body is 179 bytes, followed by the 32-byte message
commitment, 1952-byte public key and 3309-byte signature: exactly **5472 bytes**.
There is no permissive Protobuf transaction decoding or acceptance of trailing
bytes. Existing crypto and ledger rejection rules still apply.

All ABCI calls share a mutex. `CheckTx` executes against a copy of committed
state. `PrepareProposal` filters sequentially against trial state;
`ProcessProposal` rejects any invalid sequence, including duplicate nonces and
aggregate overspends. `FinalizeBlock` creates a staged snapshot and deterministic
transaction results. Only `Commit` persists that staged snapshot; intervening
mempool or proposal calls cannot replace it. Restart reports the last durable
height and hash so CometBFT can perform its handshake/replay.

State includes the application genesis commitment, height and a lexicographically
sorted map of `(address[32], balance u64, nonce u64)`, with explicit big-endian
encoding and domain labels. Snapshots include a SHA3-256 corruption checksum and
are written with file fsync, atomic rename and directory fsync. Storage failure
returns an ABCI error rather than acknowledging a commit. This bounded snapshot
store is used instead of adding RocksDB before the application needs it.

Limits: 4096 accounts, 128 transactions and 1 MiB raw transaction bytes per
block, 5472 bytes per transaction, and 8 MiB of mempool bytes. State totals and
account arithmetic reject overflow. Fees are burned in this prototype. There
are no rewards, inflation transactions or validator-set updates.

ABCI `/account` queries take a 32-byte address and return decimal-string balance
and nonce. `/state` returns the current committed height/hash. Historical queries
and proof requests fail explicitly; authenticated account inclusion proofs,
state-sync snapshots, encrypted wallet persistence, operational key isolation,
and public-network deployment are not implemented.

## Validation and reuse

The local test exercises four-node block and application-hash agreement, real
3309-byte commit signatures, offline certificate verification, a forged
certificate, signed transfers, replay/foreign-chain/malformed transaction
rejection, one-validator crash tolerance, two-validator quorum loss, catch-up,
and full network restart with retained balances/nonces.

C++ Release and ASan/UBSan tests cover canonical decoding, signature field
binding, nonce/balance rules, staging versus durable commit, wrong-chain state,
and corrupt snapshots. Go tests cover ML-DSA vote verification and persistent
anti-double-sign/replay protection. Selected upstream tests additionally cover
Byzantine prevote equivocation, conflicting proposals with partition, lock
safety and previous-round commits. The Go engine/OpenSSL dependency are not
instrumented by the C++ sanitizer build.

CometBFT revision and original Apache-2.0 notices are recorded in
`reference-sources.json` and `third_party/cometbft/`. `node/go.sum` pins the module
dependency graph; `tools/build-node.sh` checks the reference revision and tracked
source cleanliness. Go 1.26.10 Linux amd64 bootstrap is checksum-pinned and
isolated under `.deps/`. The reference checkout remains unmodified.

This is a tested local consensus integration, not a production security audit or
a public cryptocurrency launch. Four processes on one host exercise process and
network paths, but not independent-machine or wide-area fault behavior.
