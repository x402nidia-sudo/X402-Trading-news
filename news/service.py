import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from .catalog import ASSETS
from .ranking import rank_articles, parse_date


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
        key = symbol + ":v8:preview:" + hashlib.sha256(json.dumps(config_signature, sort_keys=True).encode()).hexdigest()[:20]
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
            best = articles[0] if articles else None
            warnings = ["Rule-based priority scores describe news relevance, not BUY/SELL/HOLD signals or price predictions.",
                        "Source priority uses predefined domain weights, not factual verification.",
                        "Coverage across domains does not establish accuracy or independent verification."]
            if any(s["status"] not in {"ok", "not_configured"} for s in states):
                warnings.append("Partial coverage: a source could not be queried.")
            report = {
                "schema_version": "2.2", "symbol": symbol, "name": asset["name"],
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
            snapshot = {"report": report, "latest": latest[:10]}
            self.store.cache(key, snapshot)
            return snapshot

    async def report(self, asset):
        return (await self.snapshot(asset))["report"]

    async def preview(self, asset):
        snapshot = await self.snapshot(asset)
        report = snapshot["report"]
        # Public headlines and source links; explanations and the full ranking remain paid.
        fields = ("id", "title", "url", "source", "published_at")
        return {"symbol": asset["symbol"], "name": asset["name"],
                "status": "available" if report["best_article"] else "no_today_news",
                "has_today_news": bool(report["best_article"]), "day_utc": report["day_utc"],
                "checked_at": report["generated_at"], "latest_window_days": 7,
                "partial_sources": any(s["status"] not in {"ok", "not_configured"} for s in report["providers"]),
                "articles": [{k: a[k] for k in fields} for a in snapshot["latest"]]}

