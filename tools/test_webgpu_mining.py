#!/usr/bin/env python3
"""Host Chrome QCash WebGPU correctness and native comparison; no network mining."""
import argparse
import functools
import hashlib
import http.server
import json
import platform
import random
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args): pass
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        super().end_headers()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--software',action='store_true',help='Explicit correctness-only software adapter; never hardware speed')
    p.add_argument('--headless',action='store_true')
    p.add_argument('--native',type=Path,default=ROOT/'build/qcash-vulkan')
    a=p.parse_args()
    if a.output.exists():p.error('Use a new receipt path')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    cases=[];rng=random.Random(71)
    for trial in range(3):
        header=bytearray(rng.randbytes(268));header[100:132]=bytes(32) if trial==0 else bytes([255])*32 if trial==1 else rng.randbytes(32)
        for j,start in enumerate([0,0xfffffffc,2**64-257]):
            count=[1,65,257][(trial+j)%3];digests=[]
            for i in range(count):
                header[260:268]=(start+i).to_bytes(8,'big');digests.append(hashlib.sha3_256(header).hexdigest())
            winner=next((i for i,h in enumerate(digests) if bytes.fromhex(h)<=header[100:132]),None)
            cases.append(dict(header=header.hex(),start=str(start),count=count,digests=digests,winner=winner))
    fixture=json.loads((ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json').read_text())['fixture']['job']
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'miners/webgpu')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    flags=['--no-sandbox','--enable-unsafe-webgpu','--use-angle=vulkan']
    if a.software:flags=['--no-sandbox','--enable-unsafe-webgpu','--use-angle=swiftshader','--enable-features=Vulkan','--use-vulkan=swiftshader']
    else:flags+=['--enable-features=Vulkan','--ignore-gpu-blocklist','--enable-gpu']
    result={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'platform':platform.platform(),'flags':flags,'software_requested':a.software,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'methodology':'Hardware identified by WebGPU adapter plus native device report; eight browser configurations in seven randomized rounds with interleaved native control, then eleven alternating confirmation pairs. 16,777,216 candidates per round. Initialization/prefix preparation excluded. Wall includes submission, mapping and winner verification; GPU timestamps cover mining pass only. Warm proof uses a preselected 256-nonce winning window, not network block time. No physical traffic or register/occupancy counters inferred.', 'source_hashes':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((ROOT/'miners/webgpu').glob('*')) if f.is_file()}}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/google-chrome',headless=a.headless or a.software,args=flags)
            page=browser.new_page();page.set_default_timeout(120000);logs=[]
            page.on('console',lambda m:logs.append({'type':m.type,'text':m.text}))
            page.goto(f'http://localhost:{server.server_port}/')
            result['browser']=browser.version
            result['console']=logs
            result['validation']=page.evaluate('''async cases=>{
              const {createWebGPUMiner}=await import('./miner.mjs');
              const fromHex=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
              const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
              const check=(v,m)=>{if(!v)throw Error(m);};
              const rejects=async(f,m)=>{let rejected=false;try{await f();}catch{rejected=true;}check(rejected,m);};
              let checked=0;const devices=[];
              for(const workgroup of [32,64,128,256])for(const pruned of [false,true]){
                const miner=await createWebGPUMiner({workgroup,pruned});devices.push(miner.info);
                try{
                  await rejects(()=>miner.run(0n,1),'unprepared accepted');
                  for(const c of cases){
                    await miner.prepare(fromHex(c.header));
                    const r=await miner.run(BigInt(c.start),c.count,{capture:true});
                    check(r.winner===c.winner,'capture winner mismatch');
                    for(let i=0;i<c.count;i++){check(hex(r.digests.subarray(i*32,i*32+32))===c.digests[i],'digest mismatch');checked++;}
                    const normal=await miner.run(BigInt(c.start),c.count);
                    check(normal.winner===c.winner,'normal winner mismatch');
                    check(normal.digests===null,'normal captured digests');
                    check(normal.explicitReadbackBytes===(miner.info.timestamps?20:4),'normal readback size');
                    if(normal.proof)check(normal.proof.verified&&normal.proof.blockId===c.digests[c.winner],'proof unverified');
                  }
                  await rejects(()=>miner.run((1n<<64n)-1n,2),'overflow accepted');
                  await rejects(()=>miner.run(0n,4097,{capture:true}),'oversized capture accepted');
                  await rejects(()=>miner.run(0n,0),'zero count accepted');
                  await rejects(()=>miner.prepare(new Uint8Array(267)),'short header accepted');
                  const controller=new AbortController();controller.abort();
                  await rejects(()=>miner.run(0n,1,{signal:controller.signal}),'preabort ignored');
                  const pending=miner.prepare(fromHex(cases[0].header));
                  await rejects(()=>miner.run(0n,1),'concurrent use accepted');await pending;
                }finally{miner.dispose();}
                await rejects(()=>miner.run(0n,1),'disposed accepted');
              }
              // Force multiple dispatches within a debug capture to check every
              // dynamic uniform offset and index, including partial final groups.
              for(const workgroup of [32,64,128,256]){
                const grouped=await createWebGPUMiner({workgroup,maxBatch:4096,dispatchSize:256});
                try{
                  const c=cases.find(c=>c.count===257);await grouped.prepare(fromHex(c.header));
                  const r=await grouped.run(BigInt(c.start),c.count,{capture:true});
                  for(let i=0;i<c.count;i++){check(hex(r.digests.subarray(i*32,i*32+32))===c.digests[i],'grouped digest mismatch');checked++;}
                  check(r.winner===c.winner&&r.explicitUploadBytes===32,'grouped result mismatch');
                }finally{grouped.dispose();}
              }
              const nativeAdapter=await navigator.gpu.requestAdapter({powerPreference:'high-performance'});
              const noTimestampGPU={requestAdapter:async()=>({features:{has:()=>false},limits:nativeAdapter.limits,info:nativeAdapter.info,requestDevice:d=>nativeAdapter.requestDevice(d)})};
              const untimed=await createWebGPUMiner({gpu:noTimestampGPU});
              try{await untimed.prepare(fromHex(cases[0].header));const r=await untimed.run(BigInt(cases[0].start),1);check(r.gpuSeconds===null&&r.explicitReadbackBytes===4,'timestamp-free path');}finally{untimed.dispose();}
              await rejects(()=>createWebGPUMiner({dispatchSize:65}),'unaligned dispatch size accepted');
              await rejects(()=>createWebGPUMiner({dispatchSize:32}),'unbounded dispatch count accepted');
              const cancelled=await createWebGPUMiner();await cancelled.prepare(fromHex(cases[0].header));
              const controller=new AbortController();const pending=cancelled.run(0n,1048576,{signal:controller.signal});controller.abort();
              await rejects(()=>pending,'in-flight abort ignored');await rejects(()=>cancelled.run(0n,1),'aborted session reused');cancelled.dispose();
              return {checked,devices};
            }''',cases)
            print(f"Validated {result['validation']['checked']} independent OpenSSL digests",flush=True)
            if not a.software:
                # Keep hard-target, full-range benchmark independent of preselected proof latency.
                hard=bytearray.fromhex(fixture['header']);hard[100:132]=(1).to_bytes(32,'big')
                infos=result['validation']['devices']
                if any(i['adapter'].get('isFallbackAdapter') or any(word in str(i['adapter']).lower() for word in ['swiftshader','llvmpipe','software']) for i in infos):
                    raise RuntimeError('Hardware run selected a software adapter; rerun explicitly with --software for correctness only')
                page.evaluate("""async hard=>{
                  const {createWebGPUMiner}=await import('./miner.mjs');
                  const header=Uint8Array.from(hard.match(/../g),x=>parseInt(x,16));
                  window.study={miners:{},start:new DataView(header.buffer).getBigUint64(260,false)};
                  for(const workgroup of [32,64,128,256])for(const pruned of [false,true]){
                    const key=(pruned?'final32':'pair32')+'/'+workgroup;
                    const miner=await createWebGPUMiner({workgroup,pruned});study.miners[key]=miner;
                    await miner.prepare(header);await miner.run(study.start,16777216);
                  }
                }""",hard.hex())
                run_browser="""async key=>{
                  const v=await study.miners[key].run(study.start,16777216);
                  if(v.winner!==null)throw Error('Unexpected hard-target winner');
                  return {attempts:v.attempts,wallSeconds:v.wallSeconds,gpuSeconds:v.gpuSeconds,explicitUploadBytes:v.explicitUploadBytes,explicitReadbackBytes:v.explicitReadbackBytes};
                }"""
                with tempfile.TemporaryDirectory(prefix='qcash-webgpu-') as tmp:
                    work=Path(tmp)/'hard.bin';work.write_bytes(hard+b'\0'*4)
                    def native():
                        return json.loads(subprocess.check_output([str(a.native.resolve()),'bench',str(work),'16777216','1','--shader','final32'],text=True,timeout=60))
                    keys=[f'{s}/{g}' for s in ['pair32','final32'] for g in [32,64,128,256]]
                    sweep={key:[] for key in keys};native_sweep=[]
                    for repeat in range(7):
                        order=keys+['native'];random.Random(711+repeat).shuffle(order)
                        for key in order:
                            if key=='native':native_sweep.append(native())
                            else:sweep[key].append(page.evaluate(run_browser,key))
                        print(f'WebGPU sweep {repeat+1}/7',flush=True)
                    selected=max(sweep,key=lambda key:statistics.median(16777216/r['wallSeconds'] for r in sweep[key]))
                    confirmation={'webgpu':[],'native':[]}
                    for repeat in range(11):
                        for key in (['webgpu','native'] if repeat%2==0 else ['native','webgpu']):
                            confirmation[key].append(page.evaluate(run_browser,selected) if key=='webgpu' else native())
                    metadata=page.evaluate("Object.fromEntries(Object.entries(study.miners).map(([k,m])=>[k,m.info]))")
                    proof=page.evaluate("""async ({fixture,selected})=>{
                      const miner=study.miners[selected],header=Uint8Array.from(fixture.header.match(/../g),x=>parseInt(x,16));
                      await miner.prepare(header);const start=new DataView(header.buffer).getBigUint64(260,false);const warm=[];
                      let proof;
                      for(let i=0;i<8;i++){const v=await miner.run(start,256);if(!v.proof?.verified)throw Error('Fixture proof missing');if(i)warm.push(v.wallSeconds);proof={...v.proof,header:Array.from(v.proof.header)};}
                      return {warmVerifiedProofSeconds:warm,proof};
                    }""",{'fixture':fixture,'selected':selected})
                    raw=bytes(proof['proof']['header']);assert hashlib.sha3_256(raw).hexdigest()==proof['proof']['blockId']
                    solved=Path(tmp)/'solved.bin';solved.write_bytes(raw+b'\0'*4)
                    inspect=json.loads(subprocess.check_output([str(ROOT/'build/qcash'),'inspect-work',str(solved)],text=True,timeout=30))
                    result['benchmark']={'selected':selected,'infos':metadata,'sweep':sweep,'native_sweep':native_sweep,'confirmation':confirmation,**proof,'native_inspection':inspect}
                page.evaluate('Object.values(study.miners).forEach(m=>m.dispose())')
                result['native_binary_sha256']=hashlib.sha256(a.native.read_bytes()).hexdigest()
            result['console']=logs
            after={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((ROOT/'miners/webgpu').glob('*')) if f.is_file()}
            if after!=result['source_hashes']:raise RuntimeError('Browser source changed during the run')
            browser.close()
        result['status']='passed'
    except Exception as error:
        result['status']='failed';result['error']=str(error)
        raise
    finally:
        a.output.write_text(json.dumps(result,indent=2)+'\n');server.shutdown()
    print(a.output)
if __name__=='__main__':main()
