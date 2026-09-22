import test from 'node:test';
import assert from 'node:assert/strict';
import algosdk from 'algosdk';
import {validate,b64,findSigned} from '../wallet/validation.js';
function quote(){
 const payer=algosdk.generateAccount(),receiver=algosdk.generateAccount();
 const p={fee:1000n,flatFee:true,firstValid:100n,lastValid:200n,genesisHash:Uint8Array.from(Buffer.from('SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=','base64'))};
 const t=algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({sender:payer.addr,receiver:receiver.addr,amount:100000n,assetIndex:10458941n,suggestedParams:p});
 const url='https://news.example/api/v1/market-signal/ETH';
 return {key:payer.sk,q:{kind:'payment',payer:payer.addr.toString(),network:'testnet',unsigned_transactions:[b64(algosdk.encodeUnsignedTransaction(t))],transaction_ids:[t.txID()],sign_indexes:[0],payment_index:0,expires_at:Date.now()/1000+180,challenge:{x402Version:2,resource:{url},accepts:[{scheme:'exact',network:'algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=',asset:'10458941',amount:'100000',payTo:receiver.addr.toString(),extra:{}}]},authorized:{url,pay_to:receiver.addr.toString(),amount:'100000'}}};
}
test('Pera result is the exact authorized payer transaction',()=>{const {q,key}=quote();const txs=validate(q,q.payer);assert.ok(findSigned(q,[txs[0].signTxn(key)],q.payer));});
for(const what of ['amount','asset','payTo','network','resource','budget','expired','payer','indexes']){
 test('rejects changed '+what,()=>{const {q}=quote();const r=q.challenge.accepts[0];if(what==='amount')r.amount='200000';if(what==='asset')r.asset='1';if(what==='payTo')r.payTo=algosdk.generateAccount().addr.toString();if(what==='network')r.network='other';if(what==='resource')q.challenge.resource.url='https://evil.example/api/v1/market-signal/ETH';if(what==='budget')q.authorized.amount='200000';if(what==='expired')q.expires_at=0;if(what==='payer')q.payer=algosdk.generateAccount().addr.toString();if(what==='indexes')q.sign_indexes=[0,1];assert.throws(()=>validate(q,q.payer));});
}
test('rekeying is refused even with rewritten IDs',()=>{const {q}=quote();const t=algosdk.decodeUnsignedTransaction(Buffer.from(q.unsigned_transactions[0],'base64'));t.rekeyTo=algosdk.generateAccount().addr;q.unsigned_transactions[0]=b64(algosdk.encodeUnsignedTransaction(t));q.transaction_ids[0]=t.txID();assert.throws(()=>validate(q,q.payer));});
