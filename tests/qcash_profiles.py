#!/usr/bin/env python3
"""Synthetic QCash profile linking against local Docker PIdP and real native coins.
The fixture directory/database and all wallet keys are isolated from portal replicas.
"""
import json,os,re,secrets,socket,subprocess,tempfile,time,uuid,signal
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];ORG=ROOT.parent/'OrgPortal';BIN=ROOT/'build/qcash'
BASE=os.environ.get('PLAYWRIGHT_BASE_URL','https://localhost:8443').rstrip('/')
CONTAINER=os.environ.get('QCASH_PIDP_CONTAINER','lifetech-identity-api')
assert urlparse(BASE).hostname in {'localhost','127.0.0.1'}
inspect=json.loads(subprocess.check_output(['docker','inspect',CONTAINER],text=True))[0]
env=dict(item.split('=',1) for item in inspect['Config']['Env'] if '=' in item)
assert inspect['State']['Running'] and env.get('EMAIL_VERIFICATION_DELIVERY','log')=='log'
identity_port=int(subprocess.check_output(['docker','exec',CONTAINER,'/venv/bin/python','-c','import socket; s=socket.socket(); s.bind(("0.0.0.0",0)); print(s.getsockname()[1]); s.close()'],text=True))
ip=next(iter(inspect['NetworkSettings']['Networks'].values()))['IPAddress'];IDENTITY='http://'+ip+':'+str(identity_port)
assert ip.startswith(('172.','10.','192.168.'))
origin=BASE
assert urlparse(origin).hostname.endswith('.localhost') or urlparse(origin).hostname in ['localhost','127.0.0.1']
work=Path(tempfile.mkdtemp(prefix='qcash-profiles-',dir=ROOT/'.deps'));children=[];logs=[]
fixture_database='qcash_fixture_'+uuid.uuid4().hex
report={'isolated_identity_database':fixture_database,'fixture_email_verification_required':False,'account_namespace':'qcash-fixture','status':'running','work_directory':str(work),'portal':BASE,'local_docker_identity':CONTAINER}
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def interrupted(*_):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,interrupted)
peer_port,gateway_port,adapter_port=port(),port(),port()
peer=f'127.0.0.1:{peer_port}';gateway=f'http://127.0.0.1:{gateway_port}';adapter=f'http://127.0.0.1:{adapter_port}'
def run(*args):return subprocess.check_output([str(BIN),*map(str,args)],text=True,stderr=subprocess.PIPE).strip()
def spawn(args,name,environment=None):
 log=open(work/(name+'.log'),'wb');logs.append(log)
 child=subprocess.Popen(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,env=environment);children.append(child)
def wait(f,timeout=30):
 end=time.monotonic()+timeout;last=None
 while time.monotonic()<end:
  try:
   value=f()
   if value:return value
  except Exception as e:last=type(e).__name__
  time.sleep(.1)
 raise AssertionError('Timed out: '+str(last))
def good(response):
 assert response.ok,f'local request failed: {response.status}'
 return response.json()
