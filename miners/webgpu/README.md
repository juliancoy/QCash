# QCash WebGPU mining

A reusable browser implementation of the measured native architecture: persistent
pipelines and buffers, GPU-side first-block prefix preparation, exact paired-u32
Keccak, and final-round pruning to the four SHA3-256 output lanes. Full 24-round
output remains available with `pruned: false` for correctness/performance comparisons.
No subgroups, shader-u64 extension or assumed warp size are required.

Normal search returns a winning index. Every winner is independently hashed with
the included BigInt CPU SHA3 implementation and checked against the header target
before it is returned. Normal unsuccessful candidates never run on the CPU.
This library does not establish chain identity, authenticate a node or submit work;
the existing pinned native controller/full node owns those checks.

## Run the explicit-start browser tool

From the repository root:

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory miners
```

Open `http://localhost:8080/webgpu/` in a WebGPU-capable browser. Load a **public** 272-byte
empty-transaction work file exported by `qcash work`, choose a bounded range, and
choose WebGPU or a [SIMD WASM worker pool](../wasm/README.md), and press **Scan nonce range**. Nothing mines on page load or file selection. Download
an independently verified proof, then submit it using your pinned native client:

```sh
./build/qcash submit CLIENT_HOME PINNED_PEER qcash-verified-work.bin
```

This standalone tool supports empty-transaction work only. It does not accept
wallet secrets or session tokens and has no remote submission endpoint. Browser
support requires HTTPS or localhost; unsupported contexts fail explicitly.
Cancel closes the owned GPU session when work is in flight; the next scan creates
a new session. It cannot guarantee immediate preemption of a submitted GPU batch.

## Reuse the module

```js
import {createWebGPUMiner} from './miner.mjs';
const miner = await createWebGPUMiner({workgroup: 64});
try {
  await miner.prepare(publicHeader268Bytes);
  const result = await miner.run(0n, 1_048_576, {signal: abortController.signal});
  if (result.proof) {
    // result.proof.header is the unchanged job header with its verified nonce.
    // Keep chain/reward/tip checks in your authenticated work controller.
    console.log(result.proof.nonce, result.proof.blockId);
  }
} finally {
  miner.dispose();
}
```

`prepare` replaces the public template and computes its prefix once on the GPU.
The session serializes operations by rejecting concurrent callers. Buffers and
bind groups are reused. `run` accepts a BigInt nonce and 1–16,777,216 candidates,
rejecting overflow. The default dispatch size is 1,048,576; up to 16 such dispatches
share one submission and one winner readback for a 16M-candidate range. Smaller
aligned `dispatchSize` and `maxBatch` values can bound cancellation latency; at
most 64 grouped dispatches are allowed. Workgroups 32/64/128/256 are supported and
checked against adapter limits. Runtime queue/validation/device errors fail the
session, with a per-operation timeout (default 10 seconds). Dispose is idempotent.

`capture: true` is debug-only and limited to 4,096 digests. It selects a distinct
pipeline specialization; normal mining eliminates the capture code. Debug output
is copied sequentially and decoded outside mapped GPU memory.

## Data movement and timing

WebGPU requires separate MAP_READ staging: it cannot expose a simultaneously
GPU-storage/CPU-mapped zero-copy buffer. Prefix state remains GPU-resident. Uploads
are 272 bytes per template and 16 bytes per dispatch. Normal result readback is
4 bytes, plus 16 optional timestamp bytes per submitted range. Debug capture adds
16 header bytes and 32 bytes per digest. These are API payloads, not physical
VRAM/cache/PCIe measurements. WebGPU does not expose allocation heap placement.

Optional `timestamp-query` measures the mining pass, excluding prefix preparation,
reset and readback. Without it, `gpuSeconds` is null and only four result bytes are
mapped. Wall time includes submissions, waits, decoding and independent winner
verification. The straightforward BigInt verifier prioritizes auditability; a
known-winning 256-nonce request took about 2.7 ms in the recorded Chrome run.
The native node still validates the entire block.

## Verification and measured performance

See the [recorded comparison](../../benchmarks/README.md). On the RTX 3060, the
selected `final32/64` browser configuration reached **668.4 MH/s** full wall and
**700.5 MH/s** GPU intervals, versus native **678.6 / 713.1 MH/s** in eleven
alternating confirmation pairs. These are same-host observations, not universal
performance guarantees. The browser and driver compiler determine actual register
allocation; native register/occupancy measurements do not transfer to WGSL.

```sh
node --test tests/webgpu_reference_test.mjs
python3 tools/test_webgpu_mining.py --output benchmarks/my-browser.json
python3 tools/test_webgpu_ui.py --output benchmarks/my-browser-ui.json
python3 tools/plot_webgpu_mining.py benchmarks/my-browser.json --output benchmarks/my-browser.png
```

The hardware runner uses installed host Chrome and Python Playwright, opens a
local browser window, tests eight browser configurations, and saves full receipts
and source hashes. Headless hardware Chrome exposed no adapter on this host.
`--software` is explicitly correctness-only and never yields hardware throughput.
The browser-to-node test server binds loopback and serves the public miners directory; the node acceptance
test creates an isolated temporary node and pins its peer key.
