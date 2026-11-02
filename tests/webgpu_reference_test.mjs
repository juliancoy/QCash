import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {sha3,qualifies,hex} from '../miners/webgpu/sha3.mjs';
import {shader} from '../miners/webgpu/shader.mjs';
test('Independent host SHA3 matches OpenSSL across padding boundaries',()=>{
 for(const n of [0,1,3,135,136,137,267,268,272,4096]){
  const bytes=randomBytes(n);assert.equal(hex(sha3(bytes)),createHash('sha3-256').update(bytes).digest('hex'));
 }
});
test('Unsigned big-endian target comparison includes equality',()=>{
 const a=new Uint8Array(32).fill(128),b=a.slice();assert.ok(qualifies(a,b));b[31]=127;assert.equal(qualifies(a,b),false);b[0]=129;assert.ok(qualifies(a,b));
});
test('Generator supports only portable bounded workgroups',()=>{
 for(const workgroup of [32,64,128,256])for(const pruned of [true,false])assert.match(shader({workgroup,pruned}),/@compute/);
 assert.throws(()=>shader({workgroup:33}),RangeError);
});
