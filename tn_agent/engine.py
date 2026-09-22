"""One order = one source purchase and one selection purchase, with durable recovery."""
from copy import deepcopy
import base64
import json
import re
import time
from algosdk import account
from .config import ROOT,NETWORKS,atoms,amount
from .chain import Chain,payment_header,validate_unsigned
from .journal import Journal


class PendingOrder(RuntimeError):
    def __init__(self,order_id,message):super().__init__(message);self.order_id=order_id


class ServiceUnavailable(ValueError):pass


def available_symbols():return json.loads((ROOT/'assets.json').read_text())


class LocalWallet:
    def __init__(self,key,chain):self.key=key;self.chain=chain;self.address=account.address_from_private_key(key)
    def sign(self,quote):return self.chain.sign_local(quote,self.key)


class NewsAgent:
    def __init__(self,cfg,http,journal=None):
        self.cfg=cfg;self.http=http;self.chain=Chain(http,cfg.network)
        self.journal=journal or Journal(ROOT/'.private/orders.sqlite3')

    def info(self):
        r=self.http.get(self.cfg.api_url+'/api/v1/agent-info')
        if r.status_code==404:raise ServiceUnavailable('The public API is still running the old version. The owner must deploy Trading News Agent 4 before selling queries. You have not been charged.')
        r.raise_for_status();d=r.json()
        net,asset=NETWORKS[self.cfg.network]
        if d.get('agent_protocol')!=1 or d.get('network_caip')!=net or d.get('asset_id')!=asset or d.get('pay_to')!=self.cfg.merchant_address:
            raise ValueError('The API does not match the configured network or recipient. Nothing has been signed.')
        if not d.get('payments_enabled'):raise ServiceUnavailable('The service owner has not activated the service yet.')
        if d.get('source_path')!='/api/v1/market-signal/{symbol}' or d.get('selection_path')!='/api/v1/news/{symbol}/best':
            raise ValueError('Unexpected service routes.')
        symbols=[a['symbol'] for a in d.get('assets',[])]
        if not symbols or len(symbols)!=len(set(symbols)) or any(not re.fullmatch('[A-Z0-9]{1,12}',s) for s in symbols):
            raise ValueError('Invalid symbol catalog.')
        total=atoms(d['source_price_usdc'])+atoms(d['selection_price_usdc'])
        if total>atoms(self.cfg.max_per_query_usdc):raise ValueError('The combined price exceeds your per-query limit.')
        return d

    def check_challenge(self,body,url):
        net,asset=NETWORKS[self.cfg.network]
        if body.get('x402Version')!=2 or body.get('resource',{}).get('url')!=url:raise ValueError('The payment challenge is for a different resource.')
        options=body.get('accepts',[])
        if len(options)!=1:raise ValueError('Ambiguous payment options.')
        r=options[0]
        if r.get('scheme')!='exact' or r.get('network')!=net or r.get('asset')!=asset or r.get('payTo')!=self.cfg.merchant_address:
            raise ValueError('Unexpected payment recipient, asset or network.')
        value=r.get('amount','')
        if not isinstance(value,str) or not value.isdigit() or int(value)<=0:raise ValueError('Invalid amount.')
        return int(value)

    def quote(self,symbol,info=None):
        info=info or self.info()
        if symbol not in {a['symbol'] for a in info['assets']}:raise ValueError('Symbol not available.')
        r=self.http.get(self.cfg.api_url+f'/api/v1/quote/{symbol}')
        if r.status_code==503:raise ServiceUnavailable('There is not enough up-to-date news available. You have not been charged. Please try again later.')
        r.raise_for_status();q=r.json()
        if q.get('symbol')!=symbol or q.get('expires_at',0)<=time.time() or not re.fullmatch('[A-Za-z0-9_-]{20,80}',q.get('quote_id','')):
            raise ValueError('The prepared query is invalid or has expired.')
        expected=[('feed','/api/v1/market-signal/'+symbol,atoms(info['source_price_usdc'])),
                  ('selection','/api/v1/news/'+symbol+'/best',atoms(info['selection_price_usdc']))]
        if len(q.get('stages',[]))!=2:raise ValueError('The service must advertise exactly two charges.')
        total=0
        for stage,(name,path,price) in zip(q['stages'],expected):
            url=self.cfg.api_url+path
            if stage.get('id')!=name or stage.get('url')!=url:raise ValueError('Unexpected purchase route.')
            value=self.check_challenge(stage['challenge'],url)
            if value!=price:raise ValueError('The price changed while preparing the purchase.')
            total+=value
        if total>atoms(self.cfg.max_per_query_usdc):raise ValueError('The price exceeds your budget.')
        return q,total

    def new_order(self,symbol):
        q,total=self.quote(symbol)
        funds=self.chain.funds(self.cfg.wallet_address)
        if not funds['ready']:raise ValueError('The wallet is not ready yet. Run the installer again.')
        if funds['frozen'] or funds['usdc_atoms']<total:raise ValueError(f'You need at least {amount(total)} USDC available in this wallet.')
        direct=sum(not s['challenge']['accepts'][0].get('extra',{}).get('feePayer') for s in q['stages'])
        if funds['algo_available']<direct*self.chain.params().fee:raise ValueError('There is not enough available ALGO for these transaction fees.')
        return self.journal.create(self.cfg,symbol,total,atoms(self.cfg.max_per_day_usdc),q)

    def _response(self,response,stage):
        try:data=response.json()
        except ValueError:raise ValueError('The server did not return a verifiable result.') from None
        billing=data.get('billing',{})
        if response.status_code==503 and billing.get('charged') is False and billing.get('reason')=='sources_unavailable':return data,0
        response.raise_for_status()
        if billing.get('charged') is True:
            receipt=billing.get('receipt',{});header=response.headers.get('payment-response','')
            try:from_header=json.loads(base64.b64decode(header,validate=True))
            except Exception:raise ValueError('No verifiable receipt was received.') from None
            if receipt!=from_header or receipt.get('success') is not True or receipt.get('network')!=NETWORKS[self.cfg.network][0] or receipt.get('transaction') not in stage['quote']['transaction_ids']:
                raise ValueError('The receipt does not match the signed transaction group.')
            return data,int(stage['quote']['challenge']['accepts'][0]['amount'])
        if billing.get('charged') is not False:raise ValueError('The payment status has not been confirmed.')
        if data.get('best_article') and billing.get('reason')!='stale_fallback':raise ValueError('The news result has no confirmed receipt.')
        return data,0

    def run(self,order,wallet):
        if order['api_url']!=self.cfg.api_url or order['wallet']!=self.cfg.wallet_address or order['network']!=self.cfg.network:
            raise ValueError('The saved query belongs to a different configuration. Nothing has been signed.')
        if order.get('result') is not None:return order['result']
        try:
            for definition in order['contract']['stages']:
                name=definition['id'];stage=order['stages'].setdefault(name,{})
                if 'response' in stage:
                    if not stage.get('spent'):break
                    continue
                if not stage.get('signature'):
                    # The price is checked again locally before every signature.
                    self.check_challenge(definition['challenge'],definition['url'])
                    if name=='feed' and order['contract']['expires_at']<=time.time():
                        raise ValueError('The prepared query expired before signing. Start a new query.')
                    quote=self.chain.payment(definition['challenge'],self.cfg.wallet_address,definition['label'])
                    quote['authorized']={'url':definition['url'],'pay_to':self.cfg.merchant_address,'amount':definition['challenge']['accepts'][0]['amount']}
                    validate_unsigned(quote)
                    signed=wallet.sign(quote)
                    stage.update({'quote':quote,'signature':payment_header(quote,signed)})
                    # Persist BEFORE sending: an interruption retries the same transaction.
                    self.journal.save(order)
                headers={'PAYMENT-SIGNATURE':stage['signature'],'X-NEWS-QUOTE':order['contract']['quote_id']}
                if name=='selection':headers['X-NEWS-FEED-PAYMENT']=order['stages']['feed']['signature']
                response=self.http.get(definition['url'],headers=headers,timeout=70)
                data,spent=self._response(response,stage)
                if data.get('symbol') and data['symbol']!=order['symbol']:raise ValueError('The server returned a different symbol.')
                stage.update({'response':data,'spent':spent});self.journal.save(order)
                if not spent:break
            stages=order['stages']
            selected=(stages.get('selection') or stages.get('feed') or {}).get('response',{})
            result=deepcopy(selected)
            result.update({'order_id':order['id'],'total_charged_usdc':amount(sum(s.get('spent',0) for s in stages.values())),
                'payments':[{'step':name,'amount_usdc':amount(s.get('spent',0)),'receipt':s.get('response',{}).get('billing',{}).get('receipt')} for name,s in stages.items()]})
            order['result']=result;self.journal.save(order,True);return result
        except Exception as e:
            if not any(s.get('signature') for s in order['stages'].values()):
                order['result']={'status':'cancelled','total_charged_usdc':'0','order_id':order['id']}
                self.journal.save(order,True)
                raise
            self.journal.save(order)
            raise PendingOrder(order['id'],'Query saved. Recover the same purchase with --resume '+order['id']+'. The first charge will not be repeated. Cause: '+str(e)) from None
