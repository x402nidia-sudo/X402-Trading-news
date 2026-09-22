import {PeraWalletConnect} from '@perawallet/connect';
import {validate,findSigned} from './validation.js';
const $=id=>document.getElementById(id),token=location.hash.slice(1);
let wallet,payer='',session,task,busy=false,completed='';
async function get(){const r=await fetch('/session',{headers:{'X-Agent-Token':token}});if(!r.ok)throw new Error('The local session is not valid. Return to the agent.');return r.json();}
async function reply(value,error){const r=await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Token':token},body:JSON.stringify({id:task.id,value,error})});if(!r.ok)throw new Error('The agent is no longer waiting for this operation.');completed=task.id;}
async function connect(){
 if(!wallet)wallet=new PeraWalletConnect({chainId:session.network==='mainnet'?416001:416002,shouldShowSignTxnToast:true});
 let addresses=await wallet.reconnectSession().catch(()=>[]);
 if(!addresses.length)addresses=await wallet.connect();
 payer=addresses[0];if(!payer)throw new Error('Connect a wallet.');
 if(session.expected_address&&payer!==session.expected_address)throw new Error('The connected wallet does not match the agent configuration.');
 wallet.connector?.on('disconnect',()=>{payer='';});
}
function render(){
 if(!task||task.id===completed){$('approve').disabled=true;$('approve').textContent='Waiting for the agent...';return;}
 $('approve').disabled=busy;
 if(task.action==='connect'){$('title').textContent='Connect your wallet';$('description').textContent='Use Pera to approve the agent payments. Do not enter your recovery phrase here.';$('details').textContent='Network: '+(session.network==='mainnet'?'Algorand · real funds':'Algorand Testnet · test funds');$('approve').textContent='Connect Pera';return;}
 const q=task.data;
 if(q.kind==='prepare_usdc'){$('title').textContent='Prepare your wallet for USDC';$('description').textContent='This is a one-time setup. Pera will ask you to approve the preparation transaction.';$('details').textContent=`Transferred amount: 0 USDC\nNetwork reserve in your wallet: ${q.reserve_microalgo/1e6} ALGO\nNetwork fee: ${q.network_fee_microalgo/1e6} ALGO`;$('approve').textContent='Prepare with Pera';}
 else{$('title').textContent=q.label;$('description').textContent='Review this charge and approve it in Pera.';$('details').textContent=`Price: ${Number(q.challenge.accepts[0].amount)/1e6} USDC\nNetwork fee paid by you: ${q.customer_network_fee_microalgo/1e6} ALGO\nRecipient: ${q.challenge.accepts[0].payTo}`;$('approve').textContent='Approve in Pera';}
}
$('approve').addEventListener('click',async()=>{
 if(!task||busy)return;busy=true;render();$('status').textContent='Please confirm in Pera...';
 try{
  await connect();
  if(task.action==='connect')await reply(payer);
  else{const q=task.data,txs=validate(q,payer),group=txs.map((txn,i)=>({txn,signers:q.sign_indexes.includes(i)?[payer]:[]}));
   const result=await wallet.signTransaction([group]);await reply(findSigned(q,result,payer));}
  $('status').textContent='Approval received. The agent is continuing in the terminal.';
 }catch(e){$('status').textContent=e.message||'Operation cancelled.';try{await reply(null,'cancelled');}catch{}}
 finally{busy=false;render();}
});
async function poll(){try{session=await get();if(!busy){task=session.task;render();}}catch(e){$('status').textContent='You can return to the agent terminal.';$('approve').disabled=true;return;}setTimeout(poll,1000);}
poll();
