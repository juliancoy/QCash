#!/usr/bin/env python3
"""Run initialized local validators in the foreground; Ctrl-C stops all children."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--network", type=Path, required=True)
args = parser.parse_args()
homes = sorted(p for p in args.network.resolve().glob("node*") if (p / "config/runtime.json").is_file())
if len(homes) < 4:
    parser.error("network must contain at least four initialized validators")
processes = []

def stop_signal(_signal, _frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, stop_signal)
try:
    for home in homes:
        processes.append(subprocess.Popen([str(ROOT / "build/pqcoind"), "start", "--home", str(home)],
                                          env=dict(os.environ, GOMAXPROCS="2")))
    print(f"Running {len(processes)} local validators; Ctrl-C stops them. Restart with the same command.", flush=True)
    while True:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"validator exited with status {process.returncode}")
        time.sleep(0.25)
except KeyboardInterrupt:
    pass
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
