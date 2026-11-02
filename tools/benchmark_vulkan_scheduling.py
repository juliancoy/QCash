#!/usr/bin/env python3
"""Balanced shader/workgroup sweep followed by held-out baseline confirmation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]

def summary(values):
    return dict(median=statistics.median(values), min=min(values), max=max(values), samples=values)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=ROOT/'build/qcash-vulkan')
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--resume', action='store_true', help='Resume checkpoints only with identical binaries and shader sources')
    p.add_argument('--shaders', default='pair32,compact32,row32,unroll32,final32')
    p.add_argument('--baseline-shader', default='pair32')
    p.add_argument('--confirm-case', help='Skip selection and confirm one preselected SHADER/WORKGROUP case')
    a = p.parse_args()
    known={'scalar64','pair32','compact64','compact32','row32','unroll32','final32','tree32','lean32','column32','tail32','early32'}
    shaders=a.shaders.split(',')
    if len(set(shaders))!=len(shaders) or not set(shaders)<=known or a.baseline_shader not in known:
        p.error('Invalid shader selection')
    if a.confirm_case:
        parts=a.confirm_case.split('/')
        if len(parts)!=2 or parts[0] not in known or parts[1] not in ['32','64','128','256']:
            p.error('Invalid confirmation case')
    baseline_case='previous '+a.baseline_shader+'/64'
    if a.output.exists() and not a.resume:
        p.error('Use a new output path')
    if hasattr(os, 'sched_getaffinity'):
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    cases = [(s, g) for s in shaders for g in [32, 64, 128, 256]]
    if a.confirm_case:
        cases=list(dict.fromkeys([(a.baseline_shader,64),(parts[0],int(parts[1]))]))
    receipts = {f'{s}/{g}': [] for s,g in cases}
    data = {'requested_confirmation_case':a.confirm_case, 'baseline_case': baseline_case, 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
            'source_diff_sha256': hashlib.sha256(subprocess.check_output(['git','diff','HEAD'])).hexdigest(),
            'binary_sha256': {k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in [('current',a.binary),('baseline',a.baseline)]},
            'shader_source_sha256': {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (ROOT/'shaders').iterdir() if f.is_file()},
            'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'attempts_per_round': 67108864, 'sweep_receipts': receipts,
            'host_load_start': os.getloadavg(),
            'cpu_affinity': sorted(os.sched_getaffinity(0)),
            'methodology': 'Seven randomized balanced sweep rounds; held-out eleven alternating baseline/candidate rounds. Full wall and GPU interval rates; mapped memory, GPU prefix, queue depth four. Active background load. No profiling counters inferred.'}
    if a.confirm_case:
        data['methodology']='Preselected confirmation only; eleven alternating baseline/candidate rounds. Candidate selected from prior GPU timing sweep. Full wall and GPU interval rates; mapped memory, GPU prefix, queue depth four. Active background load; no inferred profiling counters.'
    if a.resume:
        previous = json.loads(a.output.read_text())
        for key in ('binary_sha256', 'shader_source_sha256', 'attempts_per_round', 'baseline_case', 'requested_confirmation_case'):
            if previous.get(key) != data[key]:
                p.error('Cannot resume changed binaries, shader sources or attempt budget')
        if set(previous['sweep_receipts']) != set(receipts):
            p.error('Cannot resume a different case matrix')
        data = previous
        receipts = data['sweep_receipts']
    data['status'] = 'running'
    def save():
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(data,indent=2)+'\n')
    def interrupted(signum, _frame):
        data['status'] = 'interrupted'
        save()
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    def run(binary, *args):
        command = [str(binary.resolve()), *map(str,args)]
        r = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if r.returncode:
            data['status'] = 'failed'
            data['failure'] = {'command':command, 'exit_code':r.returncode,'stderr':r.stderr[-4000:]}
            save()
            raise RuntimeError('Benchmark failed; receipt saved')
        return json.loads(r.stdout)
    def rates(rows):
        return {kind:summary([data['attempts_per_round']/r['samples'][0][field] for r in rows]) for kind,field in [('wall','wall_seconds'),('gpu','gpu_seconds')]}
    with tempfile.TemporaryDirectory(prefix='qcash-scheduling-') as d:
        raw=bytearray.fromhex(json.loads((ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json').read_text())['fixture']['job']['header'])
        raw[100:132]=(1).to_bytes(32,'big')
        work=Path(d)/'hard.bin'; work.write_bytes(raw+b'\0'*4)
        def bench(binary,s,g):
            return run(binary,'bench',work,data['attempts_per_round'],1,'--shader',s,'--workgroup',g,'--memory','mapped')
        for repeat in range(0 if a.confirm_case else 7):
            order=cases.copy(); random.Random(713+repeat).shuffle(order)
            for s,g in order:
                if len(receipts[f'{s}/{g}']) > repeat:
                    continue
                receipts[f'{s}/{g}'].append(bench(a.binary,s,g))
            save(); print(f'Sweep {repeat+1}/7 complete',flush=True)
        data['sweep_rates']={k:rates(v) for k,v in receipts.items() if v}
        selected=a.confirm_case or max(data['sweep_rates'],key=lambda k:data['sweep_rates'][k]['wall']['median'])
        s,g=selected.split('/');g=int(g)
        data['selected_candidate']=selected
        data['selection_mode']='preselected_confirmation_only' if a.confirm_case else 'maximum_sweep_wall_median'
        data['diagnostics']={f'{shader}/{group}':run(a.binary,'info','--shader',shader,'--workgroup',group,'--diagnostics',1) for shader,group in cases}
        confirm=data.get('confirmation_receipts', {baseline_case:[],selected:[]})
        data['confirmation_receipts']=confirm
        for repeat in range(11):
            order=[(baseline_case,a.baseline,a.baseline_shader,64),(selected,a.binary,s,g)]
            if repeat%2:order.reverse()
            for label,binary,shader,group in order:
                if len(confirm[label]) > repeat:
                    continue
                confirm[label].append(bench(binary,shader,group))
            save();print(f'Confirmation {repeat+1}/11 complete',flush=True)
        data['confirmation_rates']={k:rates(v) for k,v in confirm.items()}
        data['host_load_end']=os.getloadavg()
        data['status']='complete'
        save()

if __name__ == '__main__':
    main()
