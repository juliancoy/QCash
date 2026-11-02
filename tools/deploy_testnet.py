#!/usr/bin/env python3
"""Install a reviewed QCash revision as bounded user services. Testnet only."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def main():
    os.umask(0o077)
    if run('git', '-C', ROOT, 'status', '--porcelain'):
        raise RuntimeError('Commit reviewed changes before deploying')
    commit = run('git', '-C', ROOT, 'rev-parse', 'HEAD')
    if run('git', '-C', ROOT, 'ls-remote', 'origin', 'refs/heads/main').split()[0] != commit:
        raise RuntimeError('Push this revision to main before deploying')
    run('cmake', '--build', ROOT / 'build', '--parallel', '2')
    run('ctest', '--test-dir', ROOT / 'build', '--output-on-failure')
    state = Path.home() / '.local/state/qcash-testnet'
    release = Path.home() / '.local/share/qcash/releases' / commit
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source, name in [(ROOT / 'build/qcash', 'qcash'), (ROOT / 'tools/wallet_gateway.py', 'wallet_gateway.py')]:
        target = release / name
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise RuntimeError('Existing immutable release has different contents')
        if not target.exists():
            shutil.copy2(source, target)
    binary = release / 'qcash'
    config_file = state / 'deployment.json'
    if config_file.exists():
        config = json.loads(config_file.read_text())
    else:
        # This host's testnet rewards belong to its separate CLI mining wallet.
        # Browser wallets remain self-custodial; never export this key to the API.
        address = run(binary, 'wallet', state / 'miner.pem')
        config = {'currency': 'QCash', 'experimental': True, 'mine_to': address}
        config_file.write_text(json.dumps(config, indent=2) + '\n')
    for name in ['node', 'client']:
        if not (state / name).exists():
            run(binary, 'init', state / name, ROOT / 'artifacts/devnet-genesis/genesis.bin')
    peer = '127.0.0.1:29700'
    run(binary, 'trust', state / 'client', peer, run(binary, 'identity', state / 'node'))
    token = state / 'gateway-token'
    if not token.exists():
        token.write_text(secrets.token_urlsafe(48) + '\n')
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    # RuntimeMaxSec renews the node's 24-hour self-signed certificate using the
    # same pinned identity. The journal survives; pending transfers are volatile.
    commands = {
        'qcash-testnet-node': f'{binary} node {state}/node {peer} --mine-to {config["mine_to"]} --mine-interval 30',
        'qcash-testnet-gateway': f'/usr/bin/python3 {release}/wallet_gateway.py --binary {binary} --home {state}/client --peer {peer} --port 29702 --token-file {token} --release-commit {commit}',
    }
    for name, command in commands.items():
        dependency = 'After=qcash-testnet-node.service\nWants=qcash-testnet-node.service\n' if name.endswith('gateway') else ''
        (units / (name + '.service')).write_text(f'''[Unit]
Description=QCash Beta test network ({name})
After=network-online.target
{dependency}[Service]
ExecStart={command}
Restart=always
RestartSec=3
RuntimeMaxSec=20h
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={state}
MemoryMax=256M
CPUQuota=10%
TasksMax=32
LimitCORE=0
[Install]
WantedBy=default.target
''')
    run('systemctl', '--user', 'daemon-reload')
    run('systemctl', '--user', 'enable', '--now', *[name + '.service' for name in commands])
    run('systemctl', '--user', 'restart', *[name + '.service' for name in commands])
    for attempt in range(30):
        try:
            chain_id = json.loads(run(binary, 'query', state / 'client', peer))['chain']
            break
        except subprocess.CalledProcessError:
            if attempt == 29:raise
            time.sleep(1)
    config.update(name='QCash Beta', commit=commit, binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), chain_id=chain_id)
    config_file.write_text(json.dumps(config, indent=2) + '\n')
    print(json.dumps({k: v for k, v in config.items() if k != 'mine_to'}, indent=2))


if __name__ == '__main__':
    main()
