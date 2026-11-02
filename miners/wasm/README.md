# QCash SIMD128 WASM worker pool

A real CPU mining backend: C++17 compiled to WebAssembly with explicit `i64x2`
SIMD, hashing two independent nonces per vector. It caches the first SHA3 block's
Keccak midstate and prunes only unused outputs of round 24, preserving the exact
SHA3-256 digest and consensus rules. Normal scans allocate no per-nonce payload.

The JavaScript module maintains 1–8 persistent Web Workers. Each owns an instance
of the same compiled module and a private, fixed 1 MiB linear memory. Disjoint
nonce ranges require no shared memory, locks or per-nonce messages. This is
parallel WASM using Web Workers, **not Emscripten pthreads**; it needs neither
SharedArrayBuffer nor COOP/COEP headers. No scalar fallback is mislabeled SIMD.
Unsupported WASM SIMD fails explicitly.

## Browser tool

Serve the shared miners directory from the repository root:

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory miners
```

Open `http://localhost:8080/webgpu/`, load a public empty-transaction work file,
and choose **SIMD WASM · 1/2/4/8 workers**. Nothing starts until Scan is pressed.
WASM selection makes no WebGPU adapter request. Download a verified solution for
submission through your pinned native client, following the browser tool guide.

## Module API

```js
import {createWasmMiner} from './wasm/miner.mjs';
const miner = await createWasmMiner({threads: 4});
try {
  await miner.prepare(publicHeader268Bytes);
  const result = await miner.run(0n, 1_048_576, {signal: abortController.signal});
  if (result.proof) console.log(result.proof.nonce, result.proof.blockId);
} finally {
  miner.dispose();
}
```

Ranges use BigInt nonce starts, reject 64-bit overflow, and contain 1–16,777,216
requested candidates. Each worker completes its assigned range; the owner selects
the lowest winning index. Odd SIMD tails are computed but unused lanes are ignored.
Capture is explicit, limited to 4,096 total digests, and transferred back only for
diagnostics. Empty worker partitions are skipped. A distinct optimized C++
instantiation removes capture stores from ordinary mining.

Every winner is independently rehashed by the separate BigInt JavaScript SHA3
reference and compared against the template's target. That protects the backend
interface; chain/reward/tip checks, peer authentication and full block validation
remain with the native controller/node. This library never requests wallet secrets,
fetches remote jobs or submits blocks.

Operations reject concurrent use. Cancellation terminates the pool when work is
in flight, and future use rejects; create a new session to resume. Worker errors,
protocol errors and operation timeouts (30 seconds by default) also fail the pool.
Dispose is idempotent and rejects outstanding operations. Preparing an immutable
job broadcasts only 268 public bytes to each worker and computes its prefix once.
Normal scans return only indices and timing metadata, with no per-nonce IPC.

## Build and validation

The checked-in `kernel.wasm` is 6.6 KB, has no host imports, and comes with compiler,
flags and SHA-256 provenance in `build.json`. Rebuild with an installed Emscripten:

```sh
python3 tools/build_wasm_miner.py --emcc /path/to/emcc
node --test tests/wasm_kernel_test.mjs
python3 tools/test_wasm_mining.py --output benchmarks/my-simd.json
python3 tools/test_wasm_mining.py --attempts 16777216 --output benchmarks/my-simd-sustained.json
python3 tools/test_webgpu_ui.py --backend 8 --output benchmarks/my-simd-ui.json
```

The host-Chrome runner compares 1, 2, 4 and 8 CPU workers with WebGPU and native
Vulkan on the same header/range. Sessions and templates are resident; seven rounds
rotate/reverse execution order. Each sample normally has a discarded full-range
warm-up; `--cold` preserves first-after-idle behavior. Both are relevant: an idle
GPU can run its first browser pass at low clocks. Raw warm-up samples are retained.

Read the [same-host comparison](../../benchmarks/README.md) for measured throughput,
worker scaling and cold/steady-state differences. Worker kernel durations include
OS preemption and exclude IPC; they are not CPU utilization or energy readings.
Results compare CPU hardware against GPU hardware, not a WASM/native CPU overhead
ratio. An equivalently optimized native CPU benchmark would be needed for that.
