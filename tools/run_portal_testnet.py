#!/usr/bin/env python3
"""Run a local development miner and wallet gateway, with no wallet-key custody."""
import argparse,json,os,re,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--mine-to',required=True,help='Public address from your browser test wallet')
p.add_argument('--home',type=Path,default=ROOT/'.deps/portal-testnet')
p.add_argument('--host',default='127.0.0.1',help='Gateway bind address; loopback or private Docker bridge only')
p.add_argument('--peer-port',type=int,default=29700);p.add_argument('--gateway-port',type=int,default=29702)
a=p.parse_args()
if not re.fullmatch('[0-9a-f]{64}',a.mine_to):p.error('expected a 64-character public wallet address')
if any(not 1024<=port<=65535 for port in [a.peer_port,a.gateway_port]) or a.peer_port==a.gateway_port:p.error('distinct nonprivileged ports required')
binary=ROOT/'build/qcash';home=a.home.resolve();home.mkdir(parents=True,exist_ok=True,mode=0o700)
def run(*args):return subprocess.check_output([str(binary),*map(str,args)],text=True).strip()
for name in ['node','client']:
 if not (home/name).exists():run('init',home/name,ROOT/'artifacts/devnet-genesis/genesis.bin')
status=json.loads(run('status',home/'node'));client=json.loads(run('status',home/'client'))
if status['chain']!=client['chain']:raise RuntimeError('node and client configurations differ')
peer='127.0.0.1:'+str(a.peer_port)
run('trust',home/'client',peer,run('identity',home/'node'))
print('Local testnet; no wallet private keys are held by these services.',flush=True)
print('QCASH_CHAIN_ID='+status['chain'],flush=True)
print(f'QCASH_GATEWAY_URL=http://{a.host}:{a.gateway_port}',flush=True)
print('For the local Worker use ENV=development. Production requires HTTPS and a separate release.',flush=True)
children=[]
def stop(*_):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
try:
 children.append(subprocess.Popen([str(binary),'node',str(home/'node'),peer,'--mine-to',a.mine_to]))
 children.append(subprocess.Popen([sys.executable,str(ROOT/'tools/wallet_gateway.py'),'--binary',str(binary),'--home',str(home/'client'),'--peer',peer,'--host',a.host,'--port',str(a.gateway_port)]))
 while all(child.poll() is None for child in children):time.sleep(.25)
 if any(child.poll() not in [None,0] for child in children):raise RuntimeError('testnet service exited; inspect its error above')
except KeyboardInterrupt:pass
finally:
 for child in reversed(children):
  if child.poll() is None:child.terminate()
 for child in children:
  try:child.wait(timeout=12)
  except subprocess.TimeoutExpired:child.kill();child.wait()
