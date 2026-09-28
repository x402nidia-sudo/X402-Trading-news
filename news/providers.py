import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import time
import httpx
from .catalog import query_for
from .ranking import NON_NEWS_DOMAINS


def retry_delay(value):
    try:
        return min(86400, max(1, int(value)))
    except (TypeError, ValueError):
        try:
            return min(86400, max(1, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())))
        except (TypeError, ValueError, OverflowError):
            return 60


class Providers:
    def __init__(self, settings, store, http):
        self.settings, self.store, self.http = settings, store, http
        self.locks = {p: asyncio.Lock() for p in ("newsapi", "gnews")}
        self.last_request = {p: 0. for p in self.locks}

    async def fetch(self, provider, asset):
        key = self.settings.provider_keys.get(provider)
        if not key:
            return [], {"provider": provider, "status": "not_configured", "count": 0}
        status = {"provider": provider, "status": "ok", "count": 0}
        async with self.locks[provider]:
            if self.store.cooldown(provider):
                return [], dict(status, status="cooldown")
            if not self.store.budget(provider, self.settings.provider_budgets[provider]):
                return [], dict(status, status="daily_limit")
            await asyncio.sleep(max(0, 1.1 - (time.monotonic() - self.last_request[provider])))
            self.last_request[provider] = time.monotonic()
            start = datetime.now(timezone.utc) - timedelta(hours=168)
            query = query_for(asset)
            params, headers = {}, {}
            if provider == "newsapi":
                url = "https://newsapi.org/v2/everything"
                params = {"q": query, "pageSize": 30, "sortBy": "publishedAt", "from": start.isoformat(),
                          "excludeDomains": ",".join(sorted(NON_NEWS_DOMAINS)), "searchIn": "title,description"}
                headers = {"X-Api-Key": key}
            else:
                url = "https://gnews.io/api/v4/search"
                filtered_query = "(" + query + ') NOT "added to PyPI"'
                params = {"q": filtered_query if len(filtered_query) <= 200 else query[:200], "apikey": key, "max": 10,
                          "sortby": "publishedAt", "from": start.isoformat()}
            if self.settings.language != "all":
                params["language" if provider == "newsapi" else "lang"] = self.settings.language
            try:
                res = await self.http.get(url, params=params, headers=headers, timeout=12)
                if res.status_code == 429:
                    delay = retry_delay(res.headers.get("retry-after"))
                    self.store.cooldown(provider, delay)
                    return [], dict(status, status="rate_limited", retry_after=delay)
                if res.status_code != 200:
                    if res.status_code in {401, 403}:
                        self.store.cooldown(provider, 900)
                    return [], dict(status, status="http_error", http_status=res.status_code)
                data = res.json()
                if (provider == "newsapi" and data.get("status") != "ok") or "articles" not in data:
                    return [], dict(status, status="invalid_response")
                items = [{"title": x.get("title"), "summary": x.get("description") or x.get("content"),
                          "url": x.get("url"), "source": (x.get("source") or {}).get("name", provider),
                          "provider": provider, "published_at": x.get("publishedAt")}
                         for x in data["articles"] if isinstance(x, dict)]
                return items, dict(status, count=len(items))
            except httpx.TimeoutException:
                return [], dict(status, status="timeout")
            except httpx.HTTPError:
                return [], dict(status, status="connection_error")
            except (ValueError, TypeError, KeyError, AttributeError):
                return [], dict(status, status="invalid_response")

    async def all(self, asset):
        results = await asyncio.gather(*(self.fetch(p, asset) for p in self.locks))
        return [a for articles, _ in results for a in articles], [state for _, state in results]
