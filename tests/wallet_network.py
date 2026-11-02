#!/usr/bin/env python3
"""Browser-compatible signatures, native queue/gossip/mining/reorg and portal UX.
All coins/keys/data are synthetic and private to a fresh local testnet.
"""
import argparse,hashlib,json,os,socket,subprocess,tempfile,time,signal
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];ORG=ROOT.parent/'OrgPortal'
BIN=Path(os.environ.get('PQCOIN_POW_BINARY',str(ROOT/'build/pqcoin-pow'))).resolve()
parser=argparse.ArgumentParser();parser.add_argument('--browser',action='store_true');args=parser.parse_args()
work=Path(tempfile.mkdtemp(prefix='wallet-network-',dir=ROOT/'.deps'));processes=[];logs=[]
def interrupted(*_):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,interrupted)
report={'status':'running','binary':str(BIN),'work_directory':str(work),'browser':args.browser}
def run(*parts,check=True):
 result=subprocess.run([str(BIN),*map(str,parts)],capture_output=True,text=True,timeout=40)
 if check and result.returncode:raise RuntimeError(result.stderr)
 return result

def freeport():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
ports=[freeport() for _ in range(5)]
def endpoint(i):return '127.0.0.1:'+str(ports[i])
def spawn(command,name,env=None):
 log=open(work/(name+'.log'),'wb');logs.append(log)
 p=subprocess.Popen(list(map(str,command)),stdout=log,stderr=subprocess.STDOUT,env=env);processes.append(p);return p

def stop(p):
 p.terminate()
 try:p.wait(timeout=10)
 except subprocess.TimeoutExpired:p.kill();p.wait()
 processes.remove(p)

def wait(operation,timeout=30):
 deadline=time.monotonic()+timeout;last=None
 while time.monotonic()<deadline:
  try:
   value=operation()
   if value:return value
  except Exception as e:last=e
  time.sleep(.1)
 raise AssertionError(f'wait failed: {last}')
def remote(command,value=None):
 return json.loads(run(command,work/'client',endpoint(0),*([value] if value else [])).stdout)
gateway_token=__import__('secrets').token_urlsafe(48)
(work/'gateway-token').write_text(gateway_token);(work/'gateway-token').chmod(0o600)
gateway='http://127.0.0.1:'+str(ports[3]);adapter='http://127.0.0.1:'+str(ports[4])
def http(path,payload=None,base=gateway):
 data=json.dumps(payload).encode() if payload is not None else None
 req=Request(base+path,data=data,headers={**({'Content-Type':'application/json'} if data else {}),**({'Authorization':'Bearer '+gateway_token} if base==gateway else {})})
 try:
  with urlopen(req,timeout=20) as response:return response.status,json.load(response)
 except HTTPError as e:return e.code,json.load(e)
