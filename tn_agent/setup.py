"""Guided production setup: users never edit config or provide news/AI API keys."""
import getpass
import json
from pathlib import Path
import httpx
from .config import ROOT,Config,atoms,amount,private_write
from .engine import NewsAgent,LocalWallet
from .chain import Chain
from .pera import Pera
from .secrets import parse_secret,save_secret


def required(prompt,secret=False):
    while True:
        value=(getpass.getpass(prompt) if secret else input(prompt)).strip()
        if value:return value
        print('This value is required. It cannot be left blank.')


def run(config_path=ROOT/'config.txt'):
    cfg=Config.load(config_path,require_ready=False);cfg.configured=False
    print('\nTRADING NEWS · Guided setup\n')
    print('1/4 · Checking the news service...')
    with httpx.Client(timeout=25,follow_redirects=False) as http:
        agent=NewsAgent(cfg,http);info=agent.info()
        total=atoms(info['source_price_usdc'])+atoms(info['selection_price_usdc'])
        print(f"Two charges per query: {info['source_price_usdc']} + {info['selection_price_usdc']} = {amount(total)} USDC.")
        print('Network: '+('Algorand · real funds' if cfg.network=='mainnet' else 'Algorand Testnet · test funds'))
        print('No news or AI API keys are required.')
        receiver=agent.chain.funds(cfg.merchant_address)
        if not receiver['ready'] or receiver['frozen']:
            raise ValueError('The service owner must prepare the receiving wallet first. You have not been charged.')
        print('\n2/4 · How would you like to approve purchases?')
        print('1. With Pera on my phone (manual use).\n2. Automatically with a dedicated wallet for this agent.')
        while True:
            mode=input('Choose 1 or 2 [1]: ').strip() or '1'
            if mode in {'1','2'}:break
        bridge=None;key=None
        try:
            if mode=='1':
                cfg.mode='pera';bridge=Pera(cfg.network)
                cfg.wallet_address=bridge.connect();wallet=bridge
            else:
                cfg.mode='automatic'
                print('Enter the 25 words of a dedicated, low-balance wallet here on YOUR computer. Never send them through chat.')
                while True:
                    try:key,cfg.wallet_address=parse_secret(required('Dedicated wallet key (hidden): ',True));break
                    except Exception:print('The key is invalid. Enter it again; it has not been saved.')
                wallet=LocalWallet(key,agent.chain)
            print('Wallet: '+cfg.wallet_address)
            print('\n3/4 · Budget')
            while True:
                value=input('Daily limit in USDC [1.00]: ').strip() or '1.00'
                try:
                    if atoms(value)<total:raise ValueError()
                    cfg.max_per_day_usdc=value;break
                except ValueError:print('Enter an amount that covers at least one query.')
            cfg.max_per_query_usdc=amount(total)
            if cfg.mode=='automatic':
                print(f'Each run may make both payments up to {amount(total)} USDC; daily limit {value} USDC (UTC day).')
                while required('Type ACCEPT to enable these automatic purchases: ').upper()!='ACCEPT':
                    print('Automatic mode cannot be enabled without your acceptance.')
            print('\n4/4 · Checking that your wallet can pay in USDC...')
            preparation_file=ROOT/'.private'/('preparation-'+cfg.network+'-'+cfg.wallet_address+'.json')
            while True:
                funds=agent.chain.funds(cfg.wallet_address)
                if funds['ready']:
                    if preparation_file.exists():preparation_file.unlink()
                    break
                try:
                    if preparation_file.exists():
                        saved=json.loads(preparation_file.read_text())
                        if saved['quote']['payer']!=cfg.wallet_address:raise ValueError('Another wallet has a pending preparation. Recover that setup first.')
                        if agent.chain.preparation_expired(saved['quote']):
                            preparation_file.unlink()
                            print('The previous preparation expired without completing. A new approval will be requested.')
                            continue
                        print('Recovering the same preparation without creating another transaction...')
                        agent.chain.submit_preparation(saved['quote'],saved['signed'])
                    else:
                        quote=agent.chain.preparation(cfg.wallet_address)
                        print(f"One-time preparation: reserve {quote['reserve_microalgo']/1e6:g} ALGO in your wallet, with a {quote['network_fee_microalgo']/1e6:g} ALGO fee.")
                        if cfg.mode=='automatic' and required('Approve this preparation? Type YES: ').upper()!='YES':
                            raise ValueError('Wallet preparation was not authorized.')
                        signed=wallet.sign(quote)
                        private_write(preparation_file,json.dumps({'quote':quote,'signed':signed}))
                        agent.chain.submit_preparation(quote,signed)
                except ValueError as e:
                    print(str(e))
                    if input('Press Enter to check again or type EXIT: ').strip().upper()=='EXIT':raise ValueError('Setup is pending; it has not been marked as complete.')
            while True:
                funds=agent.chain.funds(cfg.wallet_address)
                if funds['frozen']:raise ValueError('The USDC in this wallet is not available for transfer.')
                if funds['usdc_atoms']>=total:break
                print(f'Send at least {amount(total)} USDC on Algorand to the address above. Current balance: {amount(funds["usdc_atoms"])} USDC.')
                if input('Press Enter after receiving the funds, or type EXIT: ').strip().upper()=='EXIT':raise ValueError('Setup is waiting for funds. No purchase has been made.')
            if key:save_secret(ROOT/'.private/wallet.json',key)
            cfg.configured=True;cfg.validate();cfg.save(config_path)
            print('\nSetup complete. config.txt has been configured automatically.')
            print('Open START.bat or run bash start.sh to choose a symbol.')
            print('No news was purchased during setup. If wallet preparation was needed, it incurred the fee you approved.')
        finally:
            if bridge:bridge.close()
