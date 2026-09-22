"""Offline tests: disposable keys, real signatures, simulated providers and settlement."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
from algosdk import account, encoding, transaction
from fastapi.testclient import TestClient
from nacl.signing import VerifyKey

from tn_agent.config import Config, NETWORKS, atoms
from tn_agent.engine import NewsAgent, LocalWallet, PendingOrder, ServiceUnavailable, available_symbols
from tn_agent.journal import Journal
from tn_agent.chain import Chain, validate_unsigned, validate_signed, payment_header
from tn_agent.secrets import parse_secret, save_secret, load_secret
from tn_agent.pera import Pera
from server.main import create_app
from server.news.config import Settings
from server.news.payments import decode_payment


@contextmanager
def system(tmp_path, sponsored=True, provider_status=200, lose_response=None, settlement_unknown=False):
    key, payer=account.generate_account(); _, merchant=account.generate_account(); _, sponsor=account.generate_account()
    cfg=Config(api_url='https://news.example', network='testnet', mode='automatic', wallet_address=payer,
               merchant_address=merchant, configured=True)
    settings=Settings(demo=False,payments=True,pay_to=merchant,public_url=cfg.api_url,
                      network_name='testnet',db_path=str(tmp_path/'server.db'),request_limit=10000)
    calls=[]; receipts=[]
    def upstream(req):
        calls.append(req.url.path)
        if req.url.path=='/supported':
            return httpx.Response(200,json={'kinds':[{'x402Version':2,'scheme':'exact','network':settings.network,
                'extra':{'feePayer':sponsor} if sponsored else {}}]})
        if req.url.host=='api.gdeltproject.org':
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            return httpx.Response(provider_status,headers={'Retry-After':'120'},json={'articles':[
                {'title':'Ethereum token price could rise tomorrow','url':'https://a.example/rumour','domain':'a.example','seendate':stamp},
                {'title':'Ethereum blockchain critical vulnerability fixed in security upgrade','url':'https://b.example/security','domain':'b.example','seendate':stamp},
                {'title':'Gardening tools for the weekend','url':'https://a.example/garden','domain':'a.example','seendate':stamp},
            ]})
        if req.url.path in {'/verify','/settle'}:
            body=json.loads(req.content);p=body['paymentPayload'];ix=p['payload']['paymentIndex'];group=p['payload']['paymentGroup']
            signed=encoding.msgpack_decode(group[ix]);raw=base64.b64decode(encoding.msgpack_encode(signed.transaction))
            VerifyKey(encoding.decode_address(payer)).verify(b'TX'+raw,base64.b64decode(signed.signature))
            if req.url.path=='/verify':return httpx.Response(200,json={'isValid':True,'payer':payer})
            if settlement_unknown:raise httpx.ReadTimeout('simulated uncertain settlement',request=req)
            receipt={'success':True,'network':settings.network,'transaction':signed.transaction.get_txid(),'payer':payer}
            receipts.append(receipt);return httpx.Response(200,json=receipt)
        raise AssertionError(str(req.url))
    app=create_app(settings,httpx.MockTransport(upstream))
    with TestClient(app) as api:
        lost=False
        def transport(req):
            nonlocal lost
            if req.url.host=='news.example':
                r=api.request(req.method,req.url.path,headers=dict(req.headers),content=req.content)
                if lose_response and req.url.path==lose_response and r.status_code==200 and not lost:
                    lost=True;raise httpx.ReadTimeout('response lost after settlement',request=req)
                return httpx.Response(r.status_code,headers=r.headers,content=r.content)
            if req.url.path=='/v2/transactions/params':
                return httpx.Response(200,json={'min-fee':1000,'fee':0,'last-round':100,'genesis-hash':NETWORKS['testnet'][0].split(':',1)[1],'genesis-id':'testnet-v1.0'})
            if req.url.path.startswith('/v2/accounts/'):
                return httpx.Response(200,json={'amount':1000000,'min-balance':200000,'assets':[{'asset-id':10458941,'amount':10000000,'is-frozen':False}]})
            raise AssertionError(str(req.url))
        with httpx.Client(transport=httpx.MockTransport(transport)) as http:
            agent=NewsAgent(cfg,http,Journal(tmp_path/'orders.db'))
            yield agent,LocalWallet(key,agent.chain),api,calls,receipts


@pytest.mark.parametrize('sponsored',[False,True])
def test_two_real_signatures_select_best_and_replay_without_third_charge(tmp_path,sponsored):
    with system(tmp_path,sponsored) as (agent,wallet,api,calls,receipts):
        order=agent.new_order('ETH');result=agent.run(order,wallet)
        assert result['total_charged_usdc']=='0.2'
        assert result['best_article']['url']=='https://b.example/security'
        assert result['best_article']['date_basis']=='first_seen'
        assert result['best_article']['published_at'] is None
        assert len(receipts)==2 and receipts[0]['transaction']!=receipts[1]['transaction']
        assert calls.count('/api/v2/doc/doc')==1
        assert result['source_receipt']==receipts[0]
        assert order['stages']['feed']['response']['best_article'] is None
        assert [s['amount_usdc'] for s in result['payments']]==['0.1','0.1']
        resumed=NewsAgent(agent.cfg,agent.http,Journal(tmp_path/'orders.db'))
        assert resumed.run(resumed.journal.get(order['id']),wallet)==result
        assert len(receipts)==2 and resumed.journal.pending()==[]
        # A direct HTTP retry also returns the original settled result, not another payment.
        stage=order['stages']['feed']
        r=api.get('/api/v1/market-signal/ETH',headers={'PAYMENT-SIGNATURE':stage['signature']})
        assert r.status_code==200 and r.json()['billing']['replayed']
        assert len(receipts)==2


@pytest.mark.parametrize('path',['/api/v1/market-signal/ETH','/api/v1/news/ETH/best'])
def test_resume_after_response_lost_never_recharges(tmp_path,path):
    with system(tmp_path,lose_response=path) as (agent,wallet,api,calls,receipts):
        order=agent.new_order('ETH')
        with pytest.raises(PendingOrder):agent.run(order,wallet)
        old=deepcopy(order)
        result=agent.run(agent.journal.get(order['id']),wallet)
        assert result['best_article'] and result['total_charged_usdc']=='0.2'
        assert len(receipts)==2
        for name,stage in old['stages'].items():
            if stage.get('signature'):assert agent.journal.get(order['id'])['stages'][name]['signature']==stage['signature']


def test_uncertain_settlement_is_held_not_resubmitted(tmp_path):
    with system(tmp_path,settlement_unknown=True) as (agent,wallet,api,calls,receipts):
        order=agent.new_order('ETH')
        with pytest.raises(PendingOrder):agent.run(order,wallet)
        with pytest.raises(PendingOrder):agent.run(agent.journal.get(order['id']),wallet)
        assert calls.count('/settle')==1 and not receipts
        assert len(agent.journal.pending())==1


def test_gdelt_429_stops_before_any_signature_or_payment(tmp_path):
    with system(tmp_path,provider_status=429) as (agent,wallet,api,calls,receipts):
        with pytest.raises(ServiceUnavailable,match='You have not been charged'):agent.new_order('ETH')
        with pytest.raises(ServiceUnavailable):agent.new_order('ETH')
        assert not receipts and '/verify' not in calls and '/settle' not in calls
        assert calls.count('/api/v2/doc/doc')==1 and not agent.journal.pending()


def test_second_purchase_requires_matching_first_receipt(tmp_path):
    with system(tmp_path) as (agent,wallet,api,calls,receipts):
        order=agent.new_order('ETH')
        definition=order['contract']['stages'][1]
        q=agent.chain.payment(definition['challenge'],agent.cfg.wallet_address,'test')
        signed=payment_header(q,wallet.sign(q))
        r=api.get('/api/v1/news/ETH/best',headers={'PAYMENT-SIGNATURE':signed})
        assert r.status_code==409 and not receipts
        result=agent.run(order,wallet)
        wrong=api.get('/api/v1/news/BTC/best',headers={
            'PAYMENT-SIGNATURE':order['stages']['selection']['signature'],
            'X-NEWS-FEED-PAYMENT':order['stages']['feed']['signature']})
        assert wrong.status_code==409 and len(receipts)==2


def test_price_receiver_and_network_are_checked_before_signing(tmp_path):
    with system(tmp_path) as (agent,wallet,api,calls,receipts):
        info=agent.info();quote,total=agent.quote('ETH',info)
        q=quote['stages'][0]['challenge'];url=q['resource']['url']
        for field,value in [('network',NETWORKS['mainnet'][0]),('asset','1'),('payTo',wallet.address),('amount','-1')]:
            bad=deepcopy(q);bad['accepts'][0][field]=value
            with pytest.raises(ValueError):agent.check_challenge(bad,url)
        agent.cfg.max_per_query_usdc='0.1'
        with pytest.raises(ValueError,match='per-query limit'):agent.info()
        assert not receipts


def test_private_key_validation_and_local_permissions(tmp_path):
    key,address=account.generate_account()
    assert parse_secret(key)==(key,address)
    with pytest.raises(ValueError):parse_secret(base64.b64encode(bytes(64)).decode())
    target=tmp_path/'private'/'wallet.json';save_secret(target,key)
    assert load_secret(target)==(key,address)
    assert target.stat().st_mode & 0o077==0
    target.chmod(0o644)
    with pytest.raises(ValueError,match='permissions'):load_secret(target)


def test_daily_budget_is_persistent_and_counts_unresolved_orders(tmp_path):
    with system(tmp_path) as (agent,wallet,api,calls,receipts):
        agent.cfg.max_per_day_usdc='0.40'
        first=agent.new_order('ETH');agent.run(first,wallet)
        second=agent.new_order('ETH')
        # Unresolved earlier-day reservations still count alongside today's confirmed spend.
        with agent.journal.db() as db:db.execute("UPDATE orders SET day='2000-01-01' WHERE id=?",(second['id'],))
        agent.journal=Journal(tmp_path/'orders.db')
        with pytest.raises(ValueError,match='daily budget'):agent.new_order('ETH')
        agent.run(second,wallet)
        with pytest.raises(ValueError,match='daily budget'):agent.new_order('ETH')
        assert len(receipts)==4


def test_refusing_signature_cancels_unspent_reservation(tmp_path):
    class Refuse:
        def sign(self,q):raise ValueError('cancelled')
    with system(tmp_path) as (agent,wallet,api,calls,receipts):
        agent.cfg.max_per_day_usdc='0.2';order=agent.new_order('ETH')
        with pytest.raises(ValueError,match='cancelled'):agent.run(order,Refuse())
        assert not agent.journal.pending() and not receipts
        assert agent.new_order('ETH')


def test_group_and_signature_tampering_are_rejected(tmp_path):
    with system(tmp_path) as (agent,wallet,api,calls,receipts):
        order=agent.new_order('ETH');q=agent.chain.payment(order['contract']['stages'][0]['challenge'],wallet.address,'test')
        validate_unsigned(q)
        bad=deepcopy(q);tx=encoding.msgpack_decode(bad['unsigned_transactions'][1]);tx.rekey_to=agent.cfg.merchant_address
        bad['unsigned_transactions'][1]=encoding.msgpack_encode(tx);bad['transaction_ids'][1]=tx.get_txid()
        with pytest.raises(ValueError):validate_unsigned(bad)
        other,_=account.generate_account()
        signed=encoding.msgpack_encode(encoding.msgpack_decode(q['unsigned_transactions'][1]).sign(other))
        with pytest.raises(ValueError):validate_signed(q,signed)


@pytest.mark.parametrize('sponsored',[False,True])
def test_python_javascript_signature_interoperability(tmp_path,sponsored):
    with system(tmp_path,sponsored) as (agent,wallet,api,calls,receipts):
        order=agent.new_order('ETH');definition=order['contract']['stages'][0]
        q=agent.chain.payment(definition['challenge'],wallet.address,'test')
        q['authorized']={'url':definition['url'],'pay_to':agent.cfg.merchant_address,'amount':'100000'}
        completed=subprocess.run(['node','tests/sign_quote.mjs'],input=json.dumps({'quote':q,'key':wallet.key}),text=True,capture_output=True,check=True)
        validate_signed(q,completed.stdout.strip())


def test_automatic_wallet_preparation_is_zero_self_transfer_and_js_valid(tmp_path):
    key,address=account.generate_account()
    def mock(req):
        if req.url.path.endswith('/params'):return httpx.Response(200,json={'fee':0,'min-fee':1000,'last-round':10,'genesis-hash':NETWORKS['testnet'][0].split(':',1)[1]})
        return httpx.Response(200,json={'amount':1000000,'min-balance':100000,'assets':[]})
    with httpx.Client(transport=httpx.MockTransport(mock)) as http:
        chain=Chain(http,'testnet');q=chain.preparation(address);validate_unsigned(q)
        tx=encoding.msgpack_decode(q['unsigned_transactions'][0]);assert tx.amount==0 and tx.sender==tx.receiver==address
        done=subprocess.run(['node','tests/sign_quote.mjs'],input=json.dumps({'quote':q,'key':key}),text=True,capture_output=True,check=True)
        validate_signed(q,done.stdout.strip())


def test_local_pera_bridge_rejects_cross_origin_requests_and_duplicate_results():
    with Pera('testnet',open_browser=False) as bridge, httpx.Client(timeout=5) as http:
        assert http.get(bridge.origin+'/session').status_code==403
        h={'X-Agent-Token':bridge.token}
        assert http.get(bridge.origin+'/session',headers=h).status_code==200
        assert http.get(bridge.origin+'/session',headers=dict(h,Origin='https://evil.example')).status_code==403
        assert http.get(bridge.origin+'/',headers={'Host':'evil.example'}).status_code==403
        bridge.task={'id':'test','action':'connect'}
        assert http.post(bridge.origin+'/result',headers=h,json={'id':'test','value':'a'}).status_code==403
        h['Origin']=bridge.origin
        assert http.post(bridge.origin+'/result',headers=h,json={'id':'test','value':'a'}).status_code==200
        assert http.post(bridge.origin+'/result',headers=h,json={'id':'test','value':'a'}).status_code==409


def test_cli_catalog_and_incomplete_config_never_purchase(tmp_path):
    import agent
    assert len(available_symbols())==49
    assert agent.main(['--symbols','--json'])==0
    assert agent.main(['--describe'])==0
    assert agent.main(['--config',str(tmp_path/'missing'),'--symbol','ETH','--json','--pay'])==2


def test_required_secret_cannot_be_blank(monkeypatch,capsys):
    from tn_agent.setup import required
    values=iter(['',' ','valid-test-value'])
    monkeypatch.setattr('getpass.getpass',lambda _:next(values))
    assert required('test',True)=='valid-test-value'
    assert capsys.readouterr().out.count('It cannot be left blank')==2


def test_installer_writes_working_config_only_after_required_key_and_balance(tmp_path,monkeypatch):
    from tn_agent import setup
    key,payer=account.generate_account()
    class FakeChain:
        def funds(self,address):return {'ready':True,'frozen':False,'usdc_atoms':500000,'algo_available':200000}
    class FakeAgent:
        def __init__(self,*args):self.chain=FakeChain()
        def info(self):return {'source_price_usdc':'0.10','selection_price_usdc':'0.10'}
    monkeypatch.setattr(setup,'ROOT',tmp_path);monkeypatch.setattr(setup,'NewsAgent',FakeAgent)
    responses=iter(['2','1.00','ACCEPT']);keys=iter(['','invalid',key])
    monkeypatch.setattr('builtins.input',lambda _:next(responses));monkeypatch.setattr('getpass.getpass',lambda _:next(keys))
    target=tmp_path/'config.txt';setup.run(target)
    configured=Config.load(target)
    assert configured.configured and configured.wallet_address==payer and configured.mode=='automatic'
    assert load_secret(tmp_path/'.private/wallet.json')==(key,payer)
    assert key not in target.read_text()


def test_catalog_is_identical_between_client_and_server():
    from server.news.catalog import ASSETS
    assert set(ASSETS)=={a['symbol'] for a in available_symbols()}
