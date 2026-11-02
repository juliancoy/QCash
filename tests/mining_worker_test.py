"""Real loopback TLS mining jobs, external nonces, budgets and rejection paths."""
import hashlib
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
BIN = Path(os.environ.get('QCASH_BINARY', ROOT / 'build/qcash')).resolve()

class MiningWorkerTest(unittest.TestCase):
    def test_controller_deadline_reaps_active_child(self):
        with tempfile.TemporaryDirectory(prefix='qcash-controller-test-') as directory:
            d = Path(directory)
            fake = d/'slow-node'
            fake.write_text('#!'+sys.executable+'\nimport os,time,pathlib\npathlib.Path(__file__).with_suffix(".pid").write_text(str(os.getpid()))\ntime.sleep(120)\n')
            fake.chmod(0o700)
            result = subprocess.run([sys.executable,str(ROOT/'miners/cpu_worker.py'),'--binary',str(fake),'--home',str(d),
                '--peer','127.0.0.1:29600','--peer-key','ab'*32,'--chain-id','ab'*32,'--address','cd'*32,'--seconds','1'],
                capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'budget_ended')
            pid = int(fake.with_suffix('.pid').read_text())
            with self.assertRaises(ProcessLookupError): os.kill(pid,0)

    def test_external_and_conventional_workers(self):
        with tempfile.TemporaryDirectory(prefix='qcash-miner-test-') as directory:
            d = Path(directory)
            def call(*args, code=0):
                r = subprocess.run([str(BIN), *map(str, args)], capture_output=True, text=True, timeout=25)
                self.assertEqual(r.returncode, code, r.stderr)
                return r.stdout.strip()
            bootstrap = ROOT / 'artifacts/devnet-genesis/genesis.bin'
            initial = json.loads(call('init', d/'node', bootstrap))
            call('init', d/'client', bootstrap)
            address = call('wallet', d/'reward.pem')
            fingerprint = call('identity', d/'node')
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0)); peer = f'127.0.0.1:{s.getsockname()[1]}'
            with (d/'node.log').open('w') as log:
                node = subprocess.Popen([str(BIN), 'node', str(d/'node'), peer], stdout=log, stderr=log)
                try:
                    call('trust', d/'client', peer, fingerprint)
                    for _ in range(100):
                        try:
                            status = json.loads(call('query', d/'client', peer)); break
                        except AssertionError:
                            self.assertIsNone(node.poll()); time.sleep(.05)
                    else: self.fail('node did not start')
                    call('work', d/'client', peer, address, d/'job.bin')
                    job = json.loads(call('inspect-work', d/'job.bin'))
                    self.assertEqual(job['chainId'], initial['chain'])
                    self.assertEqual(job['rewardAddress'], address)
                    header = bytearray.fromhex(job['header']); target = bytes.fromhex(job['target'])
                    # Independent backend uses the exported canonical bytes, not the native solver.
                    nonce = 0
                    while True:
                        header[job['nonceOffset']:] = nonce.to_bytes(8, 'big')
                        if hashlib.sha3_256(header).digest() <= target: break
                        nonce += 1
                        self.assertLess(nonce, 1000000)
                    extra = header[job['extraNonceOffset']:job['nonceOffset']].hex()
                    call('complete-work', d/'job.bin', extra, nonce, d/'external.bin')
                    call('submit', d/'client', peer, d/'external.bin')
                    self.assertEqual(json.loads(call('query', d/'client', peer))['height'], 1)
                    call('complete-work', d/'job.bin', extra, nonce, d/'external.bin', code=1) # no overwrite
                    bad = nonce + 1
                    while True:
                        header[job['nonceOffset']:] = bad.to_bytes(8, 'big')
                        if hashlib.sha3_256(header).digest() > target: break
                        bad += 1
                    call('complete-work', d/'job.bin', extra, bad, d/'bad.bin', code=1)
                    self.assertFalse((d/'bad.bin').exists())
                    call('submit', d/'client', peer, d/'external.bin', code=1) # duplicate is not another reward
                    # Zero target and malformed templates fail before hashing.
                    raw = bytearray((d/'job.bin').read_bytes()); target_offset = len(b'pqcoin/pow-block/v1\0') + 32 + 32 + 8 + 8
                    raw[target_offset:target_offset+32] = bytes(32); (d/'zero.bin').write_bytes(raw)
                    call('solve', d/'zero.bin', d/'zero-solution.bin', 10, code=1)
                    raw[target_offset:target_offset+32] = (1).to_bytes(32, 'big'); (d/'hard.bin').write_bytes(raw)
                    self.assertEqual(json.loads(call('solve', d/'hard.bin', d/'no-solution.bin', 16, code=3))['attempts'], 16)
                    self.assertFalse((d/'no-solution.bin').exists())
                    process = subprocess.Popen([str(BIN),'solve',str(d/'hard.bin'),str(d/'cancelled.bin'),'100000000'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    time.sleep(.1); process.terminate(); output, _ = process.communicate(timeout=5)
                    self.assertEqual(process.returncode,130); self.assertFalse((d/'cancelled.bin').exists())
                    worker = [sys.executable, str(ROOT/'miners/cpu_worker.py'), '--binary',str(BIN),'--home',str(d/'client'),'--peer',peer,'--peer-key',fingerprint,'--chain-id',initial['chain'],'--address',address,'--rounds','2','--cooldown','0']
                    result = subprocess.run(worker,capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    events = [json.loads(line) for line in result.stdout.splitlines()]
                    self.assertEqual(sum(e['status']=='accepted_by_peer' for e in events),2)
                    self.assertEqual(json.loads(call('query',d/'client',peer))['height'],3)
                    wrong = worker.copy(); wrong[wrong.index('--chain-id')+1]='ab'*32
                    result = subprocess.run(wrong,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,2); self.assertEqual(json.loads(result.stdout)['status'],'failed')
                    self.assertEqual(json.loads(call('query',d/'client',peer))['height'],3)
                finally:
                    node.terminate()
                    try: node.wait(timeout=8)
                    except subprocess.TimeoutExpired: node.kill(); node.wait()

if __name__ == '__main__': unittest.main()
