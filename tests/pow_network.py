#!/usr/bin/env python3
"""Native PoW: independent peers, signed spending, TLS, reorg and disk recovery."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('PQCOIN_POW_BINARY',str(ROOT/'build/pqcoin-pow'))).resolve()
work=Path(tempfile.mkdtemp(prefix='pow-network-',dir=ROOT/'.deps'))
processes={}
logs={}
report={'binary':str(BIN),'work_directory':str(work),'status':'running','permissionless':True,'test_retarget_interval':0}
started=time.monotonic()

def run(*args,check=True):
    result=subprocess.run([str(BIN),*map(str,args)],capture_output=True,text=True,timeout=45)
    if check and result.returncode:raise RuntimeError(f'{args}: {result.stderr}')
    return result

def status(index,remote=False):
    result=run('query' if remote else 'status',work/f'node{index}',*([endpoint(index)] if remote else []))
    return json.loads(result.stdout)

def wait(description,operation,timeout=35):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        for i,p in processes.items():
            if p.poll() is not None:raise RuntimeError(f'node{i} exited: '+(work/f'node{i}.log').read_text()[-3000:])
        try:
            value=operation()
            if value:print(description,flush=True);return value
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as e:last=e
        time.sleep(.15)
    raise RuntimeError(f'{description}: timeout; {last}')

base=None
for candidate in range(31000,59000,11):
    reserved=[]
    try:
        for port in range(candidate,candidate+4):
            s=socket.socket();reserved.append(s);s.bind(('127.0.0.1',port))
        base=candidate;break
    except OSError:pass
    finally:
        for s in reserved:s.close()
assert base

def endpoint(i):return f'127.0.0.1:{base+i}'

def start(i,peers):
    logs[i]=open(work/f'node{i}.log','ab',buffering=0)
    args=[str(BIN),'node',str(work/f'node{i}'),endpoint(i)]
    for j in peers:args+=['--peer',endpoint(j)]
    processes[i]=subprocess.Popen(args,stdout=logs[i],stderr=subprocess.STDOUT)

def stop(i,crash=False):
    p=processes.pop(i)
    p.kill() if crash else p.terminate()
    try:p.wait(timeout=12)
    except subprocess.TimeoutExpired:p.kill();p.wait()
    logs.pop(i).close()

try:
    for i in range(4):
        run('init',work/f'node{i}',ROOT/'artifacts/devnet-genesis/genesis.bin')
    alice=run('wallet',work/'alice.pem').stdout.strip()
    bob=run('wallet',work/'bob.pem').stdout.strip()
    assert (work/'alice.pem').stat().st_mode&0o777==0o600
    run('mine',work/'node0',alice,6)
    run('sign',work/'node0',work/'alice.pem',bob,25,1,0,work/'tx.bin')
    run('mine',work/'node0',alice,1,work/'tx.bin')
    balance=json.loads(run('balance',work/'node0',alice).stdout)
    assert balance=={'spendable':224,'immature':101,'nonce':1},balance
    assert json.loads(run('balance',work/'node0',bob).stdout)['spendable']==25
    replay=run('mine',work/'node0',alice,1,work/'tx.bin',check=False)
    assert replay.returncode!=0
    report['signed_transfer_and_replay']={'alice':balance,'replay_rejected':True}
    # Two independent miners create different histories without any validator keys.
    run('mine',work/'node1',bob,2)
    expected=status(0)
    start(0,[1]);start(1,[2]);start(2,[0])
    def agreed():
        states=[status(i,True) for i in range(3)]
        return states[0] if all(s['tip']==expected['tip'] and s['state']==expected['state'] for s in states) else False
    report['initial_agreement']=wait('Three peers agreed over hybrid TLS; losing branch rolled back',agreed)
    assert status(1,True)['height']==7
    # The peer protocol does not silently fall back to a classical TLS group.
    result=subprocess.run([str(ROOT/'.deps/openssl/bin/openssl'),'s_client','-connect',endpoint(0),'-tls1_3','-groups','X25519'],input='',capture_output=True,text=True,timeout=8)
    assert result.returncode!=0,result.stdout
    report['classical_only_tls_rejected']=True
    # A changed known-peer key must be rejected, not silently trusted again.
    pin=work/'node3/peer-pins'/hashlib.sha3_256(endpoint(0).encode()).hexdigest()
    run('trust',work/'node3',endpoint(0),'00'*32)
    mismatch=run('query',work/'node3',endpoint(0),check=False)
    assert mismatch.returncode!=0 and 'pin mismatch' in mismatch.stderr,mismatch.stderr
    report['changed_peer_identity_rejected']=True
    # A complete forged block and a future timestamp are rejected by core tests;
    # here send a malformed block over the actual authenticated transport.
    (work/'bad-block.bin').write_bytes(b'invalid')
    assert run('submit',work/'node2',endpoint(0),work/'bad-block.bin',check=False).returncode!=0
    report['malformed_peer_block_rejected']=True
    for i in list(processes):stop(i,crash=True)
    run('mine',work/'node0',alice,13)
    expected=status(0)
    # Crash-tail repair retains the last complete checksummed block.
    before=(work/'node1/blocks.log').read_bytes()
    with (work/'node1/blocks.log').open('ab') as out:out.write(b'\0\x01')
    assert status(1)['height']==7
    assert (work/'node1/blocks.log').read_bytes()==before
    report['incomplete_journal_tail_recovered']=True
    # Independent fork longer than one sync page. Offline history remains valid.
    run('mine',work/'node3',bob,25)
    expected=status(3)
    # Remove only the deliberately bad public test pin before using that test node.
    pin.unlink()
    start(3,[]);start(0,[3]);start(1,[0]);start(2,[1])
    def recovered():
        states=[status(i,True) for i in range(4)]
        return states[0] if all(s['tip']==expected['tip'] and s['state']==expected['state'] for s in states) else False
    report['paged_fork_recovery']=wait('Four peers adopted the higher-work branch across sync pages after restart',recovered)
    for i in list(processes):stop(i)
    assert json.loads(run('balance',work/'node0',alice).stdout)=={'spendable':0,'immature':0,'nonce':0}
    assert status(0)['tip']==expected['tip']
    report['reorganization_rolled_back_spend_and_rewards']=True
    # Complete-record corruption must fail closed, not be mistaken for a crash tail.
    data=bytearray((work/'node2/blocks.log').read_bytes());data[10]^=1
    (work/'node2/blocks.log').write_bytes(data)
    bad=run('status',work/'node2',check=False)
    assert bad.returncode!=0 and 'checksum' in bad.stderr
    report['journal_corruption_rejected']=True
    report['status']='passed'
except BaseException as error:
    report['status']='failed';report['error']=str(error);raise
finally:
    for i in list(processes):stop(i)
    report['duration_seconds']=round(time.monotonic()-started,3)
    output=Path(os.environ.get('PQCOIN_POW_REPORT',str(ROOT/'artifacts/pow/network-report.json')));output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(f'Report: {output}',flush=True)
