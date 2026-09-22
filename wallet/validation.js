import algosdk from 'algosdk';
export const bytes = value => Uint8Array.from(atob(value), c=>c.charCodeAt(0));
export const b64 = value => btoa(Array.from(value,c=>String.fromCharCode(c)).join(''));
const networks={mainnet:['wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=','31566704'],testnet:['SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=','10458941']};
const same=(a,b)=>a?.length===b?.length&&Array.from(a||[]).every((v,i)=>v===b[i]);
function reject(){throw new Error('The received transaction does not match this purchase. Nothing has been signed.');}
export function validate(q,payer){
 if(!q||q.payer!==payer||q.expires_at*1000<=Date.now()||!networks[q.network])reject();
 const [genesis,asset]=networks[q.network],ts=q.unsigned_transactions.map(x=>algosdk.decodeUnsignedTransaction(bytes(x))),pi=q.payment_index;
 if(q.sign_indexes.length!==1||q.sign_indexes[0]!==pi)reject();
 for(const [i,t] of ts.entries())if(t.rekeyTo||b64(t.genesisHash)!==genesis||t.txID()!==q.transaction_ids[i]||t.lastValid<=t.firstValid||t.lastValid-t.firstValid>100n)reject();
 const t=ts[pi],a=t?.assetTransfer;
 if(t?.type!=='axfer'||t.sender.toString()!==payer||a.assetIndex!==BigInt(asset)||a.closeRemainderTo||a.assetSender||t.fee>10000n)reject();
 if(q.kind==='prepare_usdc'){
  if(ts.length!==1||pi!==0||a.receiver.toString()!==payer||a.amount!==0n||t.group||q.asset_id!==asset)reject();
  return ts;
 }
 const c=q.challenge,r=c?.accepts?.[0],url=new URL(c?.resource?.url||'invalid');
 if(c?.x402Version!==2||c.accepts.length!==1||!['https:','http:'].includes(url.protocol)||
    !/^\/api\/v1\/(market-signal\/[A-Z0-9]+|news\/[A-Z0-9]+\/best)$/.test(url.pathname)||url.search||url.hash||
    r.scheme!=='exact'||r.network!=='algorand:'+genesis||r.asset!==asset||a.receiver.toString()!==r.payTo||a.amount!==BigInt(r.amount))reject();
 if(!q.authorized||c.resource.url!==q.authorized.url||r.payTo!==q.authorized.pay_to||r.amount!==q.authorized.amount)reject();
 const sponsor=r.extra?.feePayer;
 if(sponsor){
  if(ts.length!==2||pi!==1||t.fee!==0n)reject();
  const f=ts[0],p=f.payment;
  if(f.type!=='pay'||f.sender.toString()!==sponsor||p.receiver.toString()!==sponsor||p.amount!==0n||p.closeRemainderTo||f.fee<2000n||f.fee>20000n||!same(f.group,t.group))reject();
  const group=t.group,copy=ts.map(x=>algosdk.decodeUnsignedTransaction(algosdk.encodeUnsignedTransaction(x)));copy.forEach(x=>x.group=undefined);
  if(!same(algosdk.computeGroupID(copy),group))reject();
 }else if(ts.length!==1||pi!==0||t.group)reject();
 return ts;
}
export function findSigned(q,values,payer){
 const matches=values.filter(Boolean).map(raw=>({raw,s:algosdk.decodeSignedTransaction(raw)})).filter(x=>x.s.txn.txID()===q.transaction_ids[q.payment_index]);
 if(matches.length!==1||!matches[0].s.sig||matches[0].s.sgnr||matches[0].s.txn.sender.toString()!==payer)reject();
 return b64(matches[0].raw);
}
