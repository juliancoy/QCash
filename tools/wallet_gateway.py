#!/usr/bin/env python3
"""Local testnet HTTP adapter. Holds a TLS peer identity, never wallet keys.

Bind loopback or a private Docker bridge; place behind the portal adapter.
No public hosting, custody, fiat conversion or production security claim.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

HEX=re.compile(r'^[0-9a-f]{64}$')

def serve(binary,home,peer,host,port,token=None,release_commit=None):
    def call(command,*args):
        result=subprocess.run([str(binary),command,str(home),peer,*map(str,args)],capture_output=True,timeout=10)
        if result.returncode:raise ValueError('Node rejected request; check funds, nonce, network and pending transfers.')
        return json.loads(result.stdout)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(12)
            super().setup()
        def log_message(self,*args):pass # Never log addresses, signed payloads or wallet-related paths.
        def reply(self,status,data):
            raw=json.dumps(data,separators=(',',':')).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def dispatch(self):
            self.connection.settimeout(12)
            if token and not hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+token).encode()):
                return self.reply(401,dict(error='Gateway authorization required.'))
            path=urlsplit(self.path).path
            if self.command=='GET' and path=='/network':
                status=call('query')
                return self.reply(200,dict(currency='QCash',name='QCash Testnet',chainId=status['chain'],height=status['height'],tip=status['tip'],decimals=0,experimental=True,commit=release_commit))
            for prefix,command in [('/accounts/','account-query'),('/transactions/','transaction-query')]:
                if self.command=='GET' and path.startswith(prefix) and HEX.fullmatch(path[len(prefix):]):
                    return self.reply(200,call(command,path[len(prefix):]))
            if self.command=='POST' and path=='/transactions':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=12000: return self.reply(413,dict(error='Invalid request size.'))
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict) or set(body)!={'signedTransfer'}:raise ValueError('Expected a signed transfer only.')
                raw=body['signedTransfer']
                if not isinstance(raw,str) or not re.fullmatch(r'[0-9a-f]{10944}',raw):raise ValueError('Invalid signed transfer encoding.')
                data=bytes.fromhex(raw)
                # Private temporary directory, removed after the native TLS request.
                with tempfile.TemporaryDirectory(prefix='pqcoin-submit-') as directory:
                    path=Path(directory)/'transfer.bin';path.write_bytes(data);path.chmod(0o600)
                    result=call('send-transfer',path)
                if result['id']!=hashlib.sha3_256(data).hexdigest():raise ValueError('Transaction identity mismatch.')
                return self.reply(202,result)
            self.reply(404,dict(error='Not found.'))
        def do_GET(self):self.safe()
        def do_POST(self):self.safe()
        def safe(self):
            try:self.dispatch()
            except (ValueError,json.JSONDecodeError):self.reply(400,dict(error='Request rejected by testnet node.'))
            except (OSError,subprocess.SubprocessError):self.reply(503,dict(error='Testnet node unavailable.'))
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        request_queue_size=8
        def __init__(self,*args):
            self.slots=threading.BoundedSemaphore(8)
            super().__init__(*args)
        def process_request(self,request,address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request);return
            try:super().process_request(request,address)
            except BaseException:self.slots.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
    server=Server((host,port),Handler)
    print(f'Testnet wallet gateway: http://{host}:{server.server_port}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=Path(__file__).resolve().parents[1]/'build/qcash')
    parser.add_argument('--home',type=Path,required=True);parser.add_argument('--peer',required=True)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=29702)
    parser.add_argument('--token-file',type=Path,help='Private shared gateway token; required for hosted deployments')
    parser.add_argument('--release-commit',help='Deployed source revision for read-only health checks')
    args=parser.parse_args()
    token=args.token_file.read_text().strip() if args.token_file else None
    if args.token_file and (not token or len(token)<32):parser.error('gateway token must contain at least 32 characters')
    serve(args.binary.resolve(),args.home.resolve(),args.peer,args.host,args.port,token,args.release_commit)
