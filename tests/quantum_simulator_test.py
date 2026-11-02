"""Actual local quantum simulation, relative phases and native accepted-block E2E."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'miners'))
from quantum_simulator import grover_circuit,validate_job
from reversible_sha3 import Circuit,threshold_comparator
BIN=Path(os.environ.get('QCASH_BINARY',ROOT/'build/qcash')).resolve()

class SimulatorTests(unittest.TestCase):
    def test_deadline_kills_native_child(self):
        with tempfile.TemporaryDirectory(prefix='qcash-simulator-budget-') as directory:
            d=Path(directory);fake=d/'slow-native'
            fake.write_text('#!'+sys.executable+'\nimport os,time,pathlib\npathlib.Path(__file__).with_suffix(".pid").write_text(str(os.getpid()))\ntime.sleep(120)\n')
            fake.chmod(0o700)
            result=subprocess.run([sys.executable,str(ROOT/'miners/quantum_simulator.py'),'--binary',str(fake),
                '--work',str(d/'work.bin'),'--output',str(d/'output.bin'),'--chain-id','ab'*32,
                '--address','cd'*32,'--seconds','1'],capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,3,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'budget_ended')
            with self.assertRaises(ProcessLookupError):os.kill(int(fake.with_suffix('.pid').read_text()),0)
            self.assertFalse((d/'output.bin').exists())

    def test_grover_amplifies_actual_simulated_state(self):
        backend=AerSimulator(method='statevector',max_parallel_threads=1)
        from qiskit import transpile
        counts=backend.run(transpile(grover_circuit(4,[5],3),backend,optimization_level=0),shots=512,seed_simulator=17).result().get_counts()
        self.assertGreater(counts.get('0101',0),450)
        for width in range(1,5):
            c=Circuit();inputs=c.allocate(width);flag=threshold_comparator(c,inputs[::-1],(1<<width)//2)
            qc=QuantumCircuit(c.qubits);qc.h(inputs);qc.compose(c.qiskit(),inplace=True);qc.z(flag);qc.compose(c.qiskit().inverse(),inplace=True)
            amplitudes=Statevector.from_instruction(qc).data
            for index,amplitude in enumerate(amplitudes):
                expected=(-1 if index<=(1<<width)//2 else 1)/(2**(width/2)) if index<1<<width else 0
                self.assertAlmostEqual(abs(amplitude-expected),0,places=10)

    def test_native_solution_and_limits(self):
        with tempfile.TemporaryDirectory(prefix='qcash-qpu-test-') as directory:
            d=Path(directory)
            def native(*argv):
                return subprocess.check_output([str(BIN),*map(str,argv)],text=True,stderr=subprocess.PIPE,timeout=20).strip()
            initial=json.loads(native('init',d/'node',ROOT/'artifacts/devnet-genesis/genesis.bin'))
            native('init',d/'client',ROOT/'artifacts/devnet-genesis/genesis.bin')
            address=native('wallet',d/'wallet.pem');pin=native('identity',d/'node')
            with socket.socket() as s:s.bind(('127.0.0.1',0));peer=f'127.0.0.1:{s.getsockname()[1]}'
            with (d/'node.log').open('w') as log:
                node=subprocess.Popen([str(BIN),'node',str(d/'node'),peer],stdout=log,stderr=log)
                try:
                    native('trust',d/'client',peer,pin)
                    for _ in range(100):
                        try:native('query',d/'client',peer);break
                        except subprocess.CalledProcessError:time.sleep(.05)
                    native('work',d/'client',peer,address,d/'work.bin')
                    # Fixture selection is classical and outside the demo: choose a
                    # 64-nonce window containing a native reference solution.
                    native('solve',d/'work.bin',d/'reference.bin',1000000)
                    control=json.loads(native('inspect-work',d/'reference.bin'))
                    start=int.from_bytes(bytes.fromhex(control['header'])[-8:],'big') & ~63
                    cli=[sys.executable,str(ROOT/'miners/quantum_simulator.py'),'--binary',str(BIN),
                        '--work',str(d/'work.bin'),'--output',str(d/'solved.bin'),'--chain-id',initial['chain'],
                        '--address',address,'--bits','6','--nonce-start',str(start),'--shots','128',
                        '--verify-reversible','--receipt',str(d/'receipt.json')]
                    done=subprocess.run(cli,capture_output=True,text=True,timeout=40)
                    self.assertEqual(done.returncode,0,done.stdout+done.stderr)
                    result=json.loads(done.stdout)
                    self.assertEqual(result['classical_hashes'],64);self.assertTrue(result['simulation_executed'])
                    self.assertFalse(result['hash_inside_grover_circuit']);self.assertFalse(result['quantum_hardware_executed'])
                    self.assertFalse(result['quantum_speedup_claimed']);self.assertTrue(result['native_verified'])
                    self.assertTrue(result['reversible_reference']['ancillas_clean'])
                    index=int(result['nonce'])-start;self.assertGreater(result['counts'].get(f'{index:06b}',0),0)
                    native('submit',d/'client',peer,d/'solved.bin')
                    self.assertEqual(json.loads(native('query',d/'client',peer))['height'],1)
                    self.assertEqual(json.loads((d/'receipt.json').read_text())['blockId'],result['blockId'])
                    duplicate=subprocess.run(cli,capture_output=True,text=True,timeout=5);self.assertEqual(duplicate.returncode,2)
                    # Empty windows don't execute a fake simulation or write a solution.
                    raw=bytearray((d/'work.bin').read_bytes());at=len(b'pqcoin/pow-block/v1\0')+80
                    raw[at:at+32]=(1).to_bytes(32,'big');(d/'hard.bin').write_bytes(raw)
                    empty=cli[:];empty[empty.index('--work')+1]=str(d/'hard.bin');empty[empty.index('--output')+1]=str(d/'none.bin')
                    empty[empty.index('--receipt')+1]=str(d/'empty.json');empty.remove('--verify-reversible')
                    no=subprocess.run(empty,capture_output=True,text=True,timeout=20)
                    self.assertEqual(no.returncode,3,no.stdout+no.stderr)
                    self.assertFalse(json.loads(no.stdout)['simulation_executed']);self.assertFalse((d/'none.bin').exists())
                    invalid=empty[:];invalid[invalid.index('--bits')+1]='30'
                    rejected=subprocess.run(invalid,capture_output=True,text=True,timeout=5);self.assertEqual(rejected.returncode,2)
                    wrong=empty[:];wrong[wrong.index('--receipt')+1]=str(d/'wrong.json');wrong[wrong.index('--chain-id')+1]='ab'*32
                    bad=subprocess.run(wrong,capture_output=True,text=True,timeout=5);self.assertEqual(bad.returncode,2)
                    self.assertFalse((d/'wrong.json').exists())
                    controller=subprocess.run([sys.executable,str(ROOT/'miners/cpu_worker.py'),'--backend','qpu-simulator',
                        '--binary',str(BIN),'--home',str(d/'client'),'--peer',peer,'--peer-key',pin,'--chain-id',initial['chain'],
                        '--address',address,'--simulator-bits','2','--rounds','1','--cooldown','0','--seconds','25'],
                        capture_output=True,text=True,timeout=30)
                    self.assertEqual(controller.returncode,0,controller.stdout+controller.stderr)
                    events=[json.loads(row) for row in controller.stdout.splitlines()]
                    simulation=next(row['result'] for row in events if row['status']=='simulator_result')
                    self.assertEqual(simulation['classical_hashes'],4)
                    self.assertEqual(simulation['mode'],'precomputed_sha3_oracle_demo')
                    self.assertFalse(simulation['quantum_hardware_executed'])
                finally:
                    node.terminate()
                    try:node.wait(timeout=8)
                    except subprocess.TimeoutExpired:node.kill();node.wait()

if __name__=='__main__':unittest.main()
