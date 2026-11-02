#!/usr/bin/env python3
"""Local browser work upload -> GPU proof download -> pinned native node acceptance."""
import argparse
import functools
import hashlib
import http.server
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from playwright.sync_api import sync_playwright
from test_webgpu_mining import Handler,ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--backend',choices=['webgpu','1','2','4','8'],default='webgpu');a=p.parse_args()
    if a.output.exists():p.error('Use a new receipt path')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    binary=ROOT/'build/qcash'
    def native(*args):return subprocess.check_output([str(binary),*map(str,args)],text=True,timeout=30).strip()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'miners')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    result={'status':'running'}
    try:
        with tempfile.TemporaryDirectory(prefix='qcash-browser-node-') as tmp:
            d=Path(tmp);native('init',d/'node',ROOT/'artifacts/devnet-genesis/genesis.bin');native('init',d/'client',ROOT/'artifacts/devnet-genesis/genesis.bin')
            reward=native('wallet',d/'reward.pem');identity=native('identity',d/'node')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));peer=f'127.0.0.1:{sock.getsockname()[1]}'
            with (d/'node.log').open('w') as log:
                node=subprocess.Popen([str(binary),'node',str(d/'node'),peer],stdout=log,stderr=log)
                try:
                    native('trust',d/'client',peer,identity)
                    for _ in range(100):
                        try:native('query',d/'client',peer);break
                        except subprocess.CalledProcessError:
                            if node.poll() is not None:raise RuntimeError('Local node exited')
                            time.sleep(.05)
                    else:raise RuntimeError('Local node did not start')
                    work=d/'work.bin';native('work',d/'client',peer,reward,work)
                    with sync_playwright() as pw:
                        browser=pw.chromium.launch(executable_path='/usr/bin/google-chrome',headless=False,args=['--no-sandbox','--enable-unsafe-webgpu','--use-angle=vulkan','--enable-features=Vulkan','--ignore-gpu-blocklist','--enable-gpu'])
                        page=browser.new_page(accept_downloads=True);page.set_default_timeout(30000)
                        page.add_init_script("window.adapterRequests=0;const original=navigator.gpu.requestAdapter.bind(navigator.gpu);navigator.gpu.requestAdapter=(...args)=>{window.adapterRequests++;return original(...args);};")
                        page.goto(f'http://localhost:{server.server_port}/webgpu/')
                        assert page.locator('#scan').is_disabled() and page.locator('#cancel').is_disabled()
                        page.locator('#work').set_input_files(str(work));page.wait_for_function("!document.getElementById('scan').disabled")
                        assert page.evaluate('window.adapterRequests')==0,'GPU initialized without Scan'
                        page.locator('#backend').select_option(a.backend)
                        page.locator('#count').fill('1048576');page.locator('#scan').click()
                        page.wait_for_function("document.getElementById('status').textContent.includes('Verified winner:')")
                        assert page.locator('#cancel').is_disabled() and page.locator('#download').is_enabled()
                        with page.expect_download() as download:page.locator('#download').click()
                        solved=d/'downloaded.bin';download.value.save_as(str(solved))
                        assert solved.stat().st_size==272
                        if a.backend!='webgpu':assert page.evaluate('window.adapterRequests')==0,'WASM unexpectedly initialized GPU'
                        original=work.read_bytes();raw=solved.read_bytes()
                        assert raw[:260]==original[:260] and raw[268:]==original[268:]
                        digest=hashlib.sha3_256(raw[:268]).hexdigest();assert int(digest,16)<=int.from_bytes(raw[100:132],'big')
                        completed=d/'native-completed.bin'
                        verified=json.loads(native('complete-work',work,raw[228:260].hex(),str(int.from_bytes(raw[260:268],'big')),completed))
                        assert completed.read_bytes()==raw
                        native('submit',d/'client',peer,solved)
                        tip=json.loads(native('query',d/'client',peer));assert tip['height']==1
                        page.screenshot(path=str(a.output.with_suffix('.png')),full_page=True)
                        result={'status':'passed','backend':a.backend,'no_gpu_initialization_before_scan':True,'downloaded_bytes':272,'sha3':digest,'native_validation':verified,'local_node_height':tip['height'],'browser':browser.version,'native_binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'status_text':page.locator('#status').inner_text()}
                        browser.close()
                finally:
                    node.terminate()
                    try:node.wait(timeout=10)
                    except subprocess.TimeoutExpired:node.kill();node.wait()
    except Exception as error:result={'status':'failed','error':str(error)};raise
    finally:server.shutdown();a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(a.output)
if __name__=='__main__':main()
