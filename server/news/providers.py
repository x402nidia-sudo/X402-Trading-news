"""Server-side adapters. Never rotate IPs or keys to bypass upstream limits."""
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import time
import httpx
from .catalog import query_for
from .ranking import publisher_domain


def retry_delay(value):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        try:
            return max(1, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 60


def gdelt_query(asset, language):
    # GDELT uses implicit AND between terms and non-nested OR groups.
    terms = ['"' + x.replace('"', '').strip() + '"' for x in asset['aliases'][:3]]
    query = '(' + ' OR '.join(terms) + ')' if len(terms) > 1 else terms[0]
    if asset.get('ambiguous'):
        query += ' (crypto OR blockchain OR token OR criptomoneda)'
    if language != 'all':
        query += ' sourcelang:' + {'en': 'english', 'es': 'spanish'}[language]
    return query


def gdelt_date(value):
    try:
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


class Providers:
    def __init__(self, settings, store, http):
        self.settings, self.store, self.http = settings, store, http
        self.locks = {p: asyncio.Lock() for p in ('gdelt', 'guardian', 'newsapi', 'gnews')}

    async def fetch(self, provider, asset):
        cfg = self.settings
        key = cfg.provider_keys.get(provider)
        status = {'provider': provider, 'status': 'ok', 'count': 0}
        if provider == 'gdelt' and not cfg.gdelt_enabled:
            return [], dict(status, status='disabled')
        if provider != 'gdelt' and not key:
            return [], dict(status, status='not_configured')
        if provider == 'guardian' and cfg.language == 'es':
            return [], dict(status, status='language_not_supported')
        # A broken source must not build a long queue of customer requests.
        remaining = self.store.cooldown_remaining(provider)
        if remaining:
            return [], dict(status, status='cooldown', retry_after=remaining)
        deadline = time.monotonic() + cfg.provider_queue_seconds
        lock = self.locks[provider]
        try:
            await asyncio.wait_for(lock.acquire(), timeout=cfg.provider_queue_seconds)
        except asyncio.TimeoutError:
            return [], dict(status, status='busy', retry_after=10)
        try:
            interval = cfg.gdelt_interval_seconds if provider == 'gdelt' else 1.1
            while True:
                remaining = self.store.cooldown_remaining(provider)
                if remaining:
                    return [], dict(status, status='cooldown', retry_after=remaining)
                delay = self.store.reserve_request(provider, interval)
                if not delay:
                    break
                if time.monotonic() + delay > deadline:
                    return [], dict(status, status='busy', retry_after=max(1, int(delay) + 1))
                await asyncio.sleep(delay)
            if not self.store.budget(provider, cfg.provider_budgets.get(provider, 0)):
                now = datetime.now(timezone.utc)
                midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                delay = int((midnight - now).total_seconds()) + 1
                self.store.cooldown(provider, delay)
                return [], dict(status, status='daily_limit', retry_after=delay)
            return await self._request(provider, asset, key, status)
        finally:
            lock.release()

    def failed(self, provider, status, reason, minimum=0, **extra):
        delay = self.store.provider_failure(provider, minimum)
        return [], dict(status, status=reason, retry_after=delay, **extra)

    async def _request(self, provider, asset, key, status):
        cfg = self.settings
        start = datetime.now(timezone.utc) - timedelta(hours=cfg.max_age_hours)
        query = query_for(asset)
        headers = {}
        if provider == 'gdelt':
            url = 'https://api.gdeltproject.org/api/v2/doc/doc'
            params = {'query': gdelt_query(asset, cfg.language), 'mode': 'artlist', 'format': 'json',
                      'maxrecords': 100, 'sort': 'datedesc', 'timespan': f'{cfg.max_age_hours}h'}
        elif provider == 'guardian':
            url = 'https://content.guardianapis.com/search'
            params = {'q': query, 'api-key': key, 'page-size': 30, 'order-by': 'newest',
                      'from-date': start.date().isoformat(), 'show-fields': 'trailText'}
        elif provider == 'newsapi':
            url = 'https://newsapi.org/v2/everything'
            params = {'q': query, 'pageSize': 30, 'sortBy': 'publishedAt', 'from': start.isoformat()}
            headers = {'X-Api-Key': key}
        else:
            url = 'https://gnews.io/api/v4/search'
            params = {'q': query[:200], 'apikey': key, 'max': 10,
                      'sortby': 'publishedAt', 'from': start.isoformat()}
        if provider in {'newsapi', 'gnews'} and cfg.language != 'all':
            params['language' if provider == 'newsapi' else 'lang'] = cfg.language
        try:
            res = await self.http.get(url, params=params, headers=headers, timeout=20 if provider == 'gdelt' else 12)
            if res.status_code == 429:
                return self.failed(provider, status, 'rate_limited', retry_delay(res.headers.get('retry-after')))
            if res.status_code != 200:
                return self.failed(provider, status, 'http_error',
                                   900 if res.status_code in {401, 403} else 0, http_status=res.status_code)
            data = res.json()
            if not isinstance(data, dict):
                raise ValueError('Invalid provider data')
            if provider == 'guardian':
                response = data.get('response', {})
                if response.get('status') != 'ok' or not isinstance(response.get('results'), list):
                    raise ValueError('Invalid Guardian data')
                items = [{'title': x.get('webTitle'), 'summary': (x.get('fields') or {}).get('trailText'),
                          'url': x.get('webUrl'), 'source': 'The Guardian', 'provider': provider,
                          'published_at': x.get('webPublicationDate')} for x in response['results'] if isinstance(x, dict)]
            else:
                if not isinstance(data.get('articles'), list) or (provider == 'newsapi' and data.get('status') != 'ok'):
                    raise ValueError('Invalid article list')
                if provider == 'gdelt':
                    items = [{'title': x.get('title'), 'summary': '', 'url': x.get('url'),
                              'source': publisher_domain(x.get('url') or ''), 'provider': provider,
                              'published_at': None, 'observed_at': gdelt_date(x.get('seendate')),
                              'date_basis': 'first_seen', 'language': x.get('language')}
                             for x in data['articles'] if isinstance(x, dict)]
                else:
                    items = [{'title': x.get('title'), 'summary': x.get('description'),
                              'url': x.get('url'), 'source': (x.get('source') or {}).get('name', provider),
                              'provider': provider, 'published_at': x.get('publishedAt')}
                             for x in data['articles'] if isinstance(x, dict)]
            self.store.provider_success(provider)
            return items, dict(status, count=len(items))
        except httpx.TimeoutException:
            return self.failed(provider, status, 'timeout')
        except httpx.HTTPError:
            return self.failed(provider, status, 'connection_error')
        except (ValueError, TypeError, KeyError, AttributeError):
            return self.failed(provider, status, 'invalid_response')

    async def all(self, asset):
        results = await asyncio.gather(*(self.fetch(p, asset) for p in self.locks))
        return [a for articles, _ in results for a in articles], [state for _, state in results]
