"""Temporary local approval window for the Python agent, bound to loopback only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
import secrets
import threading
import time
import webbrowser
import sys
from .config import ROOT


class Pera:
    def __init__(self,network,expected_address='',open_browser=True):
        self.network=network;self.expected=expected_address;self.task=None;self.response=None
        self.guard=threading.Condition();self.token=secrets.token_urlsafe(32)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,status,body,kind='application/json'):
                data=json.dumps(body).encode() if kind=='application/json' else body
                self.send_response(status);self.send_header('Content-Type',kind)
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https://*.perawallet.app https://perawallet.app https://perawallet.s3-eu-west-3.amazonaws.com https://s3.amazonaws.com/wc.perawallet.app/; media-src https://s3.amazonaws.com/wc.perawallet.app/; connect-src 'self' https://perawallet.app https://*.perawallet.app wss://*.perawallet.app https://*.walletconnect.org wss://*.walletconnect.org https://*.walletconnect.com wss://*.walletconnect.com https://s3.amazonaws.com/wc.perawallet.app/; frame-src https://perawallet.app https://*.perawallet.app; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def allowed(self,api=False):
                if self.headers.get('Host')!=owner.host:return False
                origin=self.headers.get('Origin')
                if origin and origin!=owner.origin:return False
                if api and not secrets.compare_digest(self.headers.get('X-Agent-Token',''),owner.token):return False
                return True
            def do_GET(self):
                if not self.allowed(self.path=='/session'):return self.respond(403,{'error':'Local access required'})
                if self.path=='/session':
                    with owner.guard:return self.respond(200,{'network':owner.network,'expected_address':owner.expected,'task':owner.task})
                assets={'/':('index.html','text/html; charset=utf-8'),'/wallet.js':('wallet.js','text/javascript'),'/style.css':('style.css','text/css')}
                if self.path not in assets:return self.respond(404,{})
                name,mime=assets[self.path];return self.respond(200,(ROOT/'wallet'/name).read_bytes(),mime)
            def do_POST(self):
                if self.path!='/result' or not self.allowed(True) or self.headers.get('Origin')!=owner.origin:return self.respond(403,{})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=65536:raise ValueError()
                    data=json.loads(self.rfile.read(size))
                    with owner.guard:
                        if not owner.task or data.get('id')!=owner.task['id'] or owner.response is not None:return self.respond(409,{})
                        owner.response=data;owner.guard.notify_all()
                    self.respond(200,{'ok':True})
                except (ValueError,TypeError):self.respond(400,{})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.host='127.0.0.1:'+str(self.server.server_port);self.origin='http://'+self.host
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        url=self.origin+'/#'+self.token
        if open_browser:
            print('Opening Pera in your browser. If it does not appear, open this local address:\n'+url,file=sys.stderr)
            if not webbrowser.open(url):print('Open the link above on this computer.',file=sys.stderr)

    def ask(self,action,data=None,timeout=300):
        with self.guard:
            self.response=None;self.task={'id':secrets.token_hex(16),'action':action,'data':data}
            deadline=time.monotonic()+timeout
            while self.response is None:
                left=deadline-time.monotonic()
                if left<=0:self.task=None;raise ValueError('Pera did not respond. This transaction has not been sent.')
                self.guard.wait(min(left,1))
            result=self.response;self.task=None
        if result.get('error'):raise ValueError('The operation was cancelled or not signed in Pera. This transaction has not been sent.')
        return result.get('value')
    def connect(self):
        from algosdk.encoding import is_valid_address
        address=self.ask('connect')
        if not isinstance(address,str) or not is_valid_address(address) or (self.expected and address!=self.expected):
            raise ValueError('Pera connected a different wallet. Run the installer to change the configured wallet.')
        self.expected=address;return address
    def sign(self,quote):return self.ask('sign',quote)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
