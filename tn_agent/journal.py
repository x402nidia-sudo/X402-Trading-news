"""Durable reservations: concurrent agents cannot silently exceed the daily budget."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path


class Journal:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, day TEXT, wallet TEXT, network TEXT, reserved INTEGER, spent INTEGER, state TEXT, body TEXT, updated REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS ledger(txid TEXT, network TEXT, wallet TEXT, day TEXT, amount INTEGER, PRIMARY KEY(txid,network))')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def create(self,cfg,symbol,total,limit,contract):
        day=datetime.now(timezone.utc).date().isoformat();ident=uuid.uuid4().hex
        body={'id':ident,'symbol':symbol,'api_url':cfg.api_url,'network':cfg.network,'wallet':cfg.wallet_address,
              'contract':contract,'stages':{},'result':None}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Reservations from earlier days remain held until their outcome is known.
            used=db.execute('SELECT COALESCE(SUM(amount),0) FROM ledger WHERE day=? AND wallet=? AND network=?',(day,cfg.wallet_address,cfg.network)).fetchone()[0]
            used+=db.execute('SELECT COALESCE(SUM(reserved),0) FROM orders WHERE state!=\'done\' AND wallet=? AND network=?',(cfg.wallet_address,cfg.network)).fetchone()[0]
            if used+total>limit:raise ValueError('The daily budget is spent or reserved by a pending query. Nothing has been signed.')
            db.execute('INSERT INTO orders VALUES(?,?,?,?,?,0,\'pending\',?,?)',(ident,day,cfg.wallet_address,cfg.network,total,json.dumps(body),time.time()))
        return body

    def get(self,ident):
        with self.db() as db:r=db.execute('SELECT * FROM orders WHERE id=?',(ident,)).fetchone()
        if not r:raise ValueError('Saved query not found.')
        return json.loads(r['body'])

    def save(self,body,done=False):
        spent=sum(s.get('spent',0) for s in body['stages'].values())
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r=db.execute('SELECT reserved,spent FROM orders WHERE id=?',(body['id'],)).fetchone()
            for stage in body['stages'].values():
                if stage.get('spent'):
                    receipt=stage['response']['billing']['receipt']
                    db.execute('INSERT OR IGNORE INTO ledger VALUES(?,?,?,?,?)',(receipt['transaction'],body['network'],body['wallet'],datetime.now(timezone.utc).date().isoformat(),stage['spent']))
            reserved=0 if done else max(0,r['reserved']-(spent-r['spent']))
            db.execute('UPDATE orders SET body=?,spent=?,reserved=?,state=?,updated=? WHERE id=?',
                (json.dumps(body),spent,reserved,'done' if done else 'pending',time.time(),body['id']))

    def pending(self):
        with self.db() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM orders WHERE state!=\'done\' ORDER BY updated')]

    @contextmanager
    def exclusive(self):
        # One client process per config avoids competing Pera windows and same-order retries.
        path=self.path.with_suffix('.lock')
        handle=path.open('a+b');handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:
            if __import__('os').name=='nt':
                import msvcrt
                try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                except OSError:raise ValueError('Another process is using this agent. Wait for it to finish.') from None
            else:
                import fcntl
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError:raise ValueError('Another process is using this agent. Wait for it to finish.') from None
            yield
        finally:handle.close()
