"""Two genuine paid resources; the existing contest URL remains the news feed."""
import asyncio
from contextlib import asynccontextmanager,suppress
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
import secrets
import time

import httpx
from algosdk import encoding
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse
from .news.config import Settings,atomic_usdc
from .news.catalog import ASSETS,get_asset
from .news.storage import Store
from .news.providers import Providers
from .news.service import NewsService,NoProviders
from .news.ranking import rank_articles
from .news.payments import PaymentGateway,decode_payment


def create_app(settings=None,transport=None,source_price='0.10',selection_price='0.10'):
    cfg=settings or Settings.from_env()
    cfg=replace(cfg,demo=False,ai_enabled=False)
    if settings is None:
        cfg=replace(cfg,payments=os.getenv('PAYMENTS_ENABLED','true').lower()=='true',
                    public_url=os.getenv('PUBLIC_BASE_URL','https://x402-trading-news.onrender.com').rstrip('/'),
                    network_name=os.getenv('ALGORAND_NETWORK','mainnet'),
                    provider_keys={} if os.getenv('ENABLE_LEGACY_PROVIDERS','false').lower()!='true' else cfg.provider_keys)
        source_price=os.getenv('SOURCE_PRICE_USDC','0.10');selection_price=os.getenv('SELECTION_PRICE_USDC','0.10')
    cfg.validate();atomic_usdc(source_price);atomic_usdc(selection_price)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(transport=transport,follow_redirects=False,timeout=20,headers={'User-Agent':'TradingNewsAgent/4'}) as http:
            store=Store(cfg.db_path);app.state.store=store
            app.state.news=NewsService(cfg,store,Providers(cfg,store,http),http)
            app.state.feed=PaymentGateway(replace(cfg,price_usdc=source_price),store,http)
            app.state.selection=PaymentGateway(replace(cfg,price_usdc=selection_price),store,http)
            task=asyncio.create_task(app.state.news.prefetch()) if cfg.prefetch_assets else None
            try:yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):await task

    app=FastAPI(title='Trading News Agent API',version='4.0.0',lifespan=lifespan)
    app.state.settings=cfg;app.state.source_price=source_price;app.state.selection_price=selection_price
    rates={}
    @app.middleware('http')
    async def headers(request,call_next):
        if request.url.path.startswith('/api/'):
            key=request.client.host if request.client else 'unknown';now=time.monotonic()
            start,count=rates.get(key,(now,0))
            if now-start>=60:start,count=now,0
            if count>=cfg.request_limit:return JSONResponse({'detail':'Too many requests. Please wait one minute.'},status_code=429,headers={'Retry-After':'60'})
            if len(rates)>10000:rates.clear()
            rates[key]=(start,count+1)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.exception_handler(NoProviders)
    async def unavailable(request,exc):
        return JSONResponse({'status':'unavailable','detail':'No up-to-date news is available. You have not been charged.',
            'providers':exc.states,'billing':{'charged':False,'reason':'sources_unavailable'}},status_code=503,
            headers={'Retry-After':str(exc.retry_after)})

    def asset(symbol):
        try:return get_asset(symbol)
        except KeyError:raise HTTPException(404,'Symbol not available') from None
    def url(kind,symbol):
        return cfg.public_url+(f'/api/v1/market-signal/{symbol}' if kind=='feed' else f'/api/v1/news/{symbol}/best')

    @app.get('/health')
    async def health():return {'status':'ok','version':'4.0.0','payments_enabled':cfg.payments}

    @app.get('/api/v1/assets')
    async def assets():return {'assets':list(ASSETS.values())}

    @app.get('/api/v1/agent-info')
    async def info():
        return {'agent_protocol':1,'version':'4.0.0','network':cfg.network_name,'network_caip':cfg.network,
            'asset_id':cfg.asset,'pay_to':cfg.pay_to,'payments_enabled':cfg.payments,'assets':list(ASSETS.values()),
            'source_price_usdc':source_price,'selection_price_usdc':selection_price,
            'source_path':'/api/v1/market-signal/{symbol}','selection_path':'/api/v1/news/{symbol}/best',
            'quote_path':'/api/v1/quote/{symbol}','requires_news_keys':False,'requires_ai_key':False}

    @app.get('/api/v1/quote/{symbol}')
    async def quote(symbol:str):
        if not cfg.payments:raise HTTPException(503,'The service owner has not enabled purchases yet.')
        a=asset(symbol);report=await app.state.news.report(a)
        if not report.get('best_article') or report.get('freshness',{}).get('status')!='fresh':
            raise HTTPException(503,'There is not enough up-to-date news available. You have not been charged.')
        ident=secrets.token_urlsafe(24)
        app.state.store.cache('agent-quote:'+ident,report)
        stages=[]
        for name,gateway,label in [('feed',app.state.feed,'1/2 · News feed'),('selection',app.state.selection,'2/2 · News selection')]:
            stages.append({'id':name,'label':label,'url':url(name,a['symbol']),
                'challenge':gateway.challenge(await gateway.requirement(),url(name,a['symbol']))})
        return {'quote_id':ident,'expires_at':int(time.time())+600,'symbol':a['symbol'],
                'total_usdc':str((int(atomic_usdc(source_price))+int(atomic_usdc(selection_price)))/1_000_000),
                'stages':stages,'candidate_count':len(report['articles'])}

    async def make_feed(a,quote_id):
        if quote_id:
            report=app.state.store.cached('agent-quote:'+quote_id,600)
            if not report or report['symbol']!=a['symbol']:raise HTTPException(410,'The prepared query has expired; you have not been charged.')
        else:report=await app.state.news.report(a)
        fields=('id','title','url','summary','source','published_at','observed_at','date_basis','date_provider','effective_at','providers','coverage')
        return {'schema_version':'1.0','kind':'news_feed','symbol':a['symbol'],'name':a['name'],
            'generated_at':report['generated_at'],'snapshot_id':report['report_id'],'quote_id':quote_id,
            'articles':sorted([{k:item.get(k) for k in fields} for item in report['articles']],key=lambda x:x['url']),
            'freshness':report['freshness'],'providers':report['providers'],'warnings':report['warnings'],
            'attribution':report['attribution'],'billing':report['billing'],
            'best_article':report['best_article'] if report['freshness']['status']=='stale' else None}

    @app.get('/api/v1/market-signal/{symbol}')
    async def feed(symbol:str,request:Request):
        a=asset(symbol)
        if request.query_params:raise HTTPException(400,'Use the symbol in the path without query parameters.')
        return await app.state.feed.access(request.headers.get('payment-signature'),url('feed',a['symbol']),
            lambda:make_feed(a,request.headers.get('x-news-quote','')))

    def purchased_feed(symbol,first_token,second_token):
        if not first_token:raise HTTPException(409,'Purchase the news feed for this symbol first.')
        first,fp,proof=decode_payment(first_token)
        row=app.state.store.payment(fp)
        if not row or row['state']!='settled' or row['resource']!=url('feed',symbol) or not hmac.compare_digest(row['proof'],proof):
            raise HTTPException(403,'The news purchase has no valid receipt for this symbol.')
        if second_token:
            second,_,_=decode_payment(second_token)
            def payer(p):return encoding.msgpack_decode(p['payload']['paymentGroup'][p['payload']['paymentIndex']]).transaction.sender
            if payer(first)!=payer(second):raise HTTPException(403,'Both payments must come from the same wallet.')
        return json.loads(row['body']),json.loads(row['receipt'])

    @app.get('/api/v1/news/{symbol}/best')
    async def selection(symbol:str,request:Request):
        a=asset(symbol);token=request.headers.get('payment-signature')
        if request.query_params:raise HTTPException(400,'Use the symbol in the path without query parameters.')
        async def body():
            news,receipt=purchased_feed(a['symbol'],request.headers.get('x-news-feed-payment'),token)
            raw=[]
            for item in news['articles']:
                for coverage in item.get('coverage') or [{'url':item['url'],'source':item['source']}]:
                    raw.append(dict(item,url=coverage['url'],source=coverage['source'],provider=(item.get('providers') or ['gdelt'])[0]))
            ranked,stats=rank_articles(raw,a,cfg.max_age_hours)
            return {'schema_version':'1.0','kind':'selected_news','symbol':a['symbol'],'name':a['name'],
                'generated_at':datetime.now(timezone.utc).isoformat(),'source_generated_at':news['generated_at'],
                'source_snapshot_id':news['snapshot_id'],'selection_method':'explainable_rules',
                'best_article':ranked[0] if ranked else None,'alternatives':ranked[1:6],
                'source_receipt':receipt,'stats':stats,'warnings':news['warnings'],'attribution':news['attribution'],
                'billing':{'charged':False},'recommendation':'NOT_A_TRADING_SIGNAL'}
        return await app.state.selection.access(token,url('selection',a['symbol']),body)

    @app.get('/.well-known/x402.json')
    async def discovery():
        result=app.state.feed.challenge(await app.state.feed.requirement(),url('feed','BTC'))
        result['resources']=[url(kind,s) for s in ASSETS for kind in ['feed','selection']]
        result['name']='Trading News Agent';return result
    return app


def production_app():
    # Render must use persistent storage for receipts; deployment instructions configure it.
    if os.getenv('RENDER') and not os.getenv('DATABASE_PATH','').startswith('/var/data/'):
        raise ValueError('Set DATABASE_PATH=/var/data/news.sqlite3 and attach a persistent disk before enabling payments.')
    if os.getenv('RENDER') and not os.path.ismount('/var/data'):
        raise ValueError('The persistent disk is not mounted at /var/data. Payments have not been enabled.')
    return create_app()
