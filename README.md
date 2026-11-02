# QCash — permissionless proof-of-work development chain

For conventional miners and custom accelerator integrations, see the
[mining implementation guide](miners/README.md). It includes a bounded CPU
controller and a hardware-neutral work/nonce verification interface. A complete
quantum-hardware mining backend is not supplied.
An optional [local QPU simulator](miners/README.md#local-qpu-simulation) executes
small Grover circuits with an explicitly classically precomputed oracle. Full
reversible SHA3 reference components are separately tested on basis states.

The current direction is a native C++23 SHA3 proof-of-work chain with ML-DSA
transactions, hybrid ML-KEM peer transport, cumulative-work fork choice and a
quantum-resource feasibility model. See **[POW.md](POW.md)** for mining, peers,
tests, assumptions and development limits. No fixed validator set is required.
**Security status:** [review and audit handoff](SECURITY_REVIEW.md). New devnets
use fixed difficulty after reproducing a retargeting weakness; this is not an
audited or proven quantum-secure currency. Existing chain rules are preserved.
The CometBFT material below describes the earlier, separate BFT experiment.


A C++23 cryptographic foundation and a local CometBFT consensus integration: create ML-DSA-65 / ML-KEM-768 keys, encrypt a
payment note to a recipient's public key, sign and verify the transfer, decrypt
the note, and apply a checked in-memory account transfer. No private keys are
printed or written to disk.

## Build and run

Requirements: GCC 13+ (or equivalent C++23 compiler), CMake 3.25+, Ninja, Perl,
Make, curl and a SHA-256 utility. The bootstrap pins OpenSSL 3.5.9 LTS and its
release SHA-256 from the official upstream release. It installs under `.deps/`
and does not replace system OpenSSL. Compilation defaults to four jobs; set
`PQCOIN_BUILD_JOBS` to change that limit.

```bash
cd /home/julian/Documents/Organization/pqcoin
./tools/bootstrap-openssl.sh
cmake --preset local
cmake --build --preset local
ctest --preset local
./build/pqcoin-demo

cmake --preset sanitize
cmake --build --preset sanitize
ctest --preset sanitize
```

Alternatively, with vcpkg installed and `VCPKG_ROOT` set, use
`cmake --preset vcpkg`, `cmake --build build-vcpkg`, and
`ctest --test-dir build-vcpkg --output-on-failure`. The registry baseline is pinned.
That alternative build has not been exercised here. The default manifest only
installs OpenSSL; optional `node` and `test-tools` features reserve the proposed
stack without making the crypto milestone depend on unused services.

## Protocol in this milestone

All labels below are ASCII followed by a single zero byte. Integers are unsigned
fixed-width big-endian values. Fields have no padding or platform representation.

`body = "pqcoin/transfer/v2" || u32(version) || u32(network) || sender[32] ||
recipient[32] || u64(amount) || u64(fee) || u64(nonce) || recipient_kem_key[32] || chain_id[32]`

The body is exactly 179 bytes. Version is 2, network is `0x50515431` (PQT1).
Addresses are SHA3-256 of the label `pqcoin/address/ML-DSA-65/v1` followed by the
1952-byte ML-DSA-65 public key. The chain identity is described in CONSENSUS.md.
The sender's address authenticates its included
public key. Signatures are 3309 bytes and cover `body || message_commitment[32]`.

The envelope is label `pqcoin/payment-envelope/v1`, the 1088-byte ML-KEM-768
ciphertext, a random 12-byte GCM nonce, a u32 ciphertext length, ciphertext
(maximum 65536 bytes), and a 16-byte tag. `message_commitment` is SHA3-256 of this
encoding. Recipient key commitment is SHA3-256 of the 1184-byte ML-KEM public key.

ML-KEM establishes a fresh 32-byte shared secret. HKDF-SHA3-256 derives a 32-byte
AES key with salt `SHA3-256(body || kem_ciphertext)` and info
`pqcoin/payment-key/v1` (without a trailing zero). AES-256-GCM authenticates the
body as associated data. Sender signs after encryption; changing either body or
envelope then invalidates the signature. Decryption checks the signature, key
commitment, envelope commitment and GCM authentication before returning plaintext.
This is an experimental application format, not a claim of HPKE interoperability.
The shared secret and derived AES key are wiped on scope exit. Returned plaintext
is caller-owned memory and must be cleared by the wallet when appropriate.

Amounts use integer units. Zero-value transfers and self-transfers are rejected.
`apply` validates identities, balance, fee arithmetic, recipient overflow, and the
sender's exact next nonce before any mutation. Nonces start at zero; exhausted
nonce space rejects transfers. The prototype removes fees from circulation.
These are documented demonstration rules, not a finalized monetary policy.
Concurrent account access requires caller synchronization; persistent atomicity
and crash recovery are not implemented.

The sender must obtain an **authenticated** recipient address/ML-KEM-key binding
out of band. The demo owns both wallets and supplies this binding locally. Key
rotation, signed key directories, encrypted keystores, backup/recovery, malicious
peer handling and public-network operation require further work. The local
consensus integration and its bounded wire decoder are described in CONSENSUS.md.
`decrypt_message` authenticates a signed note; ledger acceptance is a separate
operation. The library API is in-process; the consensus application adds a bounded v2 wire decoder.

## Architecture and references

`pqcoin_crypto`, `pqcoin_ledger` and `pqcoin_app` are independently linkable libraries; `pqcoin-demo` is the first
executable. A dependency-light CTest driver exercises positive and adversarial
crypto and account flows in Release and ASan/UBSan configurations. GoogleTest,
libFuzzer and Google Benchmark fit the later parser/network/benchmark milestones.
The OpenSSL dependency itself is optimized and is not sanitizer-instrumented.

Reference sources and pinned revisions are in
[`../crypto-references/README.md`](../crypto-references/README.md) and [`reference-sources.json`](reference-sources.json).
No experimental Vulkan or browser kernels are used for consensus cryptography.

Remaining production design boundaries (see [CONSENSUS.md](CONSENSUS.md)):

1. Review the v2 transaction/state formats and monetary rules; expand parser
   fuzzing and independent interoperability vectors.
2. Add encrypted wallet persistence, authenticated recipient key rotation,
   backups, and authenticated account-state proofs. Evaluate RocksDB when the
   bounded snapshot store needs to scale.
3. Add post-quantum hybrid transport and operational validator key isolation.
   Current CometBFT P2P transport uses conventional key exchange.
4. Define validator admission and economic policy, then test independent hosts,
   wide-area partitions, adversarial traffic and operational recovery before
   value-bearing operation.

The prototype now includes a four-node local BFT network with ML-DSA-65 validator
signatures. See [CONSENSUS.md](CONSENSUS.md) for build/run instructions, the tested
fault scenarios, transport limitations and the Go engine/C++ application boundary.
It is a development system with no real funds or public-network deployment.

Official foundations:
- https://docs.openssl.org/3.5/man7/EVP_SIGNATURE-ML-DSA/
- https://docs.openssl.org/3.5/man7/EVP_KEM-ML-KEM/
- https://www.openssl-library.org/source/
- https://www.rfc-editor.org/info/rfc10024/

## First local development block

The first development genesis has been mined and independently verified. Its
canonical binary header, readable report and independent validation record are
in [`artifacts/devnet-genesis/`](artifacts/devnet-genesis/).

```bash
./build/pqcoin-genesis verify artifacts/devnet-genesis
# To create a separate development genesis, choose a new, nonexistent directory:
./build/pqcoin-genesis mine artifacts/another-devnet
```

This isolated bootstrap experiment uses SHA3-256 with a fixed 16-leading-zero-bit
work requirement. It is not the proposed BFT consensus implementation. Genesis
is height zero, has an all-zero previous hash, no transactions, empty account
state and zero issued units. There is no block reward or wallet allocation.
The binary header is authoritative; `verify` recomputes its hash, checks the
fixed protocol fields and proof of work. JSON files are human-readable reports.

The header uses the zero-terminated domain `pqcoin/dev-genesis/v1`, then
big-endian u32 version, u32 network, u64 height, previous hash[32], empty
transaction commitment[32], empty account commitment[32], message commitment[32],
u64 Unix timestamp, u32 difficulty bits and u64 nonce. Commitments are SHA3-256
of the respective zero-terminated strings `pqcoin/devnet/empty-transactions/v1`,
`pqcoin/devnet/empty-accounts/v1`, and `PQCoin local development genesis`.
A particular chain must pin its exact genesis hash; another mined header creates
a different local chain. Mining refuses to overwrite an existing directory.
