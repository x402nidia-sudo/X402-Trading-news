// Disposable test keys only; stdin and stdout stay local.
import {validate,findSigned,bytes} from '../wallet/validation.js';
let input='';for await(const chunk of process.stdin)input+=chunk;
const {quote,key}=JSON.parse(input);
const txs=validate(quote,quote.payer);
process.stdout.write(findSigned(quote,[txs[quote.payment_index].signTxn(bytes(key))],quote.payer));