try:
 for name in ['node0','node1','node2','client']:
  run('init',work/name,ROOT/'artifacts/devnet-genesis/genesis.bin')
 chain=json.loads(run('status',work/'node0').stdout)['chain'];report['chain_id']=chain
 assert run('node',work/'client',endpoint(4),'--mine-interval','0',check=False).returncode!=0
 loader=ORG/'org-worker/node_modules/tsx/dist/loader.mjs'
 subprocess.run(['node','--import',str(loader),str(ORG/'web/scripts/pqcoin-fixture.ts'),chain,str(work)],check=True,capture_output=True,text=True)
 fixture=json.loads((work/'fixture.json').read_text());alice=fixture['alice'];bob=fixture['bob'];txid=fixture['transaction']
 run('mine',work/'node0',alice,6)
 # Pin the actual generated node identity before the first network request.
 for home,peer_index in [('client',0),('node0',1),('node1',0),('node2',0)]:
  fingerprint=run('identity',work/f'node{peer_index}').stdout.strip();run('trust',work/home,endpoint(peer_index),fingerprint)
 node0=spawn([BIN,'node',work/'node0',endpoint(0),'--peer',endpoint(1)],'node0')
 wait(lambda:remote('query')['height']==6)
 spawn(['python3',ROOT/'tools/wallet_gateway.py','--binary',BIN,'--home',work/'client','--peer',endpoint(0),'--port',ports[3],'--token-file',work/'gateway-token'],'gateway')
 wait(lambda:http('/network')[0]==200)
 try:urlopen(gateway+'/network',timeout=5);raise AssertionError('unauthenticated gateway request accepted')
 except HTTPError as error:assert error.code==401
 report['gateway_authentication']='passed'
 raw=(work/'transfer.bin').read_bytes();payload={'signedTransfer':raw.hex()}
 assert http('/transactions',payload)[0]==202
 assert http('/transactions',payload)[1]['id']==txid # Idempotent exact retransmission.
 assert http('/transactions',{'signedTransfer':(raw[:-1]+bytes([raw[-1]^1])).hex()})[0]==400
 assert http('/transactions',{'signedTransfer':(work/'overspend.bin').read_bytes().hex()})[0]==400
 assert http('/transactions/'+txid)[1]['status']=='pending'
 assert http('/accounts/'+alice)[1]['nonce']=='0'
 print('Browser-library signatures accepted by native OpenSSL; duplicate, signature and funds checks passed',flush=True)
 miner='44'*32
 node1=spawn([BIN,'node',work/'node1',endpoint(1),'--peer',endpoint(0),'--mine-to',miner,'--mine-interval','1'],'node1')
 wait(lambda:http('/transactions/'+txid)[1]['status']=='confirmed')
 wait(lambda:remote('query')['height']>=8) # Mature the original sender's final rewards.
 stop(node1)
 wait(lambda:int(http('/accounts/'+alice)[1]['spendable'])==274)
 assert http('/accounts/'+bob)[1]['spendable']=='25'
 report['native_signed_queue_gossip_and_confirmation']='passed'
 report['confirmed_transaction']=txid
 if args.browser:
  env={**os.environ,'PQCOIN_GATEWAY_URL':gateway,'PQCOIN_CHAIN_ID':chain,'PQCOIN_ADAPTER_PORT':str(ports[4]),'QCASH_GATEWAY_TOKEN':gateway_token}
  spawn(['node','--import',loader,ORG/'org-worker/scripts/pqcoin-local-adapter.ts'],'adapter',env)
  wait(lambda:http('/api/pqcoin/network',base=adapter)[0]==200)
  from playwright.sync_api import sync_playwright,expect
  from urllib.parse import urlparse
  base=os.environ.get('PLAYWRIGHT_BASE_URL','https://localhost:8443').rstrip('/')
  assert urlparse(base).hostname in {'localhost','127.0.0.1'}
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
   context=browser.new_context(ignore_https_errors=True,accept_downloads=True,viewport={'width':1280,'height':1000})
   context.route('**/*',lambda route:route.continue_() if urlparse(route.request.url).hostname in {'localhost','127.0.0.1'} else route.abort())
   # Use the real adapter + real node; override only the local routing destination.
   def route_api(route):
    path=urlparse(route.request.url).path.removeprefix('/api/org')
    response=context.request.fetch(adapter+path,method=route.request.method,data=route.request.post_data,headers={'Content-Type':'application/json'})
    route.fulfill(response=response)
   context.route('**/api/org/api/qcash/**',route_api)
   page=context.new_page();page.goto(base+'/finance',wait_until='domcontentloaded')
   card=page.get_by_role('region',name='QCash Testnet currency',exact=True)
   expect(card).to_be_visible(timeout=30000)
   card.get_by_role('link',name='Receive QCash',exact=True).click()
   expect(page.get_by_role('heading',name='Receive QCash Testnet',exact=True)).to_be_visible(timeout=30000)
   page.get_by_label('Backup password',exact=True).fill(fixture['password'])
   with page.expect_download() as download:page.get_by_role('button',name='Create wallet and download backup',exact=True).click()
   saved=work/'ui-created-backup.json';download.value.save_as(saved)
   assert json.loads(saved.read_text())['format']=='pqcoin-testnet-wallet-v1'
   expect(page.get_by_test_id('pqt-address')).to_be_visible()
   page.get_by_role('button',name='Lock wallet',exact=True).click()
   page.get_by_label('Backup password',exact=True).fill(fixture['password'])
   page.get_by_label('Unlock encrypted wallet backup',exact=True).set_input_files(work/'alice-backup.json')
   expect(page.get_by_test_id('pqt-address')).to_have_text(alice)
   expect(page.get_by_label('Payment request link',exact=True)).to_have_value(__import__('re').compile('.*currency=QCash.*chain='+chain+'.*to='+alice))
   expect(page.get_by_label('QCash payment QR',exact=True)).to_be_visible()
   page.get_by_role('link',name='Send QCash',exact=True).click()
   expect(page.get_by_test_id('pqt-spendable')).to_have_text('274 QCash',timeout=30000)
   page.get_by_label('Recipient QCash wallet address',exact=True).fill(bob)
   page.get_by_label('Amount (QCash)',exact=True).fill('5')
   page.get_by_role('button',name='Review payment',exact=True).click()
   region=page.get_by_role('region',name='Review QCash payment',exact=True)
   expect(region).to_contain_text('Total: 5 QCash')
   region.get_by_role('button',name='Authorize and send QCash',exact=True).click()
   expect(page.get_by_role('status').filter(has_text='Transfer submitted')).to_contain_text('Transfer submitted',timeout=30000)
   wait(lambda:any(t['status']=='pending' and t['amount']=='5' for t in http('/accounts/'+alice)[1]['transactions']))
   node1=spawn([BIN,'node',work/'node1',endpoint(1),'--peer',endpoint(0),'--mine-to',miner],'node1-browser')
   wait(lambda:http('/accounts/'+bob)[1]['spendable']=='30');stop(node1)
   expect(page.get_by_test_id('pqt-spendable')).to_have_text('269 QCash',timeout=30000)
   for width in [1280,390]:
    page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(work/f'portal-wallet-{width}.png'),full_page=True)
   page.goto(base+'/send?currency=QCash&chain='+'ff'*32+'&to='+bob,wait_until='domcontentloaded')
   expect(page.get_by_role('alert').filter(has_text='different network')).to_be_visible(timeout=30000)
   page.get_by_label('Backup password',exact=True).fill(fixture['password']);page.get_by_label('Unlock encrypted wallet backup',exact=True).set_input_files(work/'alice-backup.json')
   expect(page.get_by_role('button',name='Review payment',exact=True)).to_be_disabled()
   page.get_by_label('Currency',exact=True).select_option('DEM')
   expect(page.get_by_role('heading',name='Send Dena',exact=True)).to_be_visible()
   page.goto(base+'/send?currency=UNKNOWN',wait_until='domcontentloaded')
   expect(page.get_by_role('alert').filter(has_text='Unsupported payment currency')).to_be_visible(timeout=30000)
   assert page.get_by_role('heading',name='Send Dena',exact=True).count()==0
   browser.close()
  report['browser_self_custody_send_receive_qr_network_guard_and_dena']='passed'
 # A longer independent fork omits transfers but legitimately funds Alice.
 prior_height=max(remote('query')['height'],json.loads(run('status',work/'node1').stdout)['height'])
 run('mine',work/'node2',alice,6)
 run('mine',work/'node2',miner,prior_height+6)
 journal=(work/'node2/blocks.log').read_bytes();position=0
 while position<len(journal):
  size=int.from_bytes(journal[position:position+4],'big');rawblock=journal[position+4:position+4+size]
  path=work/'fork-block.bin';path.write_bytes(rawblock)
  run('submit',work/'client',endpoint(0),path)
  position+=4+size+32
 assert http('/transactions/'+txid)[1]['status']=='pending'
 assert http('/accounts/'+bob)[1]['spendable']=='0'
 assert http('/accounts/'+alice)[1]['nonce']=='0'
 report['reorg_rolls_back_and_requeues_valid_detached_transfer']='passed'
 stop(node0)
 node0=spawn([BIN,'node',work/'node0',endpoint(0)],'node0-restart')
 wait(lambda:http('/transactions/'+txid)[1]['status']=='unknown')
 assert http('/transactions',payload)[0]==202
 stop(node0)
 node0=spawn([BIN,'node',work/'node0',endpoint(0),'--mine-to',miner],'node0-remine')
 wait(lambda:http('/network')[0]==200)
 try:urlopen(gateway+'/network',timeout=5);raise AssertionError('unauthenticated gateway request accepted')
 except HTTPError as error:assert error.code==401
 report['gateway_authentication']='passed'
 assert http('/transactions',payload)[0]==202
 wait(lambda:http('/transactions/'+txid)[1]['status']=='confirmed')
 stop(node0)
 node0=spawn([BIN,'node',work/'node0',endpoint(0)],'node0-final')
 wait(lambda:http('/transactions/'+txid)[1]['status']=='confirmed')
 assert http('/accounts/'+bob)[1]['spendable']=='25'
 report['restart_history_rebuild_and_idempotent_resubmission']='passed'
 print('Reorganization, volatile queue restart, exact retransmission and durable history passed',flush=True)
 report['status']='passed'
except BaseException as error:
 report['status']='failed';report['error_type']=type(error).__name__
 raise
finally:
 for process in reversed(processes):
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 for log in logs:log.close()
 out=Path(os.environ.get('PQCOIN_WALLET_REPORT',str(ROOT/'artifacts/pow/wallet-network-report.json')))
 out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
 print('Report:',out,flush=True)
