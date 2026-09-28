import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from .ai import select_with_ai
from .catalog import ASSETS
from .ranking import rank_articles, parse_date


class NoProviders(Exception):
    def __init__(self, states):
        self.states = states


class NewsService:
    def __init__(self, settings, store, providers, http):
        self.settings, self.store, self.providers, self.http = settings, store, providers, http
        self.locks = {s: asyncio.Lock() for s in ASSETS}

    async def report(self, asset):
        symbol = asset["symbol"]
        config_signature = {"demo": self.settings.demo, "hours": self.settings.max_age_hours,
                            "language": self.settings.language, "ai": self.settings.ai_enabled,
                            "model": self.settings.ai_model, "asset": asset,
                            "providers": sorted(k for k, v in self.settings.provider_keys.items() if v)}
        key = symbol + ":v5:" + hashlib.sha256(json.dumps(config_signature, sort_keys=True).encode()).hexdigest()[:20]
        async with self.locks[symbol]:
            cached = self.store.cached(key, self.settings.cache_seconds)
            now = datetime.now(timezone.utc)
            cutoff = max(now - timedelta(hours=self.settings.max_age_hours), now.replace(hour=0, minute=0, second=0, microsecond=0))
            if cached and cached.get("day_utc") == now.date().isoformat() and all(parse_date(a["published_at"]) >= cutoff for a in cached["articles"]):
                result = deepcopy(cached)
                result["cached"] = True
                return result
            raw, states = await self.providers.all(asset)
            if not any(s["status"] == "ok" for s in states):
                raise NoProviders(states)
            articles, stats = rank_articles(raw, asset, self.settings.max_age_hours)
            chosen_id, ai = await select_with_ai(
                articles, asset, self.settings, self.store, self.http)
            if chosen_id:
                articles.sort(key=lambda a: a["id"] != chosen_id)
            best = articles[0] if articles and ai["status"] != "abstained" else None
            warnings = ["Editorial priority, not a probability or a buy/sell signal.",
                        "Coverage across domains does not establish accuracy or independent verification."]
            if any(s["status"] not in {"ok", "not_configured"} for s in states):
                warnings.append("Partial coverage: a source could not be queried.")
            if self.settings.ai_enabled and ai.get("fallback"):
                warnings.append("AI unavailable: explainable ranking rules were used.")
            report = {
                "schema_version": "2.0", "symbol": symbol, "name": asset["name"],
                "status": "ok" if best else "no_relevant_news", "demo": self.settings.demo,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "window_hours": self.settings.max_age_hours, "day_utc": now.date().isoformat(), "language": self.settings.language,
                "cached": False, "selection_method": "semantic_and_rules" if chosen_id else "rules",
                "ai": ai, "best_article": best, "articles": articles, "stats": stats,
                "providers": states, "warnings": warnings,
                "recommendation": "NOT_A_TRADING_SIGNAL",
                "billing": {"charged": False},
            }
            fingerprint = {"symbol": symbol, "articles": [
                {k: a[k] for k in ("id", "title", "summary", "published_at", "coverage")}
                for a in articles], "selected_id": best["id"] if best else None,
                "method": report["selection_method"], "demo": self.settings.demo}
            report["report_id"] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:24]
            self.store.cache(key, report)
            return report
