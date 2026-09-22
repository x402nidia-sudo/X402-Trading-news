#!/usr/bin/env python3
"""Trading News: choose an asset or return the selected news as JSON for another agent."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import httpx
from tn_agent.config import ROOT,Config
from tn_agent.engine import NewsAgent,LocalWallet,PendingOrder,available_symbols
from tn_agent.journal import Journal
from tn_agent.pera import Pera
from tn_agent.secrets import load_secret


def get_news(symbol,config_path=ROOT/'config.txt',resume=None):
    cfg=Config.load(config_path)
    if cfg.mode!='automatic':raise ValueError('For unattended use, run the installer and choose automatic mode with a dedicated wallet.')
    with httpx.Client(timeout=25,follow_redirects=False) as http:
        agent=NewsAgent(cfg,http)
        key,address=load_secret(ROOT/'.private/wallet.json')
        if address!=cfg.wallet_address:raise ValueError('The saved key belongs to a different wallet.')
        with agent.journal.exclusive():
            order=agent.journal.get(resume) if resume else agent.new_order(symbol.upper())
            return agent.run(order,LocalWallet(key,agent.chain))


def show_symbols(values):
    print('\nAvailable symbols:')
    for start in range(0,len(values),4):
        print('  '.join(f"{a['symbol']:<6} {a['name'][:19]:<19}" for a in values[start:start+4]))
    print()


def clean(s):return ''.join(c for c in str(s) if ord(c)>=32 or c=='\n')


def show(result):
    print('\nTRADING NEWS · '+result.get('symbol',''))
    a=result.get('best_article')
    if a:
        print(clean(a['title']));print('Source: '+clean(a['source']));print(a['url'])
        print(('Detected: ' if a.get('date_basis')=='first_seen' else 'Published: ')+str(a.get('effective_at') or a.get('published_at') or a.get('observed_at')))
        print('\nWhy this story was selected:')
        for reason in a.get('selection_reasons',[]):print('  · '+clean(reason))
    else:print('This response does not contain a selected story.')
    print('\nCharged for this query: '+result.get('total_charged_usdc','0')+' USDC')
    print('Recovery reference: '+result.get('order_id',''))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('symbol',nargs='?');p.add_argument('--symbol',dest='named_symbol')
    p.add_argument('--symbols',action='store_true',help='Show the symbol catalog without paying')
    p.add_argument('--json',action='store_true',help='JSON output for another agent')
    p.add_argument('--pay',action='store_true',help='Authorize a query within the configured budget')
    p.add_argument('--quote',action='store_true',help='Check news availability and price without signing or paying')
    p.add_argument('--diagnostics',action='store_true');p.add_argument('--resume',help='Resume a saved query without repeating the first charge')
    p.add_argument('--pending',action='store_true');p.add_argument('--describe',action='store_true')
    p.add_argument('--config',type=Path,default=ROOT/'config.txt')
    args=p.parse_args(argv)
    try:
        catalog=available_symbols()
        if args.describe:
            print(json.dumps({'name':'trading_news','description':'Select the most relevant news for one supported crypto asset. Two USDC charges within configured limits.',
                'symbols':[a['symbol'] for a in catalog],'command':'python agent.py --symbol SYMBOL --json --pay',
                'recovery':'python agent.py --resume ORDER_ID --json --pay','requires_setup':True},ensure_ascii=False));return 0
        if args.symbols:
            if args.json:print(json.dumps({'assets':catalog},ensure_ascii=False))
            else:show_symbols(catalog)
            return 0
        if args.pending:
            orders=Journal(ROOT/'.private/orders.sqlite3').pending()
            data=[{'order_id':o['id'],'symbol':o['symbol'],'completed_steps':[n for n,s in o['stages'].items() if s.get('spent')]} for o in orders]
            print(json.dumps(data,ensure_ascii=False,indent=None if args.json else 2));return 0
        cfg=Config.load(args.config)
        symbol=(args.named_symbol or args.symbol or '').upper()
        with httpx.Client(timeout=25,follow_redirects=False) as http:
            agent=NewsAgent(cfg,http)
            if args.diagnostics:
                data={'service':agent.info(),'wallet':agent.chain.funds(cfg.wallet_address),'charged':False}
                print(json.dumps(data,ensure_ascii=False,indent=None if args.json else 2));return 0
            if not args.resume and not symbol:
                if args.json:raise ValueError('Provide --symbol and --pay. Use --symbols --json to read the catalog.')
                show_symbols(catalog)
                while symbol not in {a['symbol'] for a in catalog}:
                    symbol=input('Which symbol would you like to query? ').strip().upper()
                    if symbol not in {a['symbol'] for a in catalog}:print('Choose a symbol from the list.')
            if args.quote:
                q,total=agent.quote(symbol)
                public={k:q[k] for k in ['symbol','candidate_count','total_usdc','expires_at']};public['charged']=False
                print(json.dumps(public,ensure_ascii=False,indent=None if args.json else 2));return 0
            if args.json and not args.pay:raise ValueError('Use --pay to authorize the purchase. --quote checks the service without paying.')
            if args.json and cfg.mode!='automatic':raise ValueError('Pera requires your approval. For another agent, select automatic mode in the installer.')
            if not args.pay:
                print(f'Maximum total: {cfg.max_per_query_usdc} USDC for the news feed and selection.')
                if input('Continue with this query? [y/N]: ').strip().lower() not in {'y','yes'}:return 0
            with agent.journal.exclusive(),ExitStack() as stack:
                order=agent.journal.get(args.resume) if args.resume else agent.new_order(symbol)
                if order.get('result') is not None:result=order['result']
                else:
                    try:
                        if cfg.mode=='pera':
                            wallet=stack.enter_context(Pera(cfg.network,cfg.wallet_address));wallet.connect()
                        else:
                            key,address=load_secret(ROOT/'.private/wallet.json')
                            if address!=cfg.wallet_address:raise ValueError('The key does not match the configured wallet.')
                            wallet=LocalWallet(key,agent.chain)
                    except (Exception,KeyboardInterrupt):
                        if not any(s.get('signature') for s in order['stages'].values()):
                            order['result']={'status':'cancelled','order_id':order['id'],'total_charged_usdc':'0'}
                            agent.journal.save(order,True)
                        raise
                    result=agent.run(order,wallet)
            directory=ROOT/'reports';directory.mkdir(exist_ok=True)
            (directory/(result['order_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if args.json:print(json.dumps(result,ensure_ascii=False))
            else:show(result)
            return 0
    except (ValueError,RuntimeError,httpx.HTTPError,OSError) as e:
        data={'status':'error','message':str(e)}
        if isinstance(e,PendingOrder):data['order_id']=e.order_id;data['status']='pending'
        if args.json:print(json.dumps(data,ensure_ascii=False))
        else:print(str(e),file=sys.stderr)
        return 2
    except KeyboardInterrupt:print('\nInterrupted. Use --pending to recover a purchase already started.',file=sys.stderr);return 130


if __name__=='__main__':raise SystemExit(main())
