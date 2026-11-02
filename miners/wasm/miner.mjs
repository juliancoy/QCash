import {sha3,qualifies,hex} from '../webgpu/sha3.mjs';
const modules=new Map();
function integer(n,min,max,label){if(!Number.isInteger(n)||n<min||n>max)throw RangeError(label);}
async function compiled(url){
 const key=String(url);
 if(!modules.has(key)){
  const pending=(async()=>{const r=await fetch(url);if(!r.ok)throw Error(`WASM load failed: ${r.status}`);return WebAssembly.compile(await r.arrayBuffer());})();
  modules.set(key,pending);pending.catch(()=>modules.delete(key));
 }
 return modules.get(key);
}
export async function createWasmMiner({threads=1,timeoutMs=30000,kernelUrl=new URL('./kernel.wasm',import.meta.url)}={}){
 integer(threads,1,8,'Workers must be 1..8');integer(timeoutMs,100,60000,'Invalid timeout');
 const module=await compiled(kernelUrl),workers=[],pending=new Map();
 let failure=null,closed=false,busy=false,prepared=null,nextId=1;
 function fail(error){
  failure??=error instanceof Error?error:Error(String(error));
  for(const worker of workers)worker.terminate();
  for(const item of pending.values())item.reject(failure);pending.clear();
 }
 const active=()=>{if(failure)throw failure;if(closed)throw Error('Miner disposed');};
 const enter=()=>{active();if(busy)throw Error('Miner busy; await current operation');busy=true;};
 function call(worker,data){
  active();const id=nextId++;
  return new Promise((resolve,reject)=>{pending.set(id,{worker,resolve,reject});worker.postMessage({...data,id});});
 }
 function bounded(promise,signal){
  let timer,abort;
  const deadline=new Promise((_,reject)=>{
   timer=setTimeout(()=>{const error=Error('WASM worker timed out');fail(error);reject(error);},timeoutMs);
   abort=()=>{const error=new DOMException('WASM operation aborted; session closed','AbortError');fail(error);reject(error);};
   if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  });
  return Promise.race([promise,deadline]).finally(()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);});
 }
 try{
  for(let i=0;i<threads;i++){
   const worker=new Worker(new URL('./worker.mjs',import.meta.url),{type:'module'});workers.push(worker);
   worker.onerror=e=>{e.preventDefault();fail(Error(e.message||'WASM worker error'));};
   worker.onmessageerror=()=>fail(Error('Invalid WASM worker message'));
   worker.onmessage=({data})=>{
    const item=pending.get(data.id);
    if(!item||item.worker!==worker){fail(Error('Unexpected WASM reply identity'));return;}
    pending.delete(data.id);
    if(data.kind==='error'){item.reject(Error(data.error));fail(Error(data.error));}
    else if(data.kind!=='reply'){item.reject(Error('Invalid worker reply'));fail(Error('Invalid worker reply'));}
    else item.resolve(data);
   };
  }
  const ready=await bounded(Promise.all(workers.map(worker=>call(worker,{kind:'init',module}))));
  if(ready.some(r=>!r.ready||r.shared))throw Error('Expected initialized private-memory workers');
  return {
   info:{backend:'wasm-simd128',threads,simdLanes:2,workerMemory:'private, nonshared',sharedMemoryRequired:false,maxBatch:16777216,cpuVerification:'independent BigInt SHA3 winners only'},
   async prepare(header,{signal}={}){
    enter();try{
     if(!(header instanceof Uint8Array)||header.length!==268)throw RangeError('Expected 268-byte QCash header');
     signal?.throwIfAborted();const copy=header.slice();prepared=null;
     await bounded(Promise.all(workers.map(worker=>call(worker,{kind:'prepare',header:copy}))),signal);active();prepared=copy;
    }finally{busy=false;}
   },
   async run(start,count,{capture=false,signal}={}){
    enter();try{
     if(!prepared)throw Error('Prepare a job first');
     if(typeof start!=='bigint'||start<0n||start>(1n<<64n)-1n)throw RangeError('Invalid nonce');
     integer(count,1,16777216,'Invalid count');if(start+BigInt(count)-1n>(1n<<64n)-1n)throw RangeError('Nonce overflow');
     if(capture&&count>4096)throw RangeError('Capture exceeds 4096 candidates');signal?.throwIfAborted();
     const began=performance.now(),jobs=[];
     for(let i=0;i<threads;i++){
      const base=Math.floor(count*i/threads),end=Math.floor(count*(i+1)/threads);if(base===end)continue;
      const nonce=start+BigInt(base);
      jobs.push({base,count:end-base,promise:call(workers[i],{kind:'scan',low:Number(nonce&0xffffffffn),high:Number(nonce>>32n),count:end-base,capture})});
     }
     const responses=await bounded(Promise.all(jobs.map(j=>j.promise)),signal);active();
     let winner=null;const digests=capture?new Uint8Array(count*32):null;
     for(let i=0;i<responses.length;i++){
      const r=responses[i],job=jobs[i];
      if(r.count!==job.count||!Number.isFinite(r.kernelSeconds)||r.kernelSeconds<0||r.winner!==null&&(!Number.isInteger(r.winner)||r.winner<0||r.winner>=job.count)||capture&&(!(r.digests instanceof Uint8Array)||r.digests.length!==job.count*32)){fail(Error('Invalid WASM range result'));throw failure;}
      if(r.winner!==null){const index=job.base+r.winner;winner=winner===null?index:Math.min(winner,index);}
      if(capture)digests.set(r.digests,job.base*32);
     }
     let proof=null;
     if(winner!==null){
      const header=prepared.slice(),nonce=start+BigInt(winner);new DataView(header.buffer).setBigUint64(260,nonce,false);
      const digest=sha3(header);
      if(!qualifies(digest,header.subarray(100,132))){fail(Error('Independent WASM proof verification failed'));throw failure;}
      proof={nonce:nonce.toString(),header,blockId:hex(digest),verified:true};
     }
     return {attempts:count,winner,proof,digests,wallSeconds:(performance.now()-began)/1000,workerKernelSeconds:responses.map(r=>r.kernelSeconds),activeWorkers:responses.length};
    }finally{busy=false;}
   },
   dispose(){if(closed)return;closed=true;for(const worker of workers)worker.terminate();for(const item of pending.values())item.reject(Error('Miner disposed'));pending.clear();prepared=null;},
  };
 }catch(error){fail(error);throw error;}
}