try:
 fixture_code="""
import asyncio,os,secrets,asyncpg
from pathlib import Path
from sqlalchemy.engine import make_url
async def start():
 name=os.environ['QCASH_FIXTURE_DATABASE']
 assert name.startswith('qcash_fixture_') and name.replace('_','').isalnum()
 url=make_url(os.environ['DATABASE_URL'])
 c=await asyncpg.connect(url.set(drivername='postgresql').render_as_string(hide_password=False))
 await c.execute('CREATE DATABASE "'+name+'"');await c.close()
 os.environ.update(DATABASE_URL=url.set(database=name).render_as_string(hide_password=False),AUTO_CREATE_TABLES='true',SECRET_KEY=secrets.token_urlsafe(48),TOKEN_ALGORITHM='HS256',JWT_PRIVATE_KEY='',JWT_PUBLIC_KEY='',JWT_ISSUER=os.environ['QCASH_FIXTURE_ORIGIN'],PUBLIC_BASE_URL=os.environ['QCASH_FIXTURE_ORIGIN'],ALLOWED_ORIGINS=os.environ['QCASH_FIXTURE_ORIGIN'],PORTAL_SHARED_IDENTITY='false',PORTAL_SSO_APP_SLUG='qcash-fixture',EMAIL_VERIFICATION_REQUIRED='false',EMAIL_VERIFICATION_DELIVERY='log',ADMIN_EMAILS='',ADMIN_USER_IDS='',GOOGLE_CLIENT_ID='',GITHUB_CLIENT_ID='',PII_ENCRYPTION_KEYS='',REQUIRE_PII_ENCRYPTION='false')
 pid=Path('/tmp/'+name+'.pid');pid.write_text(str(os.getpid()));pid.chmod(0o600)
 os.execv('/venv/bin/uvicorn',['/venv/bin/uvicorn','main:app','--app-dir','/app','--host','0.0.0.0','--port',os.environ['QCASH_FIXTURE_PORT']])
asyncio.run(start())
"""
 spawn(['docker','exec','-e','QCASH_FIXTURE_DATABASE='+fixture_database,'-e','QCASH_FIXTURE_ORIGIN='+BASE,'-e','QCASH_FIXTURE_PORT='+str(identity_port),CONTAINER,'/venv/bin/python','-c',fixture_code],'identity-fixture')
 for name in ['node','client']:run('init',work/name,ROOT/'artifacts/devnet-genesis/genesis.bin')
 chain=json.loads(run('status',work/'node'))['chain']
 loader=ORG/'org-worker/node_modules/tsx/dist/loader.mjs'
 subprocess.run(['node','--import',str(loader),str(ORG/'web/scripts/pqcoin-fixture.ts'),chain,str(work)],check=True,capture_output=True,text=True)
 fixture=json.loads((work/'fixture.json').read_text())
 run('mine',work/'node',fixture['alice'],6)
 run('trust',work/'client',peer,run('identity',work/'node'))
 spawn([BIN,'node',work/'node',peer,'--mine-to','44'*32],'node')
 spawn(['python3',ROOT/'tools/wallet_gateway.py','--binary',BIN,'--home',work/'client','--peer',peer,'--port',gateway_port],'gateway')
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox'])
  contexts=[];identities=[]
  bootstrap=browser.new_context(ignore_https_errors=True)
  wait(lambda:bootstrap.request.get(IDENTITY+'/health').ok)
  owner_email='bootstrap-'+uuid.uuid4().hex+'@example.com';owner_password=secrets.token_urlsafe(24)
  good(bootstrap.request.post(IDENTITY+'/auth/register',data={'email':owner_email,'password':owner_password,'full_name':'Synthetic fixture website owner'}))
  owner_token=good(bootstrap.request.post(IDENTITY+'/auth/token',form={'username':owner_email,'password':owner_password}))['access_token']
  good(bootstrap.request.post(IDENTITY+'/websites',headers={'Authorization':'Bearer '+owner_token},data={'name':'QCash isolated fixture','slug':'qcash-fixture','allowed_redirect_origins':[BASE]}))
  bootstrap.close()
  for who in ['alice','bob']:
   ctx=browser.new_context(ignore_https_errors=True,viewport={'width':1280,'height':1000});contexts.append(ctx)
   suffix=uuid.uuid4().hex[:12];email=f'qcash-{who}-{suffix}@example.com';password=secrets.token_urlsafe(24);name=f'QCash Dummy {who.title()} {suffix}'
   response=ctx.request.post(IDENTITY+'/websites/qcash-fixture/auth/register',data={'email':email,'password':password,'full_name':name},headers={'Origin':origin})
   good(response)
   issued=good(ctx.request.post(IDENTITY+'/websites/qcash-fixture/auth/token',data={'email':email,'password':password}))['access_token']
   assert ctx.request.post(IDENTITY+'/auth/session/exchange',headers={'Authorization':'Bearer '+issued,'Origin':origin}).ok
   cookies='; '.join(cookie['name']+'='+cookie['value'] for cookie in ctx.storage_state()['cookies'])
   token=good(ctx.request.get(IDENTITY+'/auth/session-token',headers={'Cookie':cookies}))['access_token']
   user=good(ctx.request.get(IDENTITY+'/auth/me',headers={'Authorization':'Bearer '+token}))
   assert user['id']==user['canonical_user_id'] and user['account_id'] and user['account_subject']
   identities.append({'userId':user['id'],'name':name,'slug':'qcash-dummy-'+who+'-'+suffix,'token':token})
  # Only the synthetic profile fixtures go into the isolated in-memory database.
  fixture_path=work/'profiles.json';fixture_path.write_text(json.dumps([{k:v for k,v in identity.items() if k!='token'} for identity in identities]));fixture_path.chmod(0o600)
  spawn(['node','--import',loader,ORG/'org-worker/scripts/pqcoin-local-adapter.ts'],'adapter',{**os.environ,'QCASH_CHAIN_ID':chain,'QCASH_GATEWAY_URL':gateway,'PQCOIN_ADAPTER_PORT':str(adapter_port),'QCASH_TEST_PORTAL_ORIGIN':BASE,'QCASH_PROFILE_FIXTURE':str(fixture_path),'QCASH_LOCAL_PIDP_URL':IDENTITY})
  wait(lambda:contexts[0].request.get(adapter+'/api/qcash/network').ok)
  for ctx in contexts:
   ctx.route('**/*',lambda route:route.continue_() if urlparse(route.request.url).hostname in {'localhost','127.0.0.1','id.localhost'} else route.abort())
   def identity_route(route,_request=None,ctx=ctx):
    parsed=urlparse(route.request.url);path=parsed.path.removeprefix('/pidp')
    response=ctx.request.fetch(IDENTITY+path+('?' + parsed.query if parsed.query else ''),method=route.request.method,data=route.request.post_data,headers={**route.request.headers,'Origin':origin,'Cookie':'; '.join(cookie['name']+'='+cookie['value'] for cookie in ctx.storage_state()['cookies'])})
    route.fulfill(response=response)
   ctx.route('**/pidp/**',identity_route)
   ctx.route('https://id.localhost:8443/**',identity_route)
   # Prevent incidental authenticated fixtures from entering the shared read replica.
   ctx.route('**/api/org/**',lambda route:route.fulfill(status=503,json={'error':'Isolated wallet test; organization replica untouched.'}))
   def wallet_route(route,_request=None,ctx=ctx):
    parsed=urlparse(route.request.url);path=parsed.path.removeprefix('/api/org')
    response=ctx.request.fetch(adapter+path+('?' + parsed.query if parsed.query else ''),method=route.request.method,data=route.request.post_data,headers={'Content-Type':'application/json',**({'Authorization':route.request.headers['authorization']} if 'authorization' in route.request.headers else {})})
    route.fulfill(response=response)
   ctx.route('**/api/org/api/qcash/**',wallet_route)
  pages=[]
  for i,(ctx,who) in enumerate(zip(contexts,['alice','bob'])):
   page=ctx.new_page();pages.append(page);page.goto(BASE+'/receive?currency=QCash',wait_until='domcontentloaded')
   expect(page.get_by_role('heading',name='Receive QCash Testnet',exact=True)).to_be_visible(timeout=30000)
   page.get_by_label('Backup password',exact=True).fill(fixture['password']);page.get_by_label('Unlock encrypted wallet backup',exact=True).set_input_files(work/(who+'-backup.json'))
   expect(page.get_by_test_id('pqt-address')).to_have_text(fixture[who])
   panel=page.get_by_role('region',name='QCash profile wallet',exact=True)
   expect(panel.get_by_label('I authorize publishing this wallet link on my public profile.',exact=True)).to_be_enabled(timeout=30000)
   panel.get_by_label('I authorize publishing this wallet link on my public profile.',exact=True).check()
   panel.get_by_role('button',name='Authorize profile wallet link',exact=True).click()
   expect(panel.get_by_role('status')).to_have_text('Verified wallet linked to your profile.',timeout=30000)
  alice,bob=pages
  alice.get_by_role('link',name='Send QCash',exact=True).click()
  alice.get_by_label('Find QCash recipient by name',exact=True).fill(identities[1]['name'])
  alice.get_by_role('button',name=re.compile(re.escape(identities[1]['name'])+' ·')).click()
  expect(alice.get_by_label('Recipient QCash wallet address',exact=True)).to_have_value(fixture['bob'])
  alice.get_by_label('Amount (QCash)',exact=True).fill('5')
  alice.get_by_role('button',name='Review payment',exact=True).click()
  expect(alice.get_by_role('region',name='Review QCash payment',exact=True)).to_contain_text(identities[1]['name'])
  # Remove the recipient's link after review; fresh resolution must stop signing.
  good(contexts[1].request.delete(adapter+'/api/qcash/profile-wallet/me',headers={'Authorization':'Bearer '+identities[1]['token']}))
  alice.get_by_role('button',name='Authorize and send QCash',exact=True).click()
  expect(alice.get_by_role('status').filter(has_text='Recipient has no current shared QCash wallet')).to_be_visible(timeout=30000)
  account=good(contexts[0].request.get(gateway+'/accounts/'+fixture['alice']));assert account['nonce']=='0'
  assert good(contexts[0].request.get(gateway+'/accounts/'+fixture['bob']))['spendable']=='0'
  # Relink with a new revision, choose it again, then authorize a real transfer.
  panel=bob.get_by_role('region',name='QCash profile wallet',exact=True)
  panel.get_by_label('I authorize publishing this wallet link on my public profile.',exact=True).check()
  panel.get_by_role('button',name='Authorize wallet link replacement',exact=True).click()
  expect(panel.get_by_role('status')).to_have_text('Verified wallet linked to your profile.',timeout=30000)
  alice.get_by_role('button',name='Review payment',exact=True).click()
  expect(alice.get_by_role('status').filter(has_text='Recipient wallet link changed')).to_be_visible(timeout=30000)
  assert good(contexts[0].request.get(gateway+'/accounts/'+fixture['alice']))['nonce']=='0'
  alice.get_by_label('Find QCash recipient by name',exact=True).fill(identities[1]['name'])
  alice.get_by_role('button',name=re.compile(re.escape(identities[1]['name'])+' ·')).click()
  alice.get_by_role('button',name='Review payment',exact=True).click()
  alice.get_by_role('button',name='Authorize and send QCash',exact=True).click()
  wait(lambda:good(contexts[0].request.get(gateway+'/accounts/'+fixture['bob']))['spendable']=='5')
  expect(alice.get_by_test_id('pqt-spendable')).to_have_text('295 QCash',timeout=30000)
  for width in [1280,390]:
   alice.set_viewport_size({'width':width,'height':1000});assert alice.evaluate('document.documentElement.scrollWidth<=innerWidth')
   alice.screenshot(path=str(work/f'qcash-profiles-{width}.png'),full_page=True)
  bob.get_by_role('button',name='Lock wallet',exact=True).click()
  expect(panel.get_by_role('button',name='Remove profile wallet link',exact=True)).to_be_enabled()
  panel.get_by_role('button',name='Remove profile wallet link',exact=True).click()
  expect(panel.get_by_role('status')).to_have_text('Wallet link removed. Blockchain transfers are unchanged.')
  result=contexts[0].request.get(adapter+'/api/qcash/profile-wallet/recipients/'+identities[1]['userId'],headers={'Authorization':'Bearer '+identities[0]['token']})
  assert result.status==404
  report.update(status='passed',real_local_identity=True,ownership_link=True,send_by_name=True,revocation_after_review_blocks_signing=True,replacement_requires_fresh_selection=True,unlink_hides_recipient=True,browser_mobile_and_desktop=True)
  browser.close()
