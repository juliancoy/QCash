"""Real Vulkan nonce search, native verification and isolated node acceptance."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('QCASH_BINARY', ROOT/'build/qcash')).resolve()
GPU = Path(os.environ.get('QCASH_VULKAN_BINARY', ROOT/'build/qcash-vulkan')).resolve()


class VulkanWorkerTests(unittest.TestCase):
    def test_real_gpu_block_and_controller(self):
        with tempfile.TemporaryDirectory(prefix='qcash-vulkan-test-') as directory:
            d = Path(directory)
            def call(program, *argv, code=0):
                r = subprocess.run([str(program), *map(str, argv)], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, code, r.stderr + r.stdout)
                return r.stdout.strip()
            def native(*argv, **kwargs):
                return call(BIN, *argv, **kwargs)
            initial = json.loads(native('init', d/'node', ROOT/'artifacts/devnet-genesis/genesis.bin'))
            native('init', d/'client', ROOT/'artifacts/devnet-genesis/genesis.bin')
            reward = native('wallet', d/'reward.pem')
            identity = native('identity', d/'node')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); peer = f'127.0.0.1:{sock.getsockname()[1]}'
            with (d/'node.log').open('w') as log:
                node = subprocess.Popen([str(BIN), 'node', str(d/'node'), peer], stdout=log, stderr=log)
                try:
                    native('trust', d/'client', peer, identity)
                    for _ in range(100):
                        r = subprocess.run([str(BIN), 'query', str(d/'client'), peer], capture_output=True, timeout=10)
                        if r.returncode == 0: break
                        self.assertIsNone(node.poll()); time.sleep(.05)
                    else: self.fail('Node did not start')
                    native('work', d/'client', peer, reward, d/'work.bin')
                    solved = json.loads(call(GPU, 'solve', d/'work.bin', d/'solved.bin', 1000000))
                    self.assertTrue(solved['native_verified'])
                    self.assertEqual(solved['backend'], 'vulkan-sha3-midstate')
                    self.assertEqual((d/'solved.bin').stat().st_mode & 0o777, 0o600)
                    native('submit', d/'client', peer, d/'solved.bin')
                    self.assertEqual(json.loads(native('query', d/'client', peer))['height'], 1)
                    call(GPU, 'solve', d/'work.bin', d/'solved.bin', 1000000, code=2)
                    hard = bytearray((d/'work.bin').read_bytes()); hard[100:132] = (1).to_bytes(32, 'big')
                    (d/'hard.bin').write_bytes(hard)
                    exhausted = json.loads(call(GPU, 'solve', d/'hard.bin', d/'none.bin', 65, code=3))
                    self.assertEqual(exhausted['attempts'], 65); self.assertFalse((d/'none.bin').exists())
                    call(GPU, 'solve', d/'hard.bin', d/'none.bin', 0, code=2)
                    hard[260:268] = (2**64 - 1).to_bytes(8, 'big'); (d/'overflow.bin').write_bytes(hard)
                    call(GPU, 'solve', d/'overflow.bin', d/'none.bin', 2, code=2)
                    process = subprocess.Popen([str(GPU), 'solve', str(d/'hard.bin'), str(d/'cancelled.bin'),
                                                '100000000'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    time.sleep(.02); process.terminate(); out, err = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 130, out + err); self.assertFalse((d/'cancelled.bin').exists())
                    events = call(sys.executable, ROOT/'miners/cpu_worker.py', '--backend', 'vulkan',
                        '--binary', BIN, '--vulkan-binary', GPU, '--home', d/'client', '--peer', peer,
                        '--peer-key', identity, '--chain-id', initial['chain'], '--address', reward,
                        '--rounds', 1, '--attempts', 1000000, '--cooldown', 0, '--seconds', 30)
                    parsed = [json.loads(row) for row in events.splitlines()]
                    self.assertTrue(any(row['status']=='vulkan_result' for row in parsed))
                    self.assertTrue(any(row['status']=='accepted_by_peer' and row['backend']=='vulkan' for row in parsed))
                    self.assertEqual(json.loads(native('query', d/'client', peer))['height'], 2)
                finally:
                    node.terminate()
                    try: node.wait(timeout=8)
                    except subprocess.TimeoutExpired: node.kill(); node.wait()


if __name__ == '__main__': unittest.main()
