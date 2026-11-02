#!/usr/bin/env python3
"""Bounded solo-mining controller. Keys stay local; every solution is node-validated."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

stopping = False

def stop(*_):
    global stopping
    stopping = True


def digest(text):
    if not re.fullmatch(r'[0-9a-f]{64}', text) or text == '0' * 64:
        raise argparse.ArgumentTypeError('Expected a nonzero lowercase 32-byte hex value')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True, help='Existing private node/client home; never a wallet key')
    parser.add_argument('--peer', required=True, help='Your reachable, independently verified node HOST:PORT')
    parser.add_argument('--peer-key', type=digest, required=True)
    parser.add_argument('--chain-id', type=digest, required=True)
    parser.add_argument('--address', type=digest, required=True, help='Public reward address')
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--attempts', type=int, default=1000000)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--cooldown', type=float, default=1.0)
    parser.add_argument('--backend', choices=['cpu','vulkan','qpu-simulator'], default='cpu')
    parser.add_argument('--vulkan-binary', type=Path, help='Local qcash-vulkan executable; defaults beside --binary')
    parser.add_argument('--vulkan-device', type=int, help='Optional Vulkan enumeration index; default selects a physical GPU')
    parser.add_argument('--vulkan-batch', type=int, default=65536)
    parser.add_argument('--simulator-bits', type=int, default=10)
    parser.add_argument('--simulator-shots', type=int, default=128)
    parser.add_argument('--simulator-seed', type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 1000000 or not 1 <= args.attempts <= 100000000 or not 1 <= args.seconds <= 3600 or not 0 <= args.cooldown <= 60:
        parser.error('Invalid rounds, attempts, seconds or cooldown budget')
    if args.backend=='qpu-simulator' and (not 1<=args.simulator_bits<=12 or not 1<=args.simulator_shots<=1024 or not 0<=args.simulator_seed<2**32 or 2**args.simulator_bits>args.attempts):
        parser.error('Simulator requires 1..12 bits, 1..1024 shots, a uint32 seed and enough attempts for the classical lookup window')
    binary = args.binary.resolve(strict=True)
    gpu = None
    if args.backend=='vulkan':
        if (args.vulkan_device is not None and not 0<=args.vulkan_device<2**32) or not 1<=args.vulkan_batch<=1048576:
            parser.error('Invalid Vulkan device or batch size')
        gpu = (args.vulkan_binary or binary.with_name('qcash-vulkan')).resolve(strict=True)
    home = args.home.resolve(strict=True)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    deadline = time.monotonic() + args.seconds

    def command(*argv, allowed=(0,), program=None):
        if stopping or time.monotonic() >= deadline:
            raise InterruptedError('Mining budget ended')
        process = subprocess.Popen([str(program or binary), *map(str, argv)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        limit = min(deadline, time.monotonic() + 20)
        try:
            while True:
                if stopping or time.monotonic() >= limit:
                    raise InterruptedError('Stopped or command deadline reached')
                try:
                    output, error = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode not in allowed:
                # Do not copy arbitrary peer diagnostics or paths into telemetry.
                raise RuntimeError('Node command failed; verify peer identity, network, node version and candidate status')
            return process.returncode, output.decode('utf-8')
        finally:
            if process.poll() is None:
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.communicate()

    def state():
        _, output = command('query', home, args.peer)
        value = json.loads(output)
        if value.get('chain') != args.chain_id:
            raise ValueError('Peer is on another chain')
        return value

    def report(status, **fields):
        print(json.dumps({'status': status, **fields}), flush=True)

    try:
        command('trust', home, args.peer, args.peer_key)
        state()
        # No wallet PEM, browser session, provider credential or signed payment is read.
        for index in range(args.rounds):
            if stopping or time.monotonic() >= deadline:
                break
            with tempfile.TemporaryDirectory(prefix='qcash-miner-') as temporary:
                work = Path(temporary) / 'work.bin'; solved = Path(temporary) / 'solved.bin'
                command('work', home, args.peer, args.address, work)
                _, raw = command('inspect-work', work); job = json.loads(raw)
                if job.get('chainId') != args.chain_id or job.get('rewardAddress') != args.address:
                    raise ValueError('Work identity or reward address mismatch')
                if args.backend=='cpu':
                    code, output = command('solve', work, solved, args.attempts, allowed=(0, 3))
                elif args.backend=='vulkan':
                    device_options=[] if args.vulkan_device is None else ['--device',args.vulkan_device]
                    code,output=command('solve',work,solved,args.attempts,'--batch',args.vulkan_batch,
                        '--seconds',19,*device_options,program=gpu,allowed=(0,3))
                    report('vulkan_result',result=json.loads(output))
                else:
                    code,output=command(Path(__file__).with_name('quantum_simulator.py'),'--binary',binary,
                        '--work',work,'--output',solved,'--chain-id',args.chain_id,'--address',args.address,
                        '--bits',args.simulator_bits,'--shots',args.simulator_shots,'--seed',args.simulator_seed,
                        '--seconds',19,program=sys.executable,allowed=(0,3))
                    report('simulator_result',result=json.loads(output))
                if code == 3:
                    report('exhausted', round=index + 1, backend=args.backend,
                           attempts=args.attempts if args.backend=='cpu' else json.loads(output).get('attempts' if args.backend=='vulkan' else 'classical_hashes'))
                elif state()['tip'] != job['parent']:
                    report('stale', round=index + 1)
                else:
                    block = json.loads(output)['blockId']
                    try:
                        command('submit', home, args.peer, solved)
                    except RuntimeError:
                        report('submission_uncertain_or_rejected', blockId=block)
                        # Never report a reward or blindly resubmit after an ambiguous failure.
                        return 2
                    report('accepted_by_peer', blockId=block, final=False, backend=args.backend)
            until = min(deadline, time.monotonic() + args.cooldown)
            while not stopping and time.monotonic() < until:
                time.sleep(min(.1, max(0, until - time.monotonic())))
        report('stopped' if stopping else 'finished')
        return 130 if stopping else 0
    except InterruptedError:
        report('stopped' if stopping else 'budget_ended')
        return 130 if stopping else 0
    except (OSError, RuntimeError, ValueError, KeyError, UnicodeError):
        report('failed', message='Check node availability, pinned identity, exact chain ID and native miner version.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