except BaseException as error:
 report.update(status='failed',error_type=type(error).__name__);raise
finally:
 # Stop only the PID recorded by this isolated fixture, then remove only its DB.
 cleanup="""
import asyncio,os,signal,asyncpg
from pathlib import Path
async def clean():
 name=os.environ['QCASH_FIXTURE_DATABASE'];assert name.startswith('qcash_fixture_') and name.replace('_','').isalnum()
 path=Path('/tmp/'+name+'.pid')
 if path.exists():
  try:os.kill(int(path.read_text()),signal.SIGTERM)
  except ProcessLookupError:pass
  path.unlink()
 c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
 await c.execute('DROP DATABASE IF EXISTS "'+name+'" WITH (FORCE)');await c.close()
asyncio.run(clean())
"""
 subprocess.run(['docker','exec','-e','QCASH_FIXTURE_DATABASE='+fixture_database,CONTAINER,'/venv/bin/python','-c',cleanup],capture_output=True,timeout=20)
 for child in reversed(children):
  child.terminate()
  try:child.wait(timeout=12)
  except subprocess.TimeoutExpired:child.kill();child.wait()
 for log in logs:log.close()
 (ROOT/'artifacts/pow/qcash-profile-report.json').write_text(json.dumps(report,indent=2)+'\n')
 print('QCash profile report:',ROOT/'artifacts/pow/qcash-profile-report.json',flush=True)
