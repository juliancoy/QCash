let exports=null,prepared=false;
self.onmessage=async({data})=>{
 const {id,kind}=data;
 try{
  if(!Number.isSafeInteger(id)||id<1)throw Error('Invalid request identity');
  if(kind==='init'){
   if(exports)throw Error('Worker already initialized');
   const instance=await WebAssembly.instantiate(data.module,{});exports=instance.exports;exports._initialize?.();
   if(!(exports.memory instanceof WebAssembly.Memory))throw Error('Missing WASM memory');
   postMessage({id,kind:'reply',ready:true,shared:typeof SharedArrayBuffer!=='undefined'&&exports.memory.buffer instanceof SharedArrayBuffer});return;
  }
  if(!exports)throw Error('Worker not initialized');
  if(kind==='prepare'){
   if(!(data.header instanceof Uint8Array)||data.header.length!==268)throw Error('Invalid header');
   new Uint8Array(exports.memory.buffer,exports.get_header(),268).set(data.header);exports.prepare();prepared=true;
   postMessage({id,kind:'reply',prepared:true});return;
  }
  if(kind!=='scan'||!prepared)throw Error('Invalid worker operation');
  const {low,high,count,capture}=data;
  if(![low,high].every(n=>Number.isInteger(n)&&n>=0&&n<=0xffffffff)||!Number.isInteger(count)||count<1||count>16777216||(capture&&count>4096))throw Error('Invalid worker range');
  const began=performance.now(),winner=exports.scan(low,high,count,capture?1:0)>>>0;
  const kernelSeconds=(performance.now()-began)/1000;
  const digests=capture?new Uint8Array(exports.memory.buffer,exports.get_output(),count*32).slice():null;
  postMessage({id,kind:'reply',count,winner:winner===0xffffffff?null:winner,kernelSeconds,digests},digests?[digests.buffer]:[]);
 }catch(error){postMessage({id,kind:'error',error:error.message});}
};
