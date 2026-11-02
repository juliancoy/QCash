import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
test('SIMD128 kernel matches OpenSSL, including odd pairs and nonce carry',async()=>{
 const binary=readFileSync(new URL('../miners/wasm/kernel.wasm',import.meta.url));
 const manifest=JSON.parse(readFileSync(new URL('../miners/wasm/build.json',import.meta.url)));
 assert.equal(createHash('sha256').update(readFileSync(new URL('../miners/wasm/kernel.cpp',import.meta.url))).digest('hex'),manifest.source_sha256);
 assert.equal(createHash('sha256').update(binary).digest('hex'),manifest.wasm_sha256);
 const module=await WebAssembly.compile(binary);assert.deepEqual(WebAssembly.Module.imports(module),[]);
 const {exports:e}=await WebAssembly.instantiate(module,{});e._initialize();
 const header=Uint8Array.from({length:268},(_,i)=>(i*71+19)%256);header.fill(255,100,132);
 new Uint8Array(e.memory.buffer,e.get_header(),268).set(header);e.prepare();
 for(const start of [0n,0xfffffffcn,(1n<<64n)-257n])for(const count of [1,2,65,257]){
  if(start+BigInt(count)-1n>=(1n<<64n))continue;
  const winner=e.scan(Number(start&0xffffffffn),Number(start>>32n),count,1)>>>0;assert.equal(winner,0);
  const output=new Uint8Array(e.memory.buffer,e.get_output(),count*32);
  for(let i=0;i<count;i++){
   new DataView(header.buffer).setBigUint64(260,start+BigInt(i),false);
   assert.equal(Buffer.from(output.subarray(i*32,i*32+32)).toString('hex'),createHash('sha3-256').update(header).digest('hex'));
  }
 }
});
