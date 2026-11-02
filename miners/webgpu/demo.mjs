import {createWebGPUMiner} from './miner.mjs';
const $=id=>document.getElementById(id);
let work=null,miner=null,controller=null,solved=null;
$('work').addEventListener('change',async()=>{
 work=null;solved=null;$('scan').disabled=true;$('download').disabled=true;
 try{
  const file=$('work').files[0];if(!file)return;
  if(file.size!==272)throw Error('Choose a 272-byte work file with zero transactions.');
  const bytes=new Uint8Array(await file.arrayBuffer());
  if(bytes.subarray(268).some(x=>x!==0))throw Error('This browser tool supports empty-transaction work only.');
  work=bytes;$('nonce').value=new DataView(bytes.buffer).getBigUint64(260,false).toString();
  $('scan').disabled=false;$('status').textContent='Work loaded. No mining is running.';
 }catch(e){$('status').textContent=e.message;}
});
$('scan').addEventListener('click',async()=>{
 const snapshot=work?.slice();if(!snapshot)return;
 controller=new AbortController();solved=null;$('scan').disabled=true;$('work').disabled=true;$('cancel').disabled=false;$('download').disabled=true;
 try{
  const start=BigInt($('nonce').value),count=Number($('count').value);
  $('status').textContent='Preparing GPU session…';miner??=await createWebGPUMiner();
  await miner.prepare(snapshot.subarray(0,268),{signal:controller.signal});
  $('status').textContent='Scanning nonce range…';const r=await miner.run(start,count,{signal:controller.signal});
  const rate=(r.attempts/r.wallSeconds/1e6).toFixed(1);
  $('status').textContent=`${r.attempts.toLocaleString()} candidates · ${rate} million/s\n${r.proof?`Verified winner: nonce ${r.proof.nonce}\n${r.proof.blockId}`:'No winner in this range.'}`;
  if(r.proof){solved=snapshot;solved.set(r.proof.header);$('download').disabled=false;}
 }catch(e){$('status').textContent=e.message;miner?.dispose();miner=null;}
 finally{controller=null;$('cancel').disabled=true;$('scan').disabled=!work;$('work').disabled=false;}
});
$('cancel').addEventListener('click',()=>controller?.abort());
$('download').addEventListener('click',()=>{
 if(!solved)return;const url=URL.createObjectURL(new Blob([solved],{type:'application/octet-stream'}));
 const link=document.createElement('a');link.href=url;link.download='qcash-verified-work.bin';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
addEventListener('pagehide',()=>{controller?.abort();miner?.dispose();});
