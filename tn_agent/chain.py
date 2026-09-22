"""Build and validate exact Algorand payments. No private key is sent to a server."""
import base64
import json
import time
from types import SimpleNamespace
from algosdk import encoding, transaction
from nacl.signing import VerifyKey
from x402.mechanisms.avm.exact import ExactAvmScheme
from x402.schemas import PaymentRequirements
from .config import NETWORKS

ALGOD={'mainnet':'https://mainnet-api.algonode.cloud','testnet':'https://testnet-api.algonode.cloud'}


def encoded(obj):return base64.b64encode(json.dumps(obj,separators=(',',':')).encode()).decode()


class UnsignedSigner:
    def __init__(self,address):self.address=address;self.indexes=[]
    def sign_transactions(self,txns,indexes):self.indexes=indexes;return [None]*len(txns)


class PreparedScheme(ExactAvmScheme):
    def __init__(self,signer,params):super().__init__(signer);self.node=SimpleNamespace(suggested_params=lambda:params)
    def _get_client(self,network):return self.node


class Chain:
    def __init__(self,http,network):self.http=http;self.network=network;self.url=ALGOD[network]
    def params(self):
        r=self.http.get(self.url+'/v2/transactions/params');r.raise_for_status();d=r.json()
        minimum=int(d.get('min-fee',1000));first=int(d['last-round'])
        if d['genesis-hash']!=NETWORKS[self.network][0].split(':',1)[1] or not 1000<=minimum<=10000 or int(d.get('fee',0))>0 or first<=0:
            raise ValueError('A bounded transaction fee could not be prepared on the selected network.')
        return transaction.SuggestedParams(fee=minimum,first=first,last=first+100,
            gh=d['genesis-hash'],gen=d.get('genesis-id'),flat_fee=True,min_fee=minimum)
    def account(self,address):
        if not encoding.is_valid_address(address):raise ValueError('Invalid wallet address.')
        r=self.http.get(self.url+'/v2/accounts/'+address)
        if r.status_code==404:return {'amount':0,'min-balance':100000,'assets':[]}
        r.raise_for_status();data=r.json()
        if data.get('auth-addr') and data['auth-addr']!=address:
            raise ValueError('This wallet uses a different authorization key. Use a standard wallet with this agent.')
        return data
    def funds(self,address):
        info=self.account(address);asset=int(NETWORKS[self.network][1])
        holding=next((a for a in info.get('assets',[]) if a.get('asset-id')==asset),None)
        return {'ready':holding is not None,'usdc_atoms':int((holding or {}).get('amount',0)),
                'frozen':bool((holding or {}).get('is-frozen',False)),
                'algo_available':int(info.get('amount',0))-int(info.get('min-balance',100000))}
    def preparation(self,address):
        p=self.params();funds=self.funds(address)
        if funds['ready']:return None
        if funds['algo_available']<100000+p.fee:
            raise ValueError('Add ALGO to prepare your wallet. The balance must cover its reserve and fee; the installer will check again.')
        t=transaction.AssetTransferTxn(address,p,address,0,int(NETWORKS[self.network][1]))
        return {'kind':'prepare_usdc','network':self.network,'payer':address,'asset_id':NETWORKS[self.network][1],
                'unsigned_transactions':[encoding.msgpack_encode(t)],'transaction_ids':[t.get_txid()],
                'sign_indexes':[0],'payment_index':0,'network_fee_microalgo':t.fee,
                'reserve_microalgo':100000,'expires_at':int(time.time())+180}
    def payment(self,challenge,address,label):
        r=challenge['accepts'][0];signer=UnsignedSigner(address)
        payload=PreparedScheme(signer,self.params()).create_payment_payload(PaymentRequirements.model_validate(r))
        txs=[encoding.msgpack_decode(s) for s in payload['paymentGroup']]
        index=payload['paymentIndex']
        if signer.indexes!=[index]:raise ValueError('The group does not support a single buyer signature.')
        return {'kind':'payment','label':label,'challenge':challenge,'payer':address,'network':self.network,
                'unsigned_transactions':payload['paymentGroup'],'transaction_ids':[t.get_txid() for t in txs],
                'sign_indexes':[index],'payment_index':index,'network_fee_microalgo':sum(t.fee for t in txs),
                'customer_network_fee_microalgo':txs[index].fee,'expires_at':int(time.time())+180}
    def sign_local(self,quote,key):
        validate_unsigned(quote)
        i=quote['payment_index'];t=encoding.msgpack_decode(quote['unsigned_transactions'][i])
        return encoding.msgpack_encode(t.sign(key))
    def submit_preparation(self,quote,signed):
        validate_signed(quote,signed)
        raw=base64.b64decode(signed,validate=True)
        try:
            r=self.http.post(self.url+'/v2/transactions',content=raw,headers={'Content-Type':'application/x-binary'});r.raise_for_status()
        except Exception:
            # A transport error does not establish failure. Keep the original txid.
            pass
        txid=quote['transaction_ids'][0]
        for _ in range(15):
            r=self.http.get(self.url+'/v2/transactions/pending/'+txid)
            if r.status_code==200:
                d=r.json()
                if d.get('confirmed-round',0)>0:return txid
                if d.get('pool-error'):raise ValueError('The network rejected the wallet preparation transaction.')
            time.sleep(1)
        raise ValueError('Wallet preparation is pending. Do not sign again: rerun the installer to recover this same operation.')

    def preparation_expired(self,quote):
        """Retire only after its validity window; a zero self-transfer cannot debit USDC."""
        tx=encoding.msgpack_decode(quote['unsigned_transactions'][0])
        return self.params().first>tx.last_valid_round and not self.funds(quote['payer'])['ready']


