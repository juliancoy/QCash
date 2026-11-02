"""Private bounded pipe client for a persistent local Vulkan process; no sockets."""
import json
import os
from pathlib import Path
import re
import selectors
import signal
import struct
import subprocess
import time


class VulkanWorker:
    def __init__(self, binary, *, deadline, cancelled=lambda: False, options=()):
        self.deadline = deadline
        self.cancelled = cancelled
        self.request_id = 0
        self.active = False
        self.buffer = bytearray()
        self.selector = selectors.DefaultSelector()
        self.process = subprocess.Popen([str(binary), 'serve', '--seconds',
            str(max(1, min(3600, int(deadline-time.monotonic())+1))), *map(str, options)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, bufsize=0)
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.ready = self._read(min(deadline, time.monotonic()+20))
            if (self.ready.get('status') != 'ready' or
                self.ready.get('protocol') != 'qcash-vulkan-worker-v1' or
                self.ready.get('worker_pid') != self.process.pid):
                raise RuntimeError('Invalid Vulkan worker handshake')
        except BaseException:
            self.close()
            raise

    def _read(self, deadline):
        while True:
            if self.cancelled() or time.monotonic() >= min(self.deadline, deadline):
                raise InterruptedError('Vulkan worker stopped or deadline reached')
            if b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeError('Invalid Vulkan worker reply')
                return value
            if not self.selector.select(.05):
                continue
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError('Vulkan worker exited without a complete reply')
            self.buffer.extend(chunk)
            if len(self.buffer) > 65536:
                raise RuntimeError('Vulkan worker reply exceeds bound')

    def solve(self, work, output, attempts, chain_id, reward_address, seconds=19):
        if not 1 <= attempts <= 100000000 or not 1 <= seconds <= 300:
            raise ValueError('Invalid Vulkan job budget')
        for value in [chain_id, reward_address]:
            if not re.fullmatch('[0-9a-f]{64}', value) or value == '0'*64:
                raise ValueError('Invalid Vulkan work identity')
        self.request_id += 1
        paths = [os.fsencode(Path(p).absolute()).hex() for p in [work, output]]
        payload = ' '.join(map(str, [self.request_id, attempts, seconds, chain_id, reward_address, *paths])).encode('ascii')
        if len(payload) > 16384:
            raise ValueError('Vulkan request exceeds bound')
        if self.cancelled() or time.monotonic() >= self.deadline:
            raise InterruptedError('Vulkan worker deadline reached')
        self.active = True
        packet = memoryview(struct.pack('!I', len(payload))+payload)
        while packet:
            written = os.write(self.process.stdin.fileno(), packet)
            if written <= 0:
                raise RuntimeError('Vulkan worker input closed')
            packet = packet[written:]
        result = self._read(min(self.deadline, time.monotonic()+seconds+1))
        if result.get('request_id') != self.request_id or result.get('worker_pid') != self.process.pid:
            raise RuntimeError('Vulkan worker response identity mismatch')
        if result.get('status') not in ['solved', 'exhausted', 'budget_ended', 'cancelled', 'failed']:
            raise RuntimeError('Invalid Vulkan job result')
        self.active = False
        return result

    def close(self):
        if self.active and self.process.poll() is None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait()
        self.selector.close()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
