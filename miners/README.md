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

The backend contract and independent nonce-verification path are implemented and
tested. **A reversible SHA3 threshold oracle, QPU circuit, provider integration
and successful quantum-hardware mining run are not supplied or claimed.** A QPU
backend must implement the full canonical-header hash/target predicate; searching
a toy oracle or classically precomputing winning nonces is not a quantum mining
implementation. See the resource-model limitations in `SECURITY_REVIEW.md` and
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
