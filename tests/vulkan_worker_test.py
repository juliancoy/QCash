"""Real Vulkan nonce search, native verification and isolated node acceptance."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import threading
import struct
import unittest
ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('QCASH_BINARY', ROOT/'build/qcash')).resolve()
GPU = Path(os.environ.get('QCASH_VULKAN_BINARY', ROOT/'build/qcash-vulkan')).resolve()
sys.path.insert(0,str(ROOT/'miners'))
from vulkan_worker import VulkanWorker


class VulkanWorkerTests(unittest.TestCase):
    def test_oversized_private_request_is_rejected(self):
        worker=subprocess.Popen([str(GPU),'serve'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            self.assertEqual(json.loads(worker.stdout.readline())['status'],'ready')
            worker.stdin.write(struct.pack('!I',16385));worker.stdin.flush()
            out,err=worker.communicate(timeout=10)
            self.assertEqual(worker.returncode,2,err+out)
        finally:
            if worker.poll() is None:worker.kill();worker.wait()

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
                        '--rounds', 2, '--attempts', 1000000, '--cooldown', 0, '--seconds', 30)
                    parsed = [json.loads(row) for row in events.splitlines()]
                    self.assertTrue(any(row['status']=='vulkan_result' for row in parsed))
                    self.assertTrue(any(row['status']=='accepted_by_peer' and row['backend']=='vulkan' for row in parsed))
                    self.assertEqual(json.loads(native('query', d/'client', peer))['height'], 3)
                    ready=[row for row in parsed if row['status']=='vulkan_ready']
                    results=[row for row in parsed if row['status']=='vulkan_result']
                    self.assertEqual(len(ready),1);self.assertEqual(len(results),2)
                    self.assertEqual({row['result']['worker_pid'] for row in results},{ready[0]['result']['worker_pid']})
                    # A deterministic tip-change fixture checks the controller's
                    # stale gate with an actual GPU candidate and real TLS peer.
                    wrapper=d/'tip-changing-node'
                    wrapper.write_text('#!'+sys.executable+'\nimport sys,subprocess,json,pathlib\n'
                        +'binary='+repr(str(BIN))+'\n'
                        +'r=subprocess.run([binary,*sys.argv[1:]],capture_output=True)\n'
                        +'out=r.stdout\n'
                        +'if sys.argv[1]=="query" and r.returncode==0:\n'
                        +' p=pathlib.Path(__file__).with_suffix(".count");n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))\n'
                        +' if n>1:\n'
                        +'  value=json.loads(out);value["tip"]="ab"*32;out=json.dumps(value).encode()\n'
                        +'if sys.argv[1]=="submit":pathlib.Path(__file__).with_suffix(".submitted").write_text("unexpected")\n'
                        +'sys.stdout.buffer.write(out);sys.stderr.buffer.write(r.stderr);raise SystemExit(r.returncode)\n')
                    wrapper.chmod(0o700)
                    stale=call(sys.executable,ROOT/'miners/cpu_worker.py','--backend','vulkan',
                        '--binary',wrapper,'--vulkan-binary',GPU,'--home',d/'client','--peer',peer,
                        '--peer-key',identity,'--chain-id',initial['chain'],'--address',reward,
                        '--rounds',1,'--attempts',1000000,'--cooldown',0,'--seconds',30)
                    self.assertTrue(any(json.loads(row)['status']=='stale' for row in stale.splitlines()))
                    self.assertFalse(wrapper.with_suffix('.submitted').exists())
                    self.assertEqual(json.loads(native('query', d/'client', peer))['height'],3)
                    # Rejection must not poison the next template in the same
                    # context. Exercise the optional GPU prefix preparation too.
                    with VulkanWorker(GPU,deadline=time.monotonic()+20,options=['--prepare','gpu']) as worker:
                        pid=worker.process.pid
                        bad=worker.solve(d/'work.bin',d/'wrong.bin',1000000,'ab'*32,reward)
                        self.assertEqual(bad['status'],'failed');self.assertFalse((d/'wrong.bin').exists())
                        no=worker.solve(d/'hard.bin',d/'hard-output.bin',65,initial['chain'],reward)
                        self.assertEqual(no['status'],'exhausted')
                        first=worker.solve(d/'work.bin',d/'again.bin',1000000,initial['chain'],reward)
                        self.assertEqual(first['status'],'solved');self.assertEqual(first['worker_pid'],pid)
                        duplicate=worker.solve(d/'work.bin',d/'again.bin',1000000,initial['chain'],reward)
                        self.assertEqual(duplicate['status'],'failed')
                    self.assertIsNotNone(worker.process.poll())
                    with VulkanWorker(GPU,deadline=time.monotonic()+20,options=['--batch','1','--queue-depth','1']) as active:
                        event=threading.Event();active.cancelled=event.is_set
                        timer=threading.Timer(.05,event.set);timer.start()
                        try:
                            with self.assertRaises(InterruptedError):active.solve(d/'hard.bin',d/'cancel-worker.bin',100000000,initial['chain'],reward)
                        finally:timer.cancel()
                    self.assertIsNotNone(active.process.poll());self.assertFalse((d/'cancel-worker.bin').exists())

                finally:
                    node.terminate()
                    try: node.wait(timeout=8)
                    except subprocess.TimeoutExpired: node.kill(); node.wait()


if __name__ == '__main__': unittest.main()
