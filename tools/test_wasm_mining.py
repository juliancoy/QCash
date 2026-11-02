#!/usr/bin/env python3
"""Compare explicit SIMD128 1/2/4/8-worker WASM, WebGPU and Vulkan on the host."""
import argparse
import functools
import hashlib
import http.server
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import tempfile
import threading
import time
from playwright.sync_api import sync_playwright
from test_webgpu_mining import Handler,ROOT

def cases():
    rows=[];rng=random.Random(71)
    fixture=json.loads((ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json').read_text())['fixture']['job']
    for trial in range(3):
        raw=bytearray(rng.randbytes(268));raw[100:132]=bytes(32) if trial==0 else bytes([255])*32 if trial==1 else rng.randbytes(32)
        for j,start in enumerate([0,0xfffffffc,2**64-257]):
            count=[1,65,257][(trial+j)%3];rows.append((raw.copy(),start,count))
    rows.append((bytearray.fromhex(fixture['header']),int(fixture['header'][-16:],16),257))
    rows.append((bytearray(rng.randbytes(268)),0xfffffff0,4095))
    out=[]
    for raw,start,count in rows:
        hashes=[]
        for i in range(count):
            raw[260:268]=(start+i).to_bytes(8,'big');hashes.append(hashlib.sha3_256(raw).hexdigest())
        winner=next((i for i,h in enumerate(hashes) if bytes.fromhex(h)<=raw[100:132]),None)
        out.append(dict(header=raw.hex(),start=str(start),count=count,hashes=hashes,winner=winner))
    return out,fixture

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--attempts',type=int,default=1048576);p.add_argument('--repeats',type=int,default=7);p.add_argument('--warm-seconds',type=float,default=1.0);p.add_argument('--cold',action='store_true',help='Retain first-after-idle behavior instead of per-sample warm-up');a=p.parse_args()
    if a.output.exists():p.error('Use a new receipt path')
    if not 65536<=a.attempts<=16777216 or not 3<=a.repeats<=15 or not 0<=a.warm_seconds<=3:p.error('Invalid sample budget')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    vectors,fixture=cases();server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'miners')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    paths=[*sorted((ROOT/'miners/wasm').glob('*')),*sorted((ROOT/'miners/webgpu').glob('*.mjs')),Path(__file__)]
    hashes=lambda:{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths if f.is_file()}
    result={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_hashes':hashes(),'attempts':a.attempts,'repeats':a.repeats,'warm_each_sample':not a.cold,'minimum_browser_warmup_seconds':0 if a.cold else a.warm_seconds,'platform':platform.platform(),'cpu':next(line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),'host_load_start':os.getloadavg(),'playwright':importlib.metadata.version('playwright'),'methodology':'Same hard-target header, cached prefix and pruned final Keccak round for all backends. Equal requested candidates per run; full wall time includes IPC/submission/completion and winner verification. Seven rotated/reversed rounds by default; warm_each_sample controls discarded full-range warm-ups before each timed browser case until minimum_browser_warmup_seconds is reached and measured_sample_index identifies the native sample; session initialization and template preparation excluded. Explicit i64x2 SIMD; persistent independent Web Workers with private memories, not Emscripten pthreads. No physical traffic, energy or CPU utilization inferred.'}
    def save():a.output.write_text(json.dumps(result,indent=2)+'\n')
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/google-chrome',headless=False,args=['--no-sandbox','--enable-unsafe-webgpu','--use-angle=vulkan','--enable-features=Vulkan','--ignore-gpu-blocklist','--enable-gpu'])
            page=browser.new_page();page.set_default_timeout(120000);logs=[];page.on('console',lambda m:logs.append({'type':m.type,'text':m.text}));result['console']=logs;result['browser']=browser.version
            page.goto(f'http://localhost:{server.server_port}/webgpu/')
            result['validation']=page.evaluate("""async cases=>{
              const {createWasmMiner}=await import('../wasm/miner.mjs');
              const bytes=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
              const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
              const check=(v,m)=>{if(!v)throw Error(m);};
              const rejects=async(f,m)=>{let bad=false;try{await f();}catch{bad=true;}check(bad,m);};
              let checked=0;const infos=[];
              for(const threads of [1,2,4,8]){
                const miner=await createWasmMiner({threads});infos.push(miner.info);
                try{
                  await rejects(()=>miner.run(0n,1),'unprepared accepted');
                  for(const c of cases){
                    await miner.prepare(bytes(c.header));
                    const r=await miner.run(BigInt(c.start),c.count,{capture:true});check(r.winner===c.winner,'winner mismatch');
                    for(let i=0;i<c.count;i++){check(hex(r.digests.subarray(i*32,i*32+32))===c.hashes[i],'digest mismatch');checked++;}
                    const normal=await miner.run(BigInt(c.start),c.count);check(normal.winner===c.winner&&normal.digests===null,'normal mismatch');
                    if(normal.proof)check(normal.proof.verified&&normal.proof.blockId===c.hashes[c.winner],'unverified proof');
                  }
                  await rejects(()=>miner.run((1n<<64n)-1n,2),'overflow');await rejects(()=>miner.run(0n,0),'empty scan');
                  await rejects(()=>miner.run(0n,4097,{capture:true}),'oversized capture');await rejects(()=>miner.prepare(new Uint8Array(267)),'short header');
                  const controller=new AbortController();controller.abort();await rejects(()=>miner.run(0n,1,{signal:controller.signal}),'preabort ignored');
                  const job=miner.prepare(bytes(cases[0].header));await rejects(()=>miner.run(0n,1),'concurrent use accepted');await job;
                }finally{miner.dispose();}await rejects(()=>miner.run(0n,1),'disposed use accepted');
              }
              const aborted=await createWasmMiner({threads:8});await aborted.prepare(bytes(cases[0].header));
              const controller=new AbortController();const job=aborted.run(0n,16777216,{signal:controller.signal});controller.abort();
              await rejects(()=>job,'in-flight abort ignored');await rejects(()=>aborted.run(0n,1),'aborted session reused');aborted.dispose();
              return {checked,infos,crossOriginIsolated};
            }""",vectors)
            print(f"Validated {result['validation']['checked']} independent OpenSSL SIMD digests",flush=True);save()
            hard=bytearray.fromhex(fixture['header']);hard[100:132]=(1).to_bytes(32,'big')
            result['sessions']=page.evaluate("""async ({hard,attempts})=>{
              const {createWasmMiner}=await import('../wasm/miner.mjs');const {createWebGPUMiner}=await import('./miner.mjs');
              const bytes=Uint8Array.from(hard.match(/../g),x=>parseInt(x,16));window.study={miners:{},start:new DataView(bytes.buffer).getBigUint64(260,false)};
              for(const threads of [1,2,4,8])study.miners['wasm-'+threads]=await createWasmMiner({threads});
              study.miners.webgpu=await createWebGPUMiner();const info=study.miners.webgpu.info;
              if(info.adapter.isFallbackAdapter||JSON.stringify(info.adapter).toLowerCase().match(/swiftshader|llvmpipe|software/))throw Error('Software GPU cannot be labeled hardware');
              for(const miner of Object.values(study.miners)){await miner.prepare(bytes);await miner.run(study.start,attempts);}
              return Object.fromEntries(Object.entries(study.miners).map(([k,m])=>[k,m.info]));
            }""",{'hard':hard.hex(),'attempts':a.attempts})
            result['samples']={key:[] for key in ['wasm-1','wasm-2','wasm-4','wasm-8','webgpu','vulkan']}
            result['warmup_samples']={key:[] for key in result['samples']}
            with tempfile.TemporaryDirectory(prefix='qcash-simd-study-') as tmp:
                work=Path(tmp)/'hard.bin';work.write_bytes(hard+b'\0'*4)
                for repeat in range(a.repeats):
                    keys=list(result['samples']);keys=keys[repeat%len(keys):]+keys[:repeat%len(keys)]
                    if repeat%2:keys.reverse()
                    for key in keys:
                        if key=='vulkan':
                            receipt=json.loads(subprocess.check_output([str(ROOT/'build/qcash-vulkan'),'bench',str(work),str(a.attempts),'1' if a.cold else '2','--shader','final32'],text=True,timeout=60))
                            receipt['measured_sample_index']=0 if a.cold else 1
                            if not a.cold:result['warmup_samples'][key].append(receipt['samples'][0])
                            result['samples'][key].append(receipt)
                        else:
                            run="""async ({key,count})=>{const r=await study.miners[key].run(study.start,count);if(r.winner!==null)throw Error('Unexpected hard-target winner');return {...r,proof:null,digests:null};}"""
                            if not a.cold:
                                warming=time.monotonic();warmups=[]
                                while not warmups or time.monotonic()-warming<a.warm_seconds:
                                    warmups.append(page.evaluate(run,{'key':key,'count':a.attempts}))
                                result['warmup_samples'][key].append(warmups)
                            receipt=page.evaluate(run,{'key':key,'count':a.attempts})
                            result['samples'][key].append(receipt)
                    print(f'SIMD/WASM/WebGPU/Vulkan comparison {repeat+1}/{a.repeats}',flush=True);save()
                result['warm_proofs']=page.evaluate("""async fixture=>{
                  const header=Uint8Array.from(fixture.header.match(/../g),x=>parseInt(x,16)),start=new DataView(header.buffer).getBigUint64(260,false),out={};
                  for(const [key,miner] of Object.entries(study.miners)){
                    await miner.prepare(header);out[key]=[];
                    for(let i=0;i<8;i++){const r=await miner.run(start,256);if(!r.proof?.verified)throw Error('Missing verified fixture proof');if(i)out[key].push({wallSeconds:r.wallSeconds,blockId:r.proof.blockId,nonce:r.proof.nonce});}
                  }
                  return out;
                }""",fixture)
                for rows in result['warm_proofs'].values():
                    for r in rows:
                        raw=bytearray.fromhex(fixture['header']);raw[260:268]=int(r['nonce']).to_bytes(8,'big');assert hashlib.sha3_256(raw).hexdigest()==r['blockId']
            page.evaluate('Object.values(study.miners).forEach(m=>m.dispose())');browser.close()
            if hashes()!=result['source_hashes']:raise RuntimeError('Source changed during study')
            result['native_binary_sha256']=hashlib.sha256((ROOT/'build/qcash-vulkan').read_bytes()).hexdigest()
            result['host_load_end']=os.getloadavg();result['status']='passed';save()
    except Exception as error:result['status']='failed';result['error']=str(error);save();raise
    finally:server.shutdown()
    print(a.output)
if __name__=='__main__':main()
