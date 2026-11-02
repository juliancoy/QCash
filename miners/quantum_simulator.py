#!/usr/bin/env python3
"""Bounded, CPU-only Qiskit Grover demo over classically precomputed SHA3 results.

This executes a real small quantum circuit in a simulator. SHA3 is NOT computed
inside that Grover circuit. All classical precomputation is counted explicitly.
A separate reversible full-hash reference can be basis-verified on the candidate.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

LABEL=b'pqcoin/pow-block/v1\0'
HEADER_BYTES=len(LABEL)+7*32+3*8
MAX_QUBITS=12


def validate_job(job):
    if not isinstance(job,dict) or job.get('format')!='qcash-work-v1':raise ValueError('Unsupported work format')
    text=job.get('header')
    if not isinstance(text,str) or len(text)!=HEADER_BYTES*2 or not re.fullmatch('[0-9a-f]+',text):raise ValueError('Invalid canonical header')
    header=bytes.fromhex(text)
    if not header.startswith(LABEL):raise ValueError('Wrong header domain')
    if type(job.get('nonceOffset')) is not int or job['nonceOffset']!=HEADER_BYTES-8:raise ValueError('Invalid nonce offset')
    if type(job.get('extraNonceOffset')) is not int or job['extraNonceOffset']!=HEADER_BYTES-40:raise ValueError('Invalid extra nonce offset')
    at=len(LABEL)
    for key,start in [('chainId',at),('parent',at+32),('target',at+80),('rewardAddress',at+112)]:
        if job.get(key)!=header[start:start+32].hex():raise ValueError('Work metadata/header mismatch')
    target=int(job['target'],16)
    if not target:raise ValueError('Zero target')
    return header,target


def phase_oracle(qubits,marked):
    from qiskit import QuantumCircuit
    circuit=QuantumCircuit(qubits,name='precomputed_sha3_truth_table')
    for value in marked:
        zeros=[bit for bit in range(qubits) if not(value>>bit)&1]
        for bit in zeros:circuit.x(bit)
        if qubits==1:circuit.z(0)
        else:
            circuit.h(qubits-1);circuit.mcx(list(range(qubits-1)),qubits-1);circuit.h(qubits-1)
        for bit in zeros:circuit.x(bit)
    return circuit


def grover_circuit(qubits,marked,iterations):
    from qiskit import QuantumCircuit
    if type(qubits) is not int or not 1<=qubits<=MAX_QUBITS:raise ValueError('Simulator requires 1..12 qubits')
    if type(iterations) is not int or not 0<=iterations<=64:raise ValueError('Iteration budget exceeded')
    if len(set(marked))!=len(marked) or any(type(v)is not int or not 0<=v<1<<qubits for v in marked):raise ValueError('Invalid marked states')
    if iterations*len(marked)>4096:raise ValueError('Oracle construction budget exceeded')
    circuit=QuantumCircuit(qubits)
    circuit.h(range(qubits))
    if iterations:
        oracle=phase_oracle(qubits,marked)
        diffusion=QuantumCircuit(qubits)
        diffusion.h(range(qubits));diffusion.x(range(qubits))
        if qubits==1:diffusion.z(0)
        else:
            diffusion.h(qubits-1);diffusion.mcx(list(range(qubits-1)),qubits-1);diffusion.h(qubits-1)
        diffusion.x(range(qubits));diffusion.h(range(qubits))
        for _ in range(iterations):circuit.compose(oracle,inplace=True);circuit.compose(diffusion,inplace=True)
    circuit.measure_all()
    return circuit


def simulate(job,*,bits=10,shots=128,seed=7,nonce_start=None,iterations=None,verify_reversible=False):
    # Imported inside the bounded worker process, never in the node or browser.
    from qiskit import transpile
    from qiskit_aer import AerSimulator
    header,target=validate_job(job)
    if type(bits)is not int or not 1<=bits<=MAX_QUBITS or type(shots)is not int or not 1<=shots<=1024:raise ValueError('Invalid simulation budget')
    if type(seed)is not int or not 0<=seed<2**32:raise ValueError('Invalid simulator seed')
    if iterations is not None and (type(iterations)is not int or not 0<=iterations<=64):raise ValueError('Invalid iteration budget')
    start=int.from_bytes(header[-8:],'big') if nonce_start is None else nonce_start
    count=1<<bits
    if type(start)is not int or not 0<=start<=2**64-count:raise ValueError('Nonce window overflows')
    began=time.monotonic();marked=[];candidate=bytearray(header)
    for index in range(count):
        candidate[-8:]=(start+index).to_bytes(8,'big')
        if int.from_bytes(hashlib.sha3_256(candidate).digest(),'big')<=target:marked.append(index)
    precompute_seconds=time.monotonic()-began
    fraction=len(marked)/count
    result={'backend':'qiskit-aer-cpu-statevector','mode':'precomputed_sha3_oracle_demo',
            'chainId':job['chainId'],'rewardAddress':job['rewardAddress'],
            'template_sha256':hashlib.sha256(header).hexdigest(),
            'hash_inside_grover_circuit':False,'quantum_hardware_executed':False,'quantum_speedup_claimed':False,
            'qubits':bits,'statevector_bytes':16*count,'classical_hashes':count,
            'classical_hash_count_scope':'lookup_window_only_excludes_candidate_rechecks_and_native_verification',
            'marked_states':len(marked),
            'nonce_start':str(start),'window_size':count,'uniform_success_probability':fraction,
            'classical_precomputation_seconds':precompute_seconds,'shots_requested':shots,'shots_executed':0,
            'seed':seed,'counts':{},'versions':{name:importlib.metadata.version(name) for name in ['qiskit','qiskit-aer']},
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if not marked:return {**result,'status':'empty_window','simulation_executed':False,'iterations':0}
    theta=math.asin(math.sqrt(fraction))
    rounds=max(0,round(math.pi/(4*theta)-.5)) if iterations is None else iterations
    circuit=grover_circuit(bits,marked,rounds)
    backend=AerSimulator(method='statevector',device='CPU',max_parallel_threads=1,
                         max_parallel_experiments=1,max_memory_mb=128)
    began=time.monotonic();compiled=transpile(circuit,backend,optimization_level=0,seed_transpiler=seed,num_processes=1)
    compiled_seconds=time.monotonic()-began;began=time.monotonic()
    measured=backend.run(compiled,shots=shots,seed_simulator=seed).result()
    if not measured.success:raise RuntimeError('Local simulator failed')
    counts=measured.get_counts(); marked_set=set(marked)
    winners=[int(key,2) for key in sorted(counts,key=lambda key:(-counts[key],key)) if int(key,2) in marked_set]
    result.update(simulation_executed=True,iterations=rounds,shots_executed=shots,counts=counts,
                  ideal_noiseless_success_probability=math.sin((2*rounds+1)*theta)**2,
                  transpile_seconds=compiled_seconds,simulation_seconds=time.monotonic()-began,
                  circuit_operations=dict(compiled.count_ops()))
    if not winners:return {**result,'status':'sampling_miss'}
    # Never fall back to an unmeasured winner from the classical lookup table.
    nonce=start+winners[0];candidate[-8:]=nonce.to_bytes(8,'big')
    if int.from_bytes(hashlib.sha3_256(candidate).digest(),'big')>target:raise AssertionError('Sampled proof is invalid')
    if verify_reversible:
        from reversible_sha3 import verify_phase_oracle
        reference=verify_phase_oracle(bytes(candidate),target)
        if reference['digest']!=hashlib.sha3_256(candidate).hexdigest() or reference['phase']!=-1:raise AssertionError('Reversible oracle mismatch')
        result['reversible_reference']=reference
    return {**result,'status':'candidate','nonce':str(nonce),'extra_nonce':header[-40:-8].hex(),
            'blockId':hashlib.sha3_256(candidate).hexdigest()}


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as out:json.dump(value,out,sort_keys=True);out.write('\n')


def worker(request_path,result_path):
    try:
        data=json.loads(Path(request_path).read_text());result=simulate(data.pop('job'),**data)
    except ImportError:result={'status':'failed','error':'Install miners/requirements-simulator.txt in an isolated environment'}
    except Exception:result={'status':'failed','error':'Simulation rejected or failed; check work format and resource budgets'}
    write_json(result_path,result)


class Cancelled(Exception):pass

def main():
    if len(sys.argv)==4 and sys.argv[1]=='--worker':worker(sys.argv[2],sys.argv[3]);return 0
    p=argparse.ArgumentParser(description=__doc__)
    for option in ['binary','work','output']:p.add_argument('--'+option,type=Path,required=True)
    p.add_argument('--chain-id',required=True);p.add_argument('--address',required=True)
    p.add_argument('--bits',type=int,default=10);p.add_argument('--shots',type=int,default=128)
    p.add_argument('--seed',type=int,default=7);p.add_argument('--nonce-start',type=int)
    p.add_argument('--iterations',type=int);p.add_argument('--seconds',type=int,default=30)
    p.add_argument('--verify-reversible',action='store_true');p.add_argument('--receipt',type=Path)
    a=p.parse_args()
    if not 1<=a.bits<=12 or not 1<=a.shots<=1024 or not 1<=a.seconds<=300 or not 0<=a.seed<2**32 or (a.iterations is not None and not 0<=a.iterations<=64):p.error('Invalid simulator budget')
    if any(not re.fullmatch('[0-9a-f]{64}',value) or value=='0'*64 for value in [a.chain_id,a.address]):p.error('Expected nonzero lowercase chain ID and reward address')
    if os.path.lexists(a.output) or (a.receipt and os.path.lexists(a.receipt)):p.error('Output and receipt paths must not already exist')
    def cancel(*_):raise Cancelled()
    signal.signal(signal.SIGINT,cancel);signal.signal(signal.SIGTERM,cancel)
    deadline=time.monotonic()+a.seconds
    def run(argv,**options):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise subprocess.TimeoutExpired(argv,0)
        return subprocess.run(argv,timeout=remaining,check=True,**options)
    try:
        binary=a.binary.resolve(strict=True)
        data=run([str(binary),'inspect-work',str(a.work)],capture_output=True,text=True)
        job=json.loads(data.stdout);validate_job(job)
        if job['chainId']!=a.chain_id or job['rewardAddress']!=a.address:raise ValueError('Work identity mismatch')
        with tempfile.TemporaryDirectory(prefix='qcash-qpu-simulator-') as temporary:
            request=Path(temporary)/'request.json';response=Path(temporary)/'result.json'
            write_json(request,dict(job=job,bits=a.bits,shots=a.shots,seed=a.seed,nonce_start=a.nonce_start,
                                    iterations=a.iterations,verify_reversible=a.verify_reversible))
            env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
                 'RAYON_NUM_THREADS':'1','QISKIT_PARALLEL':'FALSE'}
            run([sys.executable,str(Path(__file__).resolve()),'--worker',str(request),str(response)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
            result=json.loads(response.read_text())
        if result['status']=='candidate':
            nonce=int(result['nonce']);start=int(result['nonce_start']);index=nonce-start
            if not 0<=index<1<<a.bits or result['counts'].get(f'{index:0{a.bits}b}',0)<=0:raise ValueError('Candidate was not measured')
            verified=run([str(binary),'complete-work',str(a.work),result['extra_nonce'],str(nonce),str(a.output)],capture_output=True,text=True)
            native=json.loads(verified.stdout)
            if native['blockId']!=result['blockId']:raise ValueError('Native verifier disagrees')
            result.update(status='solved',native_verified=True)
        result['total_wall_seconds']=a.seconds-(deadline-time.monotonic())
        if a.receipt:write_json(a.receipt,result)
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0 if result['status']=='solved' else 3 if result['status'] in ('empty_window','sampling_miss') else 2
    except Cancelled:print(json.dumps({'status':'cancelled'}));return 130
    except subprocess.TimeoutExpired:print(json.dumps({'status':'budget_ended'}));return 3
    except (OSError,ValueError,KeyError,subprocess.CalledProcessError):
        print(json.dumps({'status':'failed','error':'Check local dependencies, work identity, native verifier and output paths'}));return 2

if __name__=='__main__':raise SystemExit(main())
