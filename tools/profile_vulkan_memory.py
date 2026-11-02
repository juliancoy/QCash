#!/usr/bin/env python3
"""Bounded Nsight capture; raw files stay in a new private directory.

GPU-wide counters require authorized access on some hosts. This tool never
changes NVIDIA driver policy. --sudo uses only noninteractive sudo for capture.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from read_vulkan_metrics import read_metrics
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,default=ROOT/'build/qcash-vulkan')
    p.add_argument('--tool',choices=['graphics','systems'],default='systems')
    p.add_argument('--from-sqlite',type=Path)
    p.add_argument('--importer',type=Path)
    p.add_argument('--ngfx',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--memory',choices=['mapped','mapped-local','staged'],default='mapped')
    p.add_argument('--sudo',action='store_true')
    p.add_argument('--passes',nargs='+',choices=['metrics','shader'],default=['metrics','shader'])
    a=p.parse_args()
    if a.output.exists():p.error('Use a new private output directory')
    a.output.mkdir(parents=True,mode=0o700)
    binary=a.binary.resolve(strict=not bool(a.from_sqlite))
    ngfx=a.ngfx or shutil.which('ngfx')
    if not ngfx:
        folder=ROOT.parent/'mlkem-vulkan/results/toolchains/nsight-graphics/extracted'
        ngfx=next((f for f in folder.rglob('ngfx') if f.is_file() and os.access(f,os.X_OK)),None)
    result={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'memory_policy':a.memory,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest() if binary.exists() else None,
            'privileged_capture':a.sudo,'status':'unavailable','commands':[],
            'metrics':{'dram_traffic':None,'cache_hit_rates':None,'runtime_spill_loads_stores':None},
            'scope':'No runtime metric is inferred from timestamps or pipeline statistics. An eight-second profiling-only window allows the injected profiler to attach.'}
    def finish(reason):
        result['reason']=reason
        (a.output/'status.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'status':result['status'],'reason':reason,'receipt':str(a.output/'status.json')}))
    if a.tool=='graphics' and not ngfx:finish('Nsight Graphics ngfx is not installed; pass --ngfx.');return
    if a.sudo and not a.from_sqlite and subprocess.run(['sudo','-n','true'],capture_output=True).returncode:
        finish('Noninteractive sudo is unavailable; authorize capture from a terminal. Driver policy was not changed.');return
    def attach_readings(path):
        readings=read_metrics(path)
        result['counter_readings']=readings
        result['status']=readings['status']
        rows=readings.get('metrics',[])
        result['metrics']={
            'dram_traffic':[r for r in rows if 'dram' in r['name'].lower()] or None,
            'cache_hit_rates':[r for r in rows if 'hit' in r['name'].lower() and any(x in r['name'].lower() for x in ['l1','l2','cache'])] or None,
            'runtime_spill_loads_stores':[r for r in rows if 'spill' in r['name'].lower() or ('local' in r['name'].lower() and any(x in r['name'].lower() for x in ['load','store']))] or None}
        finish(readings.get('reason') or 'Recorded native-unit GPU-wide readings attached; other GPU clients may contribute.')
    if a.from_sqlite:
        attach_readings(a.from_sqlite);return
    fixture=json.loads((ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json').read_text())['fixture']['job']
    raw=bytearray.fromhex(fixture['header']);raw[100:132]=(1).to_bytes(32,'big')
    work=a.output/'hard-work.bin';work.write_bytes(raw+b'\0'*4)
    env={key:os.environ[key] for key in ['PATH','HOME','DISPLAY','XDG_RUNTIME_DIR','LD_LIBRARY_PATH'] if key in os.environ}
    env['QT_QPA_PLATFORM']='offscreen'
    if a.tool=='systems':
        nsys=shutil.which('nsys')
        if not nsys:finish('Nsight Systems is not installed.');return
        raw=a.output/'capture'
        command=(['sudo','-n'] if a.sudo else [])+[nsys,'profile','--trace=vulkan','--sample=none','--cpuctxsw=none',
            '--gpu-metrics-device=0','--force-overwrite=true','-o',str(raw.absolute()),str(binary),
            'bench',str(work.absolute()),'16777216','20','--memory',a.memory,'--profile-min-seconds','8']
        result['commands'].append({'tool':'systems','command':command})
        print('Capturing Nsight Systems Vulkan timeline and available GPU metrics (8 seconds)',flush=True)
        try:
            capture=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            finish('Nsight Systems capture timed out; raw files, if any, are retained.');return
        (a.output/'systems.log').write_text(capture.stdout+capture.stderr)
        result['commands'][-1]['exit_code']=capture.returncode
        if capture.returncode:finish('Nsight Systems capture failed; inspect private systems.log.');return
        import_env=dict(env)
        if not raw.with_suffix('.nsys-rep').exists():
            importer=a.importer or shutil.which('QdstrmImporter')
            if not importer:
                base=ROOT.parent/'mlkem-vulkan/results/shader-profile'
                candidate=base/'extracted/usr/lib/nsight-systems/host-linux-x64/QdstrmImporter'
                if candidate.is_file():
                    importer=candidate
                    if (base/'local-lib').is_dir():import_env['LD_LIBRARY_PATH']=str(base/'local-lib')+(':'+import_env['LD_LIBRARY_PATH'] if import_env.get('LD_LIBRARY_PATH') else '')
            if not importer:finish('Capture retained, but a matching QdstrmImporter is unavailable; pass --importer.');return
            imported=subprocess.run([str(importer),'-f','-i',str(raw.with_suffix('.qdstrm').absolute()),'-o',str(raw.with_suffix('.nsys-rep').absolute())],env=import_env,capture_output=True,text=True,timeout=60)
            (a.output/'import.log').write_text(imported.stdout+imported.stderr)
            if imported.returncode:finish('Nsight importer failed; raw capture retained.');return
        exported=subprocess.run([nsys,'export','--force-overwrite=true','--type=sqlite','--output',str(raw.with_suffix('.sqlite').absolute()),str(raw.with_suffix('.nsys-rep').absolute())],env=import_env,capture_output=True,text=True,timeout=60)
        (a.output/'export.log').write_text(exported.stdout+exported.stderr)
        if exported.returncode:finish('Nsight SQLite export failed; raw capture retained.');return
        attach_readings(raw.with_suffix('.sqlite'));return
    completed=[]
    for mode in a.passes:
        destination=a.output/mode;destination.mkdir()
        command=([shutil.which('sudo') or 'sudo','-n','env','QT_QPA_PLATFORM=offscreen'] if a.sudo else [])+[
            str(ngfx),'--activity=GPU Trace Profiler','--platform=Linux (x86_64)',f'--exe={binary}',f'--dir={ROOT}',
            f'--args=bench {work.absolute()} 16777216 20 --memory {a.memory} --profile-min-seconds 8',
            '--start-after-submits=2','--limit-to-submits=12','--max-duration-ms=2000',
            '--architecture=Ampere GA10x','--metric-set-name=Throughput Metrics',
            '--set-gpu-clocks=unaltered','--auto-export',f'--output-dir={destination.absolute()}']
        if mode=='shader':command+=['--real-time-shader-profiler']
        print(f'Capturing {mode} ({a.memory}); timeout 60 seconds',flush=True)
        record={'pass':mode,'command':command};result['commands'].append(record)
        began=time.monotonic()
        try:
            run=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
            text=run.stdout+run.stderr;record.update(exit_code=run.returncode,wall_seconds=time.monotonic()-began)
        except subprocess.TimeoutExpired as error:
            text=str(error);record.update(exit_code=None,wall_seconds=time.monotonic()-began)
        (a.output/(mode+'.log')).write_text(text)
        files=[{'path':str(f.relative_to(a.output)),'bytes':f.stat().st_size} for f in destination.rglob('*') if f.is_file()]
        record['files']=files
        if re.search(r'ERR_NVGPUCTRPERM|INSUFFICIENT_PERMISSIONS|[Ii]nsufficient [Pp]rivilege|[Pp]ermission.{0,80}performance counter|performance counter.{0,80}[Pp]ermission',text):
            finish('Profiler reported insufficient hardware-counter permissions. Rerun this tool with --sudo from an authorized terminal.');return
        if record['exit_code']!=0:
            finish(f'Nsight Graphics {mode} capture failed; inspect the private {mode}.log.');return
        completed.extend(files)
    result['status']='captured_raw_data' if completed else 'unavailable'
    finish('Raw profiler exports saved for metric inspection; no values have been fabricated.' if completed else 'Profiler returned without exported metric files; inspect private logs.')


if __name__=='__main__':main()
