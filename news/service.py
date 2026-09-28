import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from .catalog import ASSETS
from .ranking import rank_articles, parse_date
from .insights import build_insight


class NoProviders(Exception):
    def __init__(self, states):
        self.states = states


class NewsService:
    def __init__(self, settings, store, providers, http):
        self.settings, self.store, self.providers, self.http = settings, store, providers, http
        self.locks = {s: asyncio.Lock() for s in ASSETS}

    async def snapshot(self, asset):
        symbol = asset["symbol"]
        config_signature = {"demo": self.settings.demo, "hours": self.settings.max_age_hours,
                            "language": self.settings.language, "asset": asset,
                            "providers": sorted(k for k, v in self.settings.provider_keys.items() if v)}
        # Separate unsold reports from earlier AI caches; purchased receipts stay recoverable.
        key = symbol + ":v9:cards:" + hashlib.sha256(json.dumps(config_signature, sort_keys=True).encode()).hexdigest()[:20]
        async with self.locks[symbol]:
            cached = self.store.cached(key, self.settings.cache_seconds)
            now = datetime.now(timezone.utc)
            cutoff = max(now - timedelta(hours=self.settings.max_age_hours), now.replace(hour=0, minute=0, second=0, microsecond=0))
            if cached and cached["report"].get("day_utc") == now.date().isoformat() and all(parse_date(a["published_at"]) >= cutoff for a in cached["report"]["articles"]):
                result = deepcopy(cached)
                result["report"]["cached"] = True
                return result
            failure = self.store.cached(key + ":failure", 60)
            if failure:
                raise NoProviders(failure)
            raw, states = await self.providers.all(asset)
            if not any(s["status"] == "ok" for s in states):
                self.store.cache(key + ":failure", states)
                raise NoProviders(states)
            articles, stats = rank_articles(raw, asset, self.settings.max_age_hours)
            for article in articles:
                article["insight"] = build_insight(article, asset)
            best = articles[0] if articles else None
            warnings = ["Priority scores measure relevance. Per-article BUY/SELL/HOLD signals are heuristic interpretations of the headline and available excerpt, not price forecasts or personalized investment advice.",
                        "Source priority uses predefined domain weights, not factual verification.",
                        "Coverage across domains does not establish accuracy or independent verification."]
            if any(s["status"] not in {"ok", "not_configured"} for s in states):
                warnings.append("Partial coverage: a source could not be queried.")
            report = {
                "schema_version": "2.3", "symbol": symbol, "name": asset["name"],
                "status": "ok" if best else "no_relevant_news", "demo": self.settings.demo,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "window_hours": self.settings.max_age_hours, "day_utc": now.date().isoformat(), "language": self.settings.language,
                "cached": False, "selection_method": "rules",
                "ai": {"status": "disabled"}, "best_article": best, "articles": articles, "stats": stats,
                "providers": states, "warnings": warnings,
                "assessment": None, "recommendation": None,
                "billing": {"charged": False},
            }
            fingerprint = {"symbol": symbol, "articles": [
                {k: a[k] for k in ("id", "title", "summary", "published_at", "coverage")}
                for a in articles], "selected_id": best["id"] if best else None,
                "method": report["selection_method"], "demo": self.settings.demo,
                "assessment": report["assessment"]}
            report["report_id"] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:24]
            latest, _ = rank_articles(raw, asset, 168, now, today_only=False)
            for article in latest:
                article["insight"] = build_insight(article, asset)
                public = {k: article[k] for k in ("id", "title", "summary", "url", "source", "published_at", "insight")}
                self.store.cache("article:v1:" + symbol + ":" + article["id"], public)
            with self.store.connect() as db:
                db.execute("DELETE FROM cache WHERE key LIKE 'article:v1:%' AND created<?", (time.time() - 30 * 86400,))
            snapshot = {"report": report, "latest": latest[:40]}
            self.store.cache(key, snapshot)
            return snapshot

    async def report(self, asset):
        return (await self.snapshot(asset))["report"]

    async def preview(self, asset):
        snapshot = await self.snapshot(asset)
        report = snapshot["report"]
        # Strict UTC boundary: no current/future headline, summary or signal in public previews.
        fields = ("id", "title", "source", "published_at")
        today = datetime.now(timezone.utc).date()
        history = [a for a in snapshot["latest"] if parse_date(a["published_at"]).date() < today][:10]
        return {"symbol": asset["symbol"], "name": asset["name"],
                "status": "available" if report["best_article"] else "no_today_news",
                "has_today_news": bool(report["best_article"]), "day_utc": report["day_utc"],
                "checked_at": report["generated_at"], "latest_window_days": 7,
                "partial_sources": any(s["status"] not in {"ok", "not_configured"} for s in report["providers"]),
                "articles": [{**{k: a[k] for k in fields},
                              "importance": a["insight"]["importance"],
                              "recommendation": {"signal": a["insight"]["recommendation"]["signal"]}}
                             for a in history]}

