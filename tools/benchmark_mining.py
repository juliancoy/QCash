#!/usr/bin/env python3
"""Local, single-CPU mining comparison; never contacts the hosted network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def scan(header, target, count, stop):
    header = bytearray.fromhex(header)
    start = int.from_bytes(header[-8:], 'big')
    target = bytes.fromhex(target)
    found = None
    for index in range(count):
        header[-8:] = (start + index).to_bytes(8, 'big')
        if hashlib.sha3_256(header).digest() <= target:
            found = start + index
            if stop:
                return found, index + 1
    return found, count


def python_worker(binary, work, output, count):
    job = json.loads(subprocess.check_output([binary, 'inspect-work', work], text=True))
    nonce, attempts = scan(job['header'], job['target'], int(count), True)
    if nonce is None:
        raise RuntimeError('Expected proof not found')
    header = bytes.fromhex(job['header'])
    result = json.loads(subprocess.check_output(
        [binary, 'complete-work', work, header[-40:-8].hex(), str(nonce), output], text=True))
    print(json.dumps({**result, 'attempts': attempts}))


def summary(values):
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values), 'samples': values}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--python-worker':
        python_worker(*sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT/'build/qcash')
    parser.add_argument('--simulator-python', type=Path, default=ROOT/'.deps/quantum-venv/bin/python')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vulkan-binary', type=Path, help='Include the optional built Vulkan miner')
    parser.add_argument('--vulkan-batch', type=int, default=262144)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--hashes', type=int, default=262144)
    parser.add_argument('--bits', type=int, default=8)
    parser.add_argument('--cpu', type=int)
    args = parser.parse_args()
    if not 3 <= args.repeats <= 20 or not 65536 <= args.hashes <= 10000000 or not 4 <= args.bits <= 10:
        parser.error('Use 3..20 repeats, 65536..10000000 hashes and 4..10 search bits')
    if args.output.exists():
        parser.error('Output must not exist')
    if hasattr(os, 'sched_getaffinity'):
        cpu = min(os.sched_getaffinity(0)) if args.cpu is None else args.cpu
        os.sched_setaffinity(0, {cpu})
    else:
        cpu = None
    binary = str(args.binary.resolve(strict=True))
    # Preserve the venv path: resolving its Python symlink loses the environment.
    simulator_python = str(args.simulator_python.absolute())
    if not args.simulator_python.is_file():
        parser.error('Simulator Python does not exist')
    gpu_binary = str(args.vulkan_binary.resolve(strict=True)) if args.vulkan_binary else None
    if not 1<=args.vulkan_batch<=1048576:
        parser.error('Vulkan batch must be 1..1048576')
    load_start = os.getloadavg()
    def run(*argv, codes=(0,)):
        result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120)
        if result.returncode not in codes:
            raise RuntimeError(f'Command failed ({result.returncode}): {result.stderr[-1000:]} {result.stdout[-1000:]}')
        return result.stdout.strip()
    def telemetry():
        if not gpu_binary:
            return None
        try:
            return run('nvidia-smi','--query-gpu=name,driver_version,utilization.gpu,power.draw,temperature.gpu',
                       '--format=csv,noheader,nounits')
        except (OSError, RuntimeError):
            return None
    gpu_start = telemetry()
    def native(*argv, **kwargs):
        return run(binary, *argv, **kwargs)
    with tempfile.TemporaryDirectory(prefix='qcash-speed-') as directory:
        d = Path(directory)
        initial = json.loads(native('init', d/'node', ROOT/'artifacts/devnet-genesis/genesis.bin'))
        native('init', d/'client', ROOT/'artifacts/devnet-genesis/genesis.bin')
        reward = native('wallet', d/'reward.pem')
        identity = native('identity', d/'node')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            peer = f'127.0.0.1:{sock.getsockname()[1]}'
        with (d/'node.log').open('w') as log:
            node = subprocess.Popen([binary, 'node', str(d/'node'), peer], stdout=log, stderr=log)
            try:
                native('trust', d/'client', peer, identity)
                for _ in range(100):
                    try:
                        native('query', d/'client', peer)
                        break
                    except RuntimeError:
                        if node.poll() is not None:
                            raise
                        time.sleep(.05)
                else:
                    raise RuntimeError('Loopback node did not start')
                native('work', d/'client', peer, reward, d/'work.bin')
                # A measured fixture-selection cost, not a simulated mining win.
                began = time.perf_counter()
                native('solve', d/'work.bin', d/'reference.bin', 10000000)
                fixture_seconds = time.perf_counter() - began
                reference = json.loads(native('inspect-work', d/'reference.bin'))
                winning_nonce = int.from_bytes(bytes.fromhex(reference['header'])[-8:], 'big')
                start = winning_nonce & ~((1 << args.bits) - 1)
                raw = bytearray((d/'work.bin').read_bytes())
                raw[260:268] = start.to_bytes(8, 'big')
                (d/'window.bin').write_bytes(raw)
                job = json.loads(native('inspect-work', d/'window.bin'))
                # Hard target forces a complete scan for throughput measurement.
                hard = raw.copy()
                hard[100:132] = (1).to_bytes(32, 'big')
                (d/'hard.bin').write_bytes(hard)
                hard_job = json.loads(native('inspect-work', d/'hard.bin'))
                found, attempts = scan(hard_job['header'], hard_job['target'], args.hashes, False)
                assert found is None and attempts == args.hashes
                throughput = {'Native C++ miner': [], 'Python hashlib loop': []}
                latency = {'Native C++ miner': [], 'Python hashlib miner': [], 'Qiskit Aer Grover demo': []}
                simulator_receipts = []
                gpu_receipts = []
                if gpu_binary:
                    throughput['Vulkan GPU · dispatch + wait'] = []
                    throughput['Vulkan GPU · kernel only'] = []
                    latency['Vulkan GPU miner'] = []
                # Warm up both hash paths; measured rounds alternate order.
                native('solve', d/'hard.bin', d/'never.bin', 4096, codes=(3,))
                scan(hard_job['header'], hard_job['target'], 4096, False)
                for repeat in range(args.repeats):
                    for name in ['Native C++ miner','Python hashlib loop','Vulkan GPU · dispatch + wait'][::1 if repeat % 2 == 0 else -1]:
                        if name.startswith('Vulkan') and not gpu_binary:
                            continue
                        began = time.perf_counter()
                        if name.startswith('Native'):
                            report = json.loads(native('solve', d/'hard.bin', d/'never.bin', args.hashes, codes=(3,)))
                            assert report['status'] == 'exhausted' and report['attempts'] == args.hashes
                        elif name.startswith('Vulkan'):
                            report = json.loads(run(gpu_binary, 'bench', d/'hard.bin', args.hashes, 1,
                                                    '--batch',args.vulkan_batch))
                            sample = report['samples'][0]
                            throughput[name].append(args.hashes/sample['wall_seconds'])
                            if sample['gpu_seconds'] is None:
                                raise RuntimeError('GPU timestamp queries unavailable; cannot report kernel throughput')
                            throughput['Vulkan GPU · kernel only'].append(args.hashes/sample['gpu_seconds'])
                            gpu_receipts.append(report)
                            continue
                        else:
                            found, count = scan(hard_job['header'], hard_job['target'], args.hashes, False)
                            assert found is None and count == args.hashes
                        throughput[name].append(args.hashes / (time.perf_counter() - began))
                    for number, name in enumerate(list(latency)[::1 if repeat % 2 == 0 else -1]):
                        output = d/f'solution-{repeat}-{number}.bin'
                        began = time.perf_counter()
                        if name.startswith('Native'):
                            report = json.loads(native('solve', d/'window.bin', output, 1 << args.bits))
                        elif name.startswith('Vulkan'):
                            report = json.loads(run(gpu_binary, 'solve',d/'window.bin',output,1<<args.bits,
                                                    '--batch',args.vulkan_batch))
                            assert report['native_verified']
                        elif name.startswith('Python'):
                            report = json.loads(run(sys.executable, __file__, '--python-worker', binary,
                                                    d/'window.bin', output, 1 << args.bits))
                        else:
                            report = json.loads(run(simulator_python, ROOT/'miners/quantum_simulator.py',
                                '--binary', binary, '--work', d/'window.bin', '--output', output,
                                '--chain-id', initial['chain'], '--address', reward,
                                '--bits', args.bits, '--shots', 128, '--seed', 17 + repeat, '--seconds', 110))
                            assert report['simulation_executed'] and report['native_verified']
                            simulator_receipts.append(report)
                        latency[name].append(time.perf_counter() - began)
                        verified = json.loads(native('inspect-work', output))
                        digest = hashlib.sha3_256(bytes.fromhex(verified['header'])).hexdigest()
                        assert digest == report['blockId'] and int(digest, 16) <= int(job['target'], 16)
                    print(f'Completed round {repeat + 1}/{args.repeats}', flush=True)
            finally:
                node.terminate()
                try:
                    node.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    node.kill(); node.wait()
        metadata = {
            'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'cpu_model': next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), platform.processor()),
            'cpu_affinity': cpu, 'platform': platform.platform(), 'python': sys.version,
            'python_openssl': ssl.OPENSSL_VERSION,
            'native_openssl': run(ROOT/'.deps/openssl/bin/openssl', 'version'),
            'source_commit': run('git', '-C', ROOT, 'rev-parse', 'HEAD'),
            'source_dirty': bool(run('git', '-C', ROOT, 'status', '--porcelain')),
            'load_average_start': load_start, 'load_average_end': os.getloadavg(),
            'gpu_telemetry_fields': ['name','driver_version','utilization_percent','board_power_watts','temperature_celsius'],
            'gpu_telemetry_start': gpu_start,
            'gpu_telemetry_end': telemetry(),
            'benchmark_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'gpu': json.loads(run(gpu_binary,'info')) if gpu_binary else None,
            'gpu_binary_sha256': hashlib.sha256(Path(gpu_binary).read_bytes()).hexdigest() if gpu_binary else None,
            'binary_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        }
        result = {'metadata': metadata, 'hash_attempts_per_round': args.hashes,
            'throughput_hashes_per_second': {k: summary(v) for k, v in throughput.items()},
            'verified_proof_latency_seconds': {k: summary(v) for k, v in latency.items()},
            'fixture': {'job': job, 'window_size': 1 << args.bits, 'first_winning_nonce': str(winning_nonce),
                'native_attempts_to_first_proof': winning_nonce - start + 1,
                'classical_selection_seconds_excluded_from_latency': fixture_seconds},
            'simulator_receipts': simulator_receipts,
            'gpu_receipts': gpu_receipts,
            'methodology': [
                'Single logical CPU; background workloads not stopped. Median and min/max over alternating rounds.',
                'Throughput: complete hard-target scan, same 268-byte header for both CPU paths. Native includes process startup; Python loop runs warm in process.',
                'Proof latency: separate processes, same preselected known-winning window; includes startup, search and native proof construction/verification. No submission/network latency.',
                'Fixture selection classically finds a winning window before measurement. These are conditional demo latencies, not expected network block times.',
                'Simulator performs full classical window lookup then CPU statevector simulation; CPU miners stop at first proof. No quantum speedup or QPU hardware claim.',
                'Python hashlib uses native OpenSSL hashing; this is not a pure Python SHA3 implementation.',
                'Vulkan, when explicitly enabled, reuses a fixed first-block SHA3 midstate; measured dispatch + fence wall time excludes context/pipeline creation. Kernel time uses Vulkan timestamps. Native baseline recomputes both SHA3 blocks.',
                'Vulkan proof latency includes a new process/context/pipeline; driver caches may be warm. No WebGPU, WASM or physical QPU mining backend is benchmarked. ML-KEM performance does not measure mining.',
            ]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as out:
            json.dump(result, out, indent=2); out.write('\n')
        print(args.output)


if __name__ == '__main__':
    main()
