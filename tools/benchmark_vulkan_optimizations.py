#!/usr/bin/env python3
"""Same-host before/after GPU measurements, warm-worker proofs and raw diagnostics."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'miners'))
from vulkan_worker import VulkanWorker


def summary(values):
    return dict(median=statistics.median(values),min=min(values),max=max(values),samples=values)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=ROOT/'build/qcash-vulkan')
    p.add_argument('--baseline',type=Path,default=ROOT/'build-gpu-baseline/qcash-vulkan')
    p.add_argument('--fixture',type=Path,default=ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--selected-shader',choices=['scalar64','pair32','compact64','compact32'],help='Confirm a previously selected shader rather than selecting on this run')
    p.add_argument('--repeats',type=int,default=5)
    p.add_argument('--attempts',type=int,default=16777216)
    args=p.parse_args()
    if not 3<=args.repeats<=15 or not 1048576<=args.attempts<=100000000:p.error('Invalid measurement budget')
    if args.output.exists():p.error('Output must be new')
    if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    binary=str(args.binary.resolve(strict=True));baseline=str(args.baseline.resolve(strict=True))
    native=str(ROOT/'build/qcash')
    def run(*argv):
        r=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=120)
        if r.returncode:raise RuntimeError(r.stderr[-1000:]+r.stdout[-1000:])
        return json.loads(r.stdout)
    fixture=json.loads(args.fixture.read_text())['fixture']['job']
    cases={'Previous Vulkan':(baseline,[]),'Cached commands · scalar64':(binary,['--queue-depth',1,'--shader','scalar64'])}
    for shader in ['scalar64','pair32','compact64','compact32']:
        cases['Queued · '+shader]=(binary,['--queue-depth',4,'--shader',shader])
    receipts={name:[] for name in cases}
    latency={'Previous GPU · new process':[],'Current GPU · new process':[],
             'Warm worker · CPU prefix':[],'Warm worker · GPU prefix':[],'Native CPU · new process':[]}
    worker_reports={name:[] for name in ['Warm worker · CPU prefix','Warm worker · GPU prefix']}
    load_start=os.getloadavg()
    with tempfile.TemporaryDirectory(prefix='qcash-gpu-comparison-') as directory:
        d=Path(directory)
        work=bytearray.fromhex(fixture['header'])+b'\0'*4
        (d/'work.bin').write_bytes(work)
        work[100:132]=(1).to_bytes(32,'big');(d/'hard.bin').write_bytes(work)
        # Same target/header across all sustained runs. Verify every proof later.
        for repeat in range(args.repeats):
            order=list(cases)
            order=order[repeat%len(order):]+order[:repeat%len(order)]
            if repeat%2:order.reverse()
            for name in order:
                exe,options=cases[name]
                report=run(exe,'bench',d/'hard.bin',args.attempts,1,'--batch',262144,*options)
                receipts[name].append(report)
            print(f'Sustained comparison {repeat+1}/{args.repeats}',flush=True)
        rates={name:summary([args.attempts/r['samples'][0]['wall_seconds'] for r in reports]) for name,reports in receipts.items()}
        selected=args.selected_shader or max((shader for shader in ['scalar64','pair32','compact64','compact32']),
                     key=lambda s:rates['Queued · '+s]['median'])
        diagnostics={shader:run(binary,'info','--shader',shader,'--diagnostics',1) for shader in ['scalar64','pair32','compact64','compact32']}
        worker_ready={}
        for name,mode in [('Warm worker · CPU prefix','cpu'),('Warm worker · GPU prefix','gpu')]:
            reports=[]
            with VulkanWorker(binary,deadline=time.monotonic()+90,options=['--shader',selected,'--prepare',mode]) as worker:
                worker_ready[name]=worker.ready
                for repeat in range(args.repeats+1):
                    output=d/f'worker-{mode}-{repeat}.bin'
                    began=time.perf_counter();parent_cpu=time.process_time()
                    report=worker.solve(d/'work.bin',output,256,fixture['chainId'],fixture['rewardAddress'])
                    wall=time.perf_counter()-began;parent_cpu=time.process_time()-parent_cpu
                    assert report['status']=='solved' and report['native_verified']
                    checked=run(native,'inspect-work',output)
                    digest=hashlib.sha3_256(bytes.fromhex(checked['header'])).hexdigest()
                    assert digest==report['blockId'] and int(digest,16)<=int(fixture['target'],16)
                    if repeat:
                        latency[name].append(wall)
                        reports.append({**report,'parent_cpu_seconds':parent_cpu,'request_wall_seconds':wall})
            worker_reports[name]=reports
        for repeat in range(args.repeats):
            rows=[('Previous GPU · new process',baseline,[]),('Current GPU · new process',binary,['--shader',selected]),('Native CPU · new process',native,[])]
            if repeat%2:rows.reverse()
            for name,exe,options in rows:
                output=d/f'{repeat}-{exe==baseline}-{exe==native}.bin'
                began=time.perf_counter();report=run(exe,'solve',d/'work.bin',output,256,*options)
                latency[name].append(time.perf_counter()-began)
                checked=run(native,'inspect-work',output)
                digest=hashlib.sha3_256(bytes.fromhex(checked['header'])).hexdigest()
                assert digest==report['blockId'] and int(digest,16)<=int(fixture['target'],16)
    metadata={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'platform':platform.platform(),
        'cpu_model':next(line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),
        'device':run(binary,'info')['device'],'load_start':load_start,'load_end':os.getloadavg(),
        'cpu_affinity':list(os.sched_getaffinity(0)),'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        'source_dirty':bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip()),
        'baseline_source_commit':'c3c8ac4','baseline_binary_sha256':hashlib.sha256(Path(baseline).read_bytes()).hexdigest(),
        'current_binary_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'shader_source_sha256':{file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in (ROOT/'shaders').iterdir() if file.is_file()}}
    result={'metadata':metadata,'attempts_per_round':args.attempts,'repeats':args.repeats,'fixture':fixture,
        'selected_shader':selected,'selection_mode':'prior_selection_confirmation' if args.selected_shader else 'maximum_observed_dispatch_throughput','throughput_attempts_per_second':rates,
        'kernel_attempts_per_second':{name:summary([args.attempts/r['samples'][0]['gpu_seconds'] for r in reports]) for name,reports in receipts.items()},
        'proof_latency_seconds':{name:summary(samples) for name,samples in latency.items()},
        'sustained_receipts':receipts,'diagnostics':diagnostics,'worker_ready':worker_ready,'worker_proof_receipts':worker_reports,
        'methodology':['All sustained cases use the same hard-target SHA3 header and complete all requested attempts.',
        'Previous executable built from c3c8ac4; new candidates use the current source. Process setup excluded from sustained sample timing.',
        'Balanced case order, one host logical CPU, background work remains active. Queue depth four groups four dispatches per fence wait.',
        'Warm proof latency includes private-pipe IPC, work-file read, prefix preparation, GPU search, one independent CPU SHA3 verification and exclusive file creation.',
        'Worker startup and one warm-up job are excluded from warm latency, and retained separately in handshake receipts.',
        'Known-winning 256-nonce fixture is preselected classically; these are conditional proof latencies, not expected network block times.',
        'Physical GPU pipeline statistics are driver-reported. They do not directly measure runtime stalls or achieved occupancy.',
        'Selection mode distinguishes candidate selection from confirmation of a prior choice. These are measured candidates under the recorded load, not peak-performance guarantees.',
        'GPU execution intervals include inter-dispatch barriers. Implausible per-thread local-memory statistics are retained/flagged and not used for occupancy calculations.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
    print('Selected',selected,flush=True)
    print(args.output)


if __name__=='__main__':main()