def validate_unsigned(q):
    if q['expires_at']<=time.time():raise ValueError('The prepared authorization has expired; nothing has been signed.')
    ts=[encoding.msgpack_decode(x) for x in q['unsigned_transactions']]
    pi=q['payment_index'];t=ts[pi];network,asset=NETWORKS[q['network']]
    if q['sign_indexes']!=[pi]:raise ValueError('Unexpected signers.')
    for i,tx in enumerate(ts):
        if tx.rekey_to or tx.genesis_hash!=network.split(':',1)[1] or tx.get_txid()!=q['transaction_ids'][i] or not 0<tx.last_valid_round-tx.first_valid_round<=100 or tx.first_valid_round!=t.first_valid_round or tx.last_valid_round!=t.last_valid_round:
            raise ValueError('Unexpected transaction network or authorization.')
    if t.type!='axfer' or t.sender!=q['payer'] or t.index!=int(asset) or t.close_assets_to or t.revocation_target or t.fee>10000:
        raise ValueError('Unexpected transfer.')
    if q['kind']=='prepare_usdc':
        if len(ts)!=1 or t.receiver!=q['payer'] or t.amount!=0 or t.group:raise ValueError('Invalid wallet preparation.')
        return
    r=q['challenge']['accepts'][0];sponsor=r.get('extra',{}).get('feePayer')
    if t.receiver!=r['payTo'] or t.amount!=int(r['amount']) or r['asset']!=asset or r['network']!=network:
        raise ValueError('Unexpected amount, asset or recipient.')
    if sponsor:
        if len(ts)!=2 or pi!=1 or t.fee!=0:raise ValueError('Invalid sponsored transaction group.')
        f=ts[0]
        if f.type!='pay' or f.sender!=sponsor or f.receiver!=sponsor or f.amt!=0 or f.close_remainder_to or not 2000<=f.fee<=20000 or f.group!=t.group:
            raise ValueError('Invalid fee sponsorship.')
        claimed=t.group
        for x in ts:x.group=None
        if transaction.calculate_group_id(ts)!=claimed:raise ValueError('The transaction group was modified.')
    elif len(ts)!=1 or pi!=0 or t.group:raise ValueError('Unexpected transaction group.')


def validate_signed(q,signed):
    st=encoding.msgpack_decode(signed)
    i=q['payment_index']
    if not getattr(st,'signature',None) or getattr(st,'authorizing_address',None) or encoding.msgpack_encode(st.transaction)!=q['unsigned_transactions'][i]:
        raise ValueError('The signature does not belong to this operation.')
    try:
        VerifyKey(encoding.decode_address(q['payer'])).verify(b'TX'+base64.b64decode(q['unsigned_transactions'][i]),base64.b64decode(st.signature))
    except Exception:raise ValueError('Invalid signature.') from None
    return st


def payment_header(q,signed):
    validate_signed(q,signed)
    group=q['unsigned_transactions'][:];group[q['payment_index']]=signed
    return encoded({'x402Version':2,'accepted':q['challenge']['accepts'][0],
        'resource':q['challenge']['resource'],'extensions':q['challenge'].get('extensions',{}),
        'payload':{'paymentGroup':group,'paymentIndex':q['payment_index']}})
