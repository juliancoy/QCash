import {shader} from './shader.mjs';
import {sha3,qualifies,hex} from './sha3.mjs';
const MAX_NONCE=(1n<<64n)-1n;
function integer(v,lo,hi,label){if(!Number.isInteger(v)||v<lo||v>hi)throw RangeError(label);}
export async function createWebGPUMiner({gpu=globalThis.navigator?.gpu,workgroup=64,maxBatch=16777216,dispatchSize=1048576,timeoutMs=10000,pruned=true}={}){
 integer(workgroup,32,256,'Invalid workgroup');if(![32,64,128,256].includes(workgroup))throw RangeError('Invalid workgroup');
 integer(maxBatch,1,16777216,'Invalid batch limit');integer(timeoutMs,100,60000,'Invalid timeout');
 if(!gpu)throw Error('WebGPU requires a supported browser and secure context');
 const adapter=await gpu.requestAdapter({powerPreference:'high-performance'});
 if(!adapter)throw Error('No WebGPU adapter available');
 if(adapter.limits.maxComputeInvocationsPerWorkgroup<workgroup||adapter.limits.maxComputeWorkgroupSizeX<workgroup)throw RangeError('Workload exceeds adapter limits');
 integer(dispatchSize,workgroup,Math.min(1048576,workgroup*adapter.limits.maxComputeWorkgroupsPerDimension),'Invalid dispatch size');
 if(dispatchSize%workgroup)throw RangeError('Dispatch size must be a multiple of workgroup');
 const maxDispatches=Math.ceil(maxBatch/dispatchSize);
 if(maxDispatches>64)throw RangeError('Too many grouped dispatches');
 const timestamps=adapter.features.has('timestamp-query');
 const device=await adapter.requestDevice({requiredFeatures:timestamps?['timestamp-query']:[]});
 const alignment=device.limits.minUniformBufferOffsetAlignment;
 const resources=[];let closed=false,failure=null,busy=false,prepared=null;
 const fail=e=>{failure??=e instanceof Error?e:Error(String(e));device.destroy();};
 device.lost.then(info=>{if(!closed)fail(Error(`WebGPU device lost: ${info.reason}: ${info.message}`));});
 device.addEventListener('uncapturederror',e=>fail(e.error));
 const active=()=>{if(failure)throw failure;if(closed)throw Error('Miner disposed');};
 const enter=()=>{active();if(busy)throw Error('Miner busy; await the current job');busy=true;};
 function bounded(promise,signal){
  let timer,abort;
  const deadline=new Promise((_,reject)=>{
   timer=setTimeout(()=>{const e=Error('GPU operation timed out');fail(e);reject(e);},timeoutMs);
   abort=()=>{const e=new DOMException('GPU operation aborted; session closed','AbortError');fail(e);reject(e);};
   if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  });
  return Promise.race([promise,deadline]).finally(()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);});
 }
 const make=(size,usage,label)=>{const b=device.createBuffer({size,usage,label});resources.push(b);return b;};
 const U=GPUBufferUsage,MAX_CAPTURE=4096,RESULT=16+MAX_CAPTURE*32;
 try{
  device.pushErrorScope('out-of-memory');device.pushErrorScope('validation');
  const layout=device.createBindGroupLayout({entries:[
   {binding:0,visibility:GPUShaderStage.COMPUTE,buffer:{type:'read-only-storage'}},
   {binding:1,visibility:GPUShaderStage.COMPUTE,buffer:{type:'storage'}},
   {binding:2,visibility:GPUShaderStage.COMPUTE,buffer:{type:'uniform',hasDynamicOffset:true}},
   {binding:3,visibility:GPUShaderStage.COMPUTE,buffer:{type:'storage'}}]});
  const pipelineLayout=device.createPipelineLayout({bindGroupLayouts:[layout]});
  const module=device.createShaderModule({code:shader({workgroup,pruned}),label:'QCash paired-word SHA3'});
  const messages=(await module.getCompilationInfo()).messages.filter(m=>m.type==='error');
  if(messages.length)throw Error(messages.map(m=>`${m.lineNum}: ${m.message}`).join('\n'));
  const pipeline=async(entryPoint,capture=false)=>bounded(device.createComputePipelineAsync({layout:pipelineLayout,compute:{module,entryPoint,constants:entryPoint==='mine'?{CAPTURE:capture}:{}},label:`QCash ${entryPoint} ${capture?'capture':'normal'}`}));
  const preparePipeline=await pipeline('prepare'),resetPipeline=await pipeline('reset'),minePipeline=await pipeline('mine'),capturePipeline=await pipeline('mine',true);
  const header=make(272,U.STORAGE|U.COPY_DST,'Immutable job header');
  const midstate=make(200,U.STORAGE,'GPU-resident prefix midstate');
  const params=make(alignment*maxDispatches,U.UNIFORM|U.COPY_DST,'Nonce range');
  const result=make(RESULT,U.STORAGE|U.COPY_SRC,'Winner and optional debug digests');
  const readback=make(RESULT+16,U.COPY_DST|U.MAP_READ,'Bounded readback');
  const group=device.createBindGroup({layout,entries:[header,midstate,params,result].map((buffer,binding)=>({binding,resource:{buffer,...(binding===2?{size:16}:{})}}))});
  const queries=timestamps?device.createQuerySet({type:'timestamp',count:2}):null;
  if(queries)resources.push(queries);
  const queryBuffer=timestamps?make(16,U.QUERY_RESOLVE|U.COPY_SRC,'GPU timestamps'):null;
  const validation=await device.popErrorScope(),oom=await device.popErrorScope();
  if(validation||oom)throw validation??oom;
  function compute(encoder,pipeline,count,timed=false,dispatches=1){
   const pass=encoder.beginComputePass(timed&&queries?{timestampWrites:{querySet:queries,beginningOfPassWriteIndex:0,endOfPassWriteIndex:1}}:{});
   pass.setPipeline(pipeline);
   for(let i=0;i<dispatches;i++){pass.setBindGroup(0,group,[i*alignment]);pass.dispatchWorkgroups(timed?Math.ceil(Math.min(count-i*dispatchSize,dispatchSize)/workgroup):count);}
   pass.end();
  }
  return {
   info:{adapter:{vendor:adapter.info?.vendor??'',architecture:adapter.info?.architecture??'',device:adapter.info?.device??'',description:adapter.info?.description??'',isFallbackAdapter:adapter.info?.isFallbackAdapter??null},workgroup,maxBatch,dispatchSize,timestamps,variant:pruned?'final32':'pair32',readback:'4-byte winner; debug digests only on request',cpuVerification:'independent BigInt SHA3 of winners only'},
   async prepare(bytes,{signal}={}){
    enter();try{
     if(!(bytes instanceof Uint8Array)||bytes.length!==268)throw RangeError('Expected a 268-byte QCash work header');
     signal?.throwIfAborted();const copy=bytes.slice(),upload=new Uint8Array(272);upload.set(copy);
     prepared=null;device.queue.writeBuffer(header,0,upload);
     const encoder=device.createCommandEncoder();compute(encoder,preparePipeline,1);device.queue.submit([encoder.finish()]);
     await bounded(device.queue.onSubmittedWorkDone(),signal);active();prepared=copy;
    }finally{busy=false;}
   },
   async run(start,count,{capture=false,signal}={}){
    enter();try{
     if(!prepared)throw Error('Prepare a job first');
     if(typeof start!=='bigint'||start<0n||start>MAX_NONCE)throw RangeError('Invalid nonce');
     integer(count,1,maxBatch,'Invalid candidate count');if(start+BigInt(count)-1n>MAX_NONCE)throw RangeError('Nonce overflow');
     if(capture&&count>MAX_CAPTURE)throw RangeError('Debug capture exceeds 4096 candidates');
     signal?.throwIfAborted();const started=performance.now();
     const dispatches=Math.ceil(count/dispatchSize);
     for(let i=0;i<dispatches;i++)device.queue.writeBuffer(params,i*alignment,new Uint32Array([Number(start&0xffffffffn),Number(start>>32n),count,i*dispatchSize]));
     const encoder=device.createCommandEncoder();compute(encoder,resetPipeline,1);
     compute(encoder,capture?capturePipeline:minePipeline,count,true,dispatches);
     const bytes=capture?16+count*32:4,queryOffset=Math.ceil(bytes/8)*8;
     encoder.copyBufferToBuffer(result,0,readback,0,bytes);
     if(queries){encoder.resolveQuerySet(queries,0,2,queryBuffer,0);encoder.copyBufferToBuffer(queryBuffer,0,readback,queryOffset,16);}
     device.queue.submit([encoder.finish()]);
     const mappedBytes=queries?queryOffset+16:bytes;
     await bounded(readback.mapAsync(GPUMapMode.READ,0,mappedBytes),signal);active();
     let winner,gpuSeconds=null,digests=null;
     try{
      const mapped=readback.getMappedRange(0,mappedBytes);winner=new DataView(mapped).getUint32(0,true);
      if(queries){const ticks=new BigUint64Array(mapped,queryOffset,2);if(ticks[1]>ticks[0])gpuSeconds=Number(ticks[1]-ticks[0])*1e-9;}
      if(capture){digests=new Uint8Array(count*32);const view=new DataView(mapped),output=new DataView(digests.buffer);for(let i=0;i<count*8;i++)output.setUint32(i*4,view.getUint32(16+i*4,true),false);}
     }finally{readback.unmap();}
     let proof=null;
     if(winner!==0xffffffff){
      if(winner>=count){fail(Error('GPU returned out-of-range winner'));throw failure;}
      const candidate=prepared.slice(),nonce=start+BigInt(winner);new DataView(candidate.buffer).setBigUint64(260,nonce,false);
      const digest=sha3(candidate);
      if(!qualifies(digest,candidate.subarray(100,132))){fail(Error('Independent CPU proof verification failed'));throw failure;}
      proof={nonce:nonce.toString(),blockId:hex(digest),header:candidate,verified:true};
     }
     return {attempts:count,winner:winner===0xffffffff?null:winner,proof,digests,gpuSeconds,wallSeconds:(performance.now()-started)/1000,explicitUploadBytes:16*dispatches,explicitReadbackBytes:bytes+(queries?16:0)};
    }finally{busy=false;}
   },
   dispose(){if(closed)return;closed=true;for(const r of resources)r.destroy();device.destroy();prepared=null;},
  };
 }catch(error){closed=true;for(const r of resources)r.destroy();device.destroy();throw error;}
}
