#!/usr/bin/env python3
"""Create a public-source audit snapshot from an explicit allowlist."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/audit'
DIRECTORIES=('include','src','tests','tools','third_party')
TOP=('CMakeLists.txt','CMakePresets.json','vcpkg.json','README.md','POW.md',
     'SECURITY_REVIEW.md','CONSENSUS.md','reference-sources.json','.gitignore')
REPORTS=('consensus-adversary.json','quantum-circuit-resources.json',
         'network-report.json','network-report-sanitize.json','clang-tidy.txt',
         'parser-fuzz.txt','review-validation.json','ctest-release.xml','ctest-sanitize.xml')

def main():
    files={ROOT/p for p in TOP}
    for directory in DIRECTORIES:
        files.update(p for p in (ROOT/directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pem','.pyc'))
    files.update(ROOT/'artifacts/pow'/p for p in REPORTS)
    files.add(ROOT/'artifacts/devnet-genesis/genesis.bin')
    payload={}
    for p in sorted(files):
        if p.is_symlink():raise RuntimeError(f'symlink refused: {p}')
        if not p.is_file():raise RuntimeError(f'missing audit input: {p}')
        payload[p.relative_to(ROOT).as_posix()]=p.read_bytes()
    manifest={name:hashlib.sha256(data).hexdigest() for name,data in payload.items()}
    payload['AUDIT-MANIFEST.json']=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    OUT.mkdir(parents=True,exist_ok=True)
    archive=OUT/'pqcoin-review.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,data in sorted(payload.items()):
            info=tarfile.TarInfo('pqcoin/'+name);info.size=len(data);info.mode=0o644
            if name.startswith('tools/') and name.endswith('.sh'):info.mode=0o755
            tar.addfile(info,io.BytesIO(data))
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (OUT/'pqcoin-review.tar.gz.sha256').write_text(f'{digest}  {archive.name}\n')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print(f'{archive}: {len(manifest)} public files; SHA256 {digest}')
if __name__=='__main__':main()
