#!/usr/bin/env python3
"""Retain clock snapshots and cold/warm WebGPU timings after CPU-only mining."""
import argparse
import functools
import http.server
import json
import subprocess
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_webgpu_mining import Handler,ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():p.error('Use a new output path')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    fixture=json.loads((ROOT/'benchmarks/mining-speed-ryzen5800x-rtx3060.json').read_text())['fixture']['job'];hard=bytearray.fromhex(fixture['header']);hard[100:132]=(1).to_bytes(32,'big')
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'miners')));threading.Thread(target=server.serve_forever,daemon=True).start()
    def telemetry():return subprocess.check_output(['nvidia-smi','--query-gpu=pstate,clocks.gr,clocks.sm,clocks.mem,power.draw,utilization.gpu','--format=csv,noheader'],text=True,timeout=10).strip()
    result={'note':'Read-only clock snapshots outside measured intervals; no driver settings changed. CPU-only phase naturally idles the GPU.','cycles':[]}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/google-chrome',headless=False,args=['--no-sandbox','--enable-unsafe-webgpu','--use-angle=vulkan','--enable-features=Vulkan','--ignore-gpu-blocklist','--enable-gpu']);page=browser.new_page();page.set_default_timeout(60000)
            page.goto(f'http://localhost:{server.server_port}/webgpu/')
            page.evaluate("""async header=>{const {createWasmMiner}=await import('../wasm/miner.mjs');const {createWebGPUMiner}=await import('./miner.mjs');window.cpu=await createWasmMiner();window.gpu=await createWebGPUMiner();const bytes=Uint8Array.from(header.match(/../g),x=>parseInt(x,16));window.start=new DataView(bytes.buffer).getBigUint64(260,false);await cpu.prepare(bytes);await gpu.prepare(bytes);await gpu.run(start,16777216);}""",hard.hex())
            for _ in range(3):
                page.evaluate('cpu.run(start,16777216)');row={'before_first':telemetry(),'visibility':page.evaluate('({visibility:document.visibilityState,focus:document.hasFocus()})')}
                row['first']=page.evaluate('gpu.run(start,16777216)');row['after_first']=telemetry()
                row['second']=page.evaluate('gpu.run(start,16777216)');row['after_second']=telemetry();result['cycles'].append(row)
            page.evaluate('cpu.dispose();gpu.dispose()');browser.close()
    finally:server.shutdown();a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(a.output)
if __name__=='__main__':main()
