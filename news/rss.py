"""Free RSS/Atom retrieval. One cached download serves every asset; no API keys."""
import asyncio
from copy import deepcopy
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
import httpx
from .providers import retry_delay
from .ranking import clean_text, parse_date

MAX_BYTES = 2_000_000


def parse_feed(content, source):
    # Accept UTF-8 feeds only. Decode first so alternate encodings cannot hide a DTD.
    text = content.decode("utf-8-sig")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("DTD/entity declarations are not supported")
    root = ET.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("Not a news feed")
    items = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        fields = {}
        link = None
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            fields.setdefault(name, "".join(child.itertext()).strip())
            if name == "link" and child.attrib.get("rel", "alternate") == "alternate":
                link = child.attrib.get("href") or fields[name]
        date = fields.get("pubDate") or fields.get("published") or fields.get("date") or fields.get("updated")
        dt = parse_date(date)
        if dt is None:
            try:
                dt = parsedate_to_datetime(date)
                if dt.tzinfo is None:
                    dt = None
            except (ValueError, TypeError, OverflowError):
                dt = None
        items.append({"title": clean_text(fields.get("title"), 240),
                      "summary": clean_text(fields.get("description") or fields.get("summary"), 1200),
                      "url": link, "source": source, "provider": "rss:" + source,
                      "published_at": dt.astimezone(timezone.utc).isoformat() if dt else None})
        if len(items) >= 500:
            break
    return items


class RSSProviders:
    def __init__(self, settings, store, http):
        self.settings, self.store, self.http = settings, store, http
        self.cache_identity = {"rss": list(settings.rss_feed_urls)}
        self.locks = {url: asyncio.Lock() for url in settings.rss_feed_urls}

    async def fetch(self, url):
        source = urlsplit(url).hostname
        key = "rss:feed:v1:" + hashlib.sha256(url.encode()).hexdigest()
        state = {"provider": "rss:" + source, "status": "ok", "count": 0}
        async with self.locks[url]:
            cached = self.store.cached(key, self.settings.rss_cache_seconds)
            if cached is not None:
                return deepcopy(cached["items"]), dict(cached["state"], cached=True)
            failure = self.store.cached(key + ":failure", 60)
            if failure:
                return [], failure
            if self.store.cooldown(key):
                return [], dict(state, status="cooldown")
            try:
                async with self.http.stream("GET", url, timeout=12, follow_redirects=False,
                                            headers={"Accept": "application/rss+xml, application/atom+xml, application/xml"}) as response:
                    if response.status_code != 200:
                        state.update(status="http_error", http_status=response.status_code)
                        if response.status_code == 429:
                            delay = retry_delay(response.headers.get("retry-after"))
                            self.store.cooldown(key, delay)
                            state.update(status="rate_limited", retry_after=delay)
                        self.store.cache(key + ":failure", state)
                        return [], state
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise ValueError("Feed too large")
                items = parse_feed(bytes(content), source)
                state["count"] = len(items)
                self.store.cache(key, {"items": items, "state": state})
                return items, state
            except httpx.TimeoutException:
                state["status"] = "timeout"
            except httpx.HTTPError:
                state["status"] = "connection_error"
            except (ValueError, ET.ParseError):
                state["status"] = "invalid_response"
            self.store.cache(key + ":failure", state)
            return [], state

    async def all(self, asset):
        results = await asyncio.gather(*(self.fetch(url) for url in self.locks))
        return [item for items, _ in results for item in items], [state for _, state in results]
