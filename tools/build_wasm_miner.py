#!/usr/bin/env python3
"""Build the explicit SIMD128 mining kernel with an installed Emscripten."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--emcc',type=Path);a=p.parse_args()
    compiler=a.emcc or shutil.which('emcc')
    if not compiler:p.error('Pass --emcc /path/to/installed/emcc')
    source=ROOT/'miners/wasm/kernel.cpp';output=ROOT/'miners/wasm/kernel.wasm'
    flags=['-std=c++17','-O3','-msimd128','-fno-exceptions','-fno-rtti','--no-entry','-sSTANDALONE_WASM=1','-sFILESYSTEM=0','-sINITIAL_MEMORY=1048576','-sSTACK_SIZE=65536','-sEXPORTED_FUNCTIONS=["_get_header","_get_output","_prepare","_scan"]']
    subprocess.run([str(compiler),str(source),*flags,'-o',str(output)],check=True,timeout=120)
    output.chmod(0o644)
    manifest={'compiler':subprocess.check_output([str(compiler),'--version'],text=True).splitlines()[0],'flags':flags,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'wasm_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'bytes':output.stat().st_size,'simd':'explicit wasm_simd128.h, two independent uint64 nonce lanes','memory':'one private nonshared linear memory per worker'}
    (output.parent/'build.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
