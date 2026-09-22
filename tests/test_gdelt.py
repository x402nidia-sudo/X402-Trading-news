"""Network-free integration and resilience tests for the free provider."""

import asyncio

from datetime import datetime, timedelta, timezone

from email.utils import format_datetime

import json

import time

import httpx

import pytest

from fastapi.testclient import TestClient

from server.news.catalog import get_asset

from server.news.config import Settings

from server.news.payments import encoded

from server.news.providers import Providers, gdelt_query, retry_delay

from server.news.ranking import rank_articles

from server.news.service import NewsService, NoProviders

from server.news.storage import Store

def rows(symbol='Bitcoin'):
    return {'articles': [
        {'title': f'{symbol} blockchain security upgrade fixes critical vulnerability',
         'url': 'https://wire.example/upgrade', 'domain': 'spoof.example',
         'seendate': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'language': 'English'},
        {'title': 'Gardening tools and garden tips', 'url': 'https://wire.example/garden',
         'seendate': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')},
    ]}

def config(tmp_path, **kwargs):
    return Settings(demo=False, db_path=str(tmp_path / 'gdelt.db'), **kwargs)

def age_report(store, key, seconds):
    body = store.cached(key, 100000)
    body['generated_at'] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with store.connect() as db:
        db.execute('UPDATE cache SET created=?,body=? WHERE key=?', (time.time()-seconds, json.dumps(body), key))
        db.execute('UPDATE provider_state SET next_request=0')

def test_gdelt_query_handles_language_and_ambiguous_assets():
    assert gdelt_query(get_asset('BTC'), 'en') == '"Bitcoin" sourcelang:english'
    q = gdelt_query(get_asset('AVAX'), 'es')
    assert '(crypto OR blockchain OR token OR criptomoneda)' in q and 'sourcelang:spanish' in q
    assert ' AND ' not in q
    assert 'sourcelang:' not in gdelt_query(get_asset('BTC'), 'all')

def test_gdelt_normalizes_detection_without_inventing_publication(tmp_path):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=rows())
    async def run():
        cfg = config(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            items, status = await Providers(cfg, Store(cfg.db_path), http).fetch('gdelt', get_asset('BTC'))
        ranked, stats = rank_articles(items, get_asset('BTC'))
        assert status['status'] == 'ok' and len(ranked) == 1
        assert ranked[0]['published_at'] is None and ranked[0]['observed_at']
        assert ranked[0]['date_basis'] == 'first_seen' and ranked[0]['summary'] == ''
        assert ranked[0]['source'] == 'wire.example'
        assert stats['excluded']['irrelevant'] == 1
        req = seen[0]
        assert req.url.params['maxrecords'] == '100' and req.url.params['timespan'] == '48h'
        assert 'apikey' not in req.url.params and 'api-key' not in req.url.params
    asyncio.run(run())

def test_invalid_gdelt_detection_date_is_not_fabricated(tmp_path):
    def handler(req):
        data = rows(); data['articles'][0]['seendate'] = 'garbage'
        return httpx.Response(200, json=data)
    async def run():
        cfg = config(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            items, _ = await Providers(cfg, Store(cfg.db_path), http).fetch('gdelt', get_asset('BTC'))
            assert rank_articles(items, get_asset('BTC'))[0] == []
    asyncio.run(run())

def test_1000_concurrent_reports_use_one_provider_request_and_survive_restart(tmp_path):
    calls = []
    async def handler(req):
        calls.append(req)
        await asyncio.sleep(.005)
        return httpx.Response(200, json=rows())
    async def run():
        cfg = config(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            store = Store(cfg.db_path)
            service = NewsService(cfg, store, Providers(cfg, store, http), http)
            reports = await asyncio.gather(*(service.report(get_asset('BTC')) for _ in range(1000)))
            assert len(calls) == 1
            assert len({r['report_id'] for r in reports}) == 1
            assert all(r['best_article'] and r['freshness']['status'] == 'fresh' for r in reports)
            restarted = NewsService(cfg, Store(cfg.db_path), Providers(cfg, store, http), http)
            again = await restarted.report(get_asset('BTC'))
            assert again['cached'] and len(calls) == 1
    asyncio.run(run())

def test_429_cooldown_shared_by_assets_and_persisted(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={'Retry-After': '120'})
    async def run():
        cfg = config(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            store = Store(cfg.db_path)
            provider = Providers(cfg, store, http)
            _, first = await provider.fetch('gdelt', get_asset('BTC'))
            assert first['status'] == 'rate_limited' and first['retry_after'] >= 120
            results = await asyncio.gather(*(provider.fetch('gdelt', get_asset('ETH')) for _ in range(100)))
            assert all(s['status'] == 'cooldown' for _, s in results)
            _, again = await Providers(cfg, Store(cfg.db_path), http).fetch('gdelt', get_asset('SOL'))
            assert again['status'] == 'cooldown' and len(calls) == 1
    asyncio.run(run())

@pytest.mark.parametrize('kind', ['502', 'json', 'timeout'])
def test_transient_failures_back_off_without_hammering_source(tmp_path, kind):
    calls = []
    def handler(req):
        calls.append(req)
        if kind == 'timeout': raise httpx.ReadTimeout('test', request=req)
        return httpx.Response(502) if kind == '502' else httpx.Response(200, text='backend busy')
    async def run():
        cfg = config(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            store = Store(cfg.db_path); provider = Providers(cfg, store, http)
            _, first = await provider.fetch('gdelt', get_asset('BTC'))
            _, second = await provider.fetch('gdelt', get_asset('ETH'))
            assert first['retry_after'] >= 60 and second['status'] == 'cooldown'
            assert len(calls) == 1
            store.cooldown('gdelt', -1)
            with store.connect() as db: db.execute('UPDATE provider_state SET next_request=0')
            _, third = await provider.fetch('gdelt', get_asset('ETH'))
            assert third['retry_after'] >= 120 and len(calls) == 2
    asyncio.run(run())

def test_retry_after_date_and_long_delay_are_respected():
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=600), usegmt=True)
    assert 598 <= retry_delay(date) <= 600
    assert retry_delay('172800') == 172800

def test_persistent_frequency_reservation_and_bounded_queue(tmp_path):
    cfg = config(tmp_path, provider_queue_seconds=.01)
    store = Store(cfg.db_path)
    assert store.reserve_request('gdelt', 10) == 0
    assert 9 < Store(cfg.db_path).reserve_request('gdelt', 10) <= 10
    def handler(req): raise AssertionError('Must not contact source before next slot')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = Providers(cfg, store, http)
            _, result = await provider.fetch('gdelt', get_asset('BTC'))
            assert result['status'] == 'busy'
            await provider.locks['gdelt'].acquire()
            try:
                _, result = await provider.fetch('gdelt', get_asset('BTC'))
                assert result['status'] == 'busy'
            finally:
                provider.locks['gdelt'].release()
    asyncio.run(run())

def test_stale_fallback_preserves_age_has_cutoff_and_recovers(tmp_path):
    mode = 'ok'; calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=rows()) if mode == 'ok' else httpx.Response(429)
    async def run():
        nonlocal mode
        cfg = config(tmp_path); store = Store(cfg.db_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            service = NewsService(cfg, store, Providers(cfg, store, http), http)
            asset = get_asset('BTC'); key = service.cache_key(asset)
            fresh = await service.report(asset)
            age_report(store, key, 1000); mode = '429'
            stale = await service.report(asset)
            assert stale['status'] == 'stale_fallback' and stale['freshness']['age_seconds'] >= 1000
            assert stale['billing'] == {'charged': False, 'reason': 'stale_fallback'}
            assert stale['report_id'] == fresh['report_id']
            assert (await service.report(asset))['generated_at'] == stale['generated_at']
            assert len(calls) == 2
            age_report(store, key, 3601)
            with pytest.raises(NoProviders): await service.report(asset)
            assert len(calls) == 2
            store.cooldown('gdelt', -1); mode = 'ok'
            renewed = await service.report(asset)
            assert renewed['freshness']['status'] == 'fresh' and len(calls) == 3
    asyncio.run(run())

def test_fallback_does_not_show_articles_outside_window(tmp_path):
    def handler(req): return httpx.Response(200, json=rows())
    async def run():
        cfg = config(tmp_path); store = Store(cfg.db_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            service = NewsService(cfg, store, Providers(cfg, store, http), http)
            asset = get_asset('BTC'); key = service.cache_key(asset)
            report = await service.report(asset)
            report['articles'][0]['effective_at'] = (datetime.now(timezone.utc)-timedelta(hours=60)).isoformat()
            store.cache(key, report); store.cooldown('gdelt', 120)
            with pytest.raises(NoProviders): await service.report(asset)
    asyncio.run(run())

def test_another_configured_provider_keeps_fresh_report_on_gdelt_429(tmp_path):
    def handler(req):
        if req.url.host == 'api.gdeltproject.org': return httpx.Response(429)
        return httpx.Response(200, json={'response': {'status': 'ok', 'results': [{
            'webTitle': 'Bitcoin blockchain protocol upgrade', 'webUrl': 'https://theguardian.com/test',
            'webPublicationDate': datetime.now(timezone.utc).isoformat(), 'fields': {}}]}})
    async def run():
        cfg = config(tmp_path, provider_keys={'guardian': 'test'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            store = Store(cfg.db_path)
            report = await NewsService(cfg, store, Providers(cfg, store, http), http).report(get_asset('BTC'))
            assert report['freshness']['status'] == 'fresh' and report['best_article']['source'] == 'The Guardian'
            assert any(p['status'] == 'rate_limited' for p in report['providers'])
    asyncio.run(run())

def test_prefetch_warms_shared_cache_without_payment_and_cancels(tmp_path):
    calls = []
    def handler(req): calls.append(req); return httpx.Response(200, json=rows())
    async def run():
        cfg = config(tmp_path, prefetch_assets=('BTC',)); store = Store(cfg.db_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            service = NewsService(cfg, store, Providers(cfg, store, http), http)
            task = asyncio.create_task(service.prefetch())
            try:
                for _ in range(100):
                    if store.cached(service.cache_key(get_asset('BTC')), 900): break
                    await asyncio.sleep(.005)
                assert store.cached(service.cache_key(get_asset('BTC')), 900)
                report = await service.report(get_asset('BTC'))
                assert report['cached'] and len(calls) == 1
                assert calls[0].url.host == 'api.gdeltproject.org'
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())

def test_gdelt_selects_material_asset_news_not_first_or_most_recent(tmp_path):
    def handler(req):
        data = rows()
        data['articles'].insert(0, {'title': 'Bitcoin price prediction could rise next week',
            'url': 'https://wire.example/prediction',
            'seendate': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')})
        data['articles'][1]['seendate'] = (datetime.now(timezone.utc)-timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')
        return httpx.Response(200, json=data)
    async def run():
        cfg = config(tmp_path); store = Store(cfg.db_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            report = await NewsService(cfg, store, Providers(cfg, store, http), http).report(get_asset('BTC'))
            assert report['best_article']['url'].endswith('/upgrade')
            assert report['best_article']['category'] == 'security'
            assert report['best_article']['selection_reasons'] and report['stats']['unique'] == 2
    asyncio.run(run())

def test_recent_detection_does_not_override_known_old_publication():
    detected = {'title': 'Bitcoin blockchain upgrade', 'url': 'https://wire.example/same',
        'provider': 'gdelt', 'observed_at': datetime.now(timezone.utc).isoformat()}
    known = dict(detected, provider='guardian', published_at=(datetime.now(timezone.utc)-timedelta(hours=70)).isoformat())
    assert not rank_articles([detected, known], get_asset('BTC'))[0]
