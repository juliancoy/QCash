#!/usr/bin/env python3
"""Compare mapped and staged Vulkan memory; GPU counters are separate evidence."""
import argparse
from contextlib import ExitStack
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
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repeats',type=int,default=7)
    p.add_argument('--attempts',type=int,default=16777216)
    a=p.parse_args()
    if a.output.exists():p.error('Use a new output file')
    if not 3<=a.repeats<=15 or not 1048576<=a.attempts<=100000000:p.error('Invalid measurement budget')
    if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    binary=str(a.binary.resolve(strict=True));native=str(ROOT/'build/qcash')
    policies=['mapped','mapped-local','staged']
    receipts={mode:[] for mode in policies};debug={mode+' · '+layout:[] for mode in policies for layout in ['aos','soa']}
    load_start=os.getloadavg()
    def run(*argv):
        value=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=120)
        if value.returncode:
            failure={'command':list(map(str,argv)),'exit_code':value.returncode,'stderr':value.stderr[-4000:],'completed_sustained_receipts':receipts,'completed_debug_receipts':debug}
            a.output.with_suffix('.failed.json').write_text(json.dumps(failure,indent=2)+'\n')
            raise RuntimeError('Benchmark command failed; a failure receipt was retained')
        return json.loads(value.stdout)
    fixture=json.loads((ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json').read_text())['fixture']['job']
    hot={mode:[] for mode in policies};ready={}
    with tempfile.TemporaryDirectory(prefix='qcash-memory-study-') as temporary:
        d=Path(temporary);raw=bytearray.fromhex(fixture['header'])
        (d/'work.bin').write_bytes(raw+b'\0'*4);raw[100:132]=(1).to_bytes(32,'big');(d/'hard.bin').write_bytes(raw+b'\0'*4)
        for repeat in range(a.repeats):
            order=policies[repeat%3:]+policies[:repeat%3]
            if repeat%2:order=order[::-1]
            for mode in order:
                receipts[mode].append(run(binary,'bench',d/'hard.bin',a.attempts,1,'--memory',mode))
                for layout in (['aos','soa'] if repeat%2==0 else ['soa','aos']):
                    debug[mode+' · '+layout].append(run(binary,'bench',d/'hard.bin',4096,20,'--memory',mode,'--capture-layout',layout))
            print(f'Memory policy and capture comparison {repeat+1}/{a.repeats}',flush=True)
        with ExitStack() as stack:
            workers={mode:stack.enter_context(VulkanWorker(binary,deadline=time.monotonic()+120,options=['--memory',mode])) for mode in policies}
            for mode,worker in workers.items():
                ready[mode]=worker.ready
                report=worker.solve(d/'work.bin',d/f'warmup-{mode}.bin',256,fixture['chainId'],fixture['rewardAddress'])
                assert report['status']=='solved' and report['native_verified']
            for repeat in range(a.repeats):
                order=policies[repeat%3:]+policies[:repeat%3]
                if repeat%2:order=order[::-1]
                for mode in order:
                    output=d/f'proof-{mode}-{repeat}.bin'
                    began=time.perf_counter();report=workers[mode].solve(d/'work.bin',output,256,fixture['chainId'],fixture['rewardAddress'])
                    elapsed=time.perf_counter()-began
                    assert report['status']=='solved' and report['native_verified']
                    checked=run(native,'inspect-work',output);digest=hashlib.sha3_256(bytes.fromhex(checked['header'])).hexdigest()
                    assert digest==report['blockId'] and int(digest,16)<=int(fixture['target'],16)
                    hot[mode].append({**report,'request_wall_seconds':elapsed})
    result={'metadata':{'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'platform':platform.platform(),
        'device':run(binary,'info')['device'],'cpu_affinity':list(os.sched_getaffinity(0)),
        'load_start':load_start,'load_end':os.getloadavg(),
        'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        'source_tracked_dirty':bool(subprocess.check_output(['git','-C',str(ROOT),'diff','HEAD','--name-only'],text=True).strip()),
        'binary_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        'attempts_per_round':a.attempts,'repeats':a.repeats,
        'topology':{mode:rows[0]['memory_topology'] for mode,rows in receipts.items()},
        'normal_attempts_per_second':{mode:summary([a.attempts/r['samples'][0]['wall_seconds'] for r in rows]) for mode,rows in receipts.items()},
        'normal_gpu_interval_attempts_per_second':{mode:summary([a.attempts/r['samples'][0]['gpu_seconds'] for r in rows]) for mode,rows in receipts.items()},
        'capture_gpu_seconds_per_4096':{case:summary([statistics.median(s['gpu_seconds'] for s in r['samples']) for r in rows]) for case,rows in debug.items()},
        'capture_wall_seconds_per_4096':{case:summary([statistics.median(s['wall_seconds'] for s in r['samples']) for r in rows]) for case,rows in debug.items()},
        'warm_verified_proof_seconds':{mode:summary([r['request_wall_seconds'] for r in rows]) for mode,rows in hot.items()},
        'sustained_receipts':receipts,'capture_receipts':debug,'worker_ready':ready,'warm_proof_receipts':hot,
        'runtime_counters':{'dram_bytes':None,'cache_hit_rates':None,'spill_loads_stores':None},
        'methodology':['Normal mining completes every nonce on an identical hard-target 268-byte header. No per-nonce payload is uploaded or downloaded.',
        'Explicit upload/readback bytes count vkCmdCopyBuffer payloads, not physical DRAM/cache/PCIe traffic. Mapped CPU reads/writes are not zero hardware traffic.',
        'Balanced policy order, one host logical CPU, active background workloads. Warm proof IPC excludes its separately recorded worker startup.',
        'Capture is a debug-only workload of 4096 candidates, 20 iterations per session; each plotted point is the median of one session, with repeated sessions summarized.',
        'GPU timestamps bracket compute and (for staged mode) uploads, but end before result-copy/readback; CPU wall time includes copies, waits and digest decoding.',
        'SoA stores adjacent nonce words adjacently across a warp; AoS stores complete per-nonce digest records. Source layout is known; actual cache traffic requires hardware counters.',
        'Warm proof uses a classically preselected 256-nonce winning window, not expected network block times. Native SHA3 verifies each output.',
        'Null runtime counter fields mean unmeasured, not zero. Profiling availability has a separate receipt.']}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
    print(a.output)


if __name__=='__main__':main()
