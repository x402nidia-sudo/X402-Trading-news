import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from .ai import select_with_ai
from .catalog import ASSETS
from .ranking import rank_articles, parse_date


class NoProviders(Exception):
    def __init__(self, states):
        self.states = states
        self.retry_after = min((s["retry_after"] for s in states if s.get("retry_after")), default=60)


def demo_articles(asset):
    now = datetime.now(timezone.utc)
    name, symbol = asset["aliases"][0], asset["symbol"]
    samples = [
        (f"DEMO: {name} blockchain security upgrade after a simulated exploit", "Simulation: protocol security upgrade exercise, not a real incident.", 1, "demo-wire-a.example", "guardian", "security"),
        (f"DEMO: {name} blockchain security upgrade after a simulated exploit", "Example of the same headline received from another provider.", 1, "demo-wire-a.example", "newsapi", "security"),
        (f"DEMO: {name} blockchain security upgrade after a simulated exploit", "Example of similar coverage by another fictional publisher.", 2, "demo-wire-b.example", "gnews", "security"),
        (f"DEMO: {name} token adoption in a fictional payments pilot", "Payment integration simulation. This does not describe a real launch.", .3, "demo-wire-c.example", "gnews", "adoption"),
        (f"DEMO: {name} token price could rise in an imagined scenario", "Fictional prediction demonstrating the speculation penalty.", .1, "demo-wire-d.example", "newsapi", "prediction"),
        ("Weekend gardening guide", "Not related to cryptocurrency or this asset.", 1, "demo-wire-e.example", "newsapi", "unrelated"),
        (f"DEMO: {name} blockchain old upgrade", "Old fictional story used to test the age filter.", 240, "demo-wire-f.example", "guardian", "old"),
    ]
    return [{"title": title, "summary": summary, "published_at": (now - timedelta(hours=hours)).isoformat(),
             "source": "Demo publisher " + domain.split(".")[0][-1].upper(),
             "url": f"https://{domain}/{symbol.lower()}/{slug}", "provider": provider}
            for title, summary, hours, domain, provider, slug in samples]


class NewsService:
    def __init__(self, settings, store, providers, http):
        self.settings, self.store, self.providers, self.http = settings, store, providers, http
        self.locks = {s: asyncio.Lock() for s in ASSETS}

    def cache_key(self, asset):
        symbol = asset["symbol"]
        config_signature = {"demo": self.settings.demo, "hours": self.settings.max_age_hours,
                            "language": self.settings.language, "ai": self.settings.ai_enabled,
                            "model": self.settings.ai_model, "asset": asset,
                            "providers": sorted(k for k, v in self.settings.provider_keys.items() if v),
                            "gdelt": self.settings.gdelt_enabled}
        return symbol + ":v3.1:" + hashlib.sha256(json.dumps(config_signature, sort_keys=True).encode()).hexdigest()[:20]

    def snapshot(self, key, max_age):
        cached = self.store.cached(key, max_age)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.settings.max_age_hours)
        if cached and all((parse_date(a.get("effective_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
                          for a in cached["articles"]):
            result = deepcopy(cached)
            result["cached"] = True
            result["freshness"]["age_seconds"] = max(0, int((datetime.now(timezone.utc) - parse_date(cached["generated_at"])).total_seconds()))
            return result
        return None

    def stale_fallback(self, key, states):
        result = self.snapshot(key, self.settings.stale_max_age_seconds)
        if result and result.get("best_article"):
            result["freshness"]["status"] = "stale"
            result["status"] = "stale_fallback"
            result["billing"] = {"charged": False, "reason": "stale_fallback"}
            result["providers"] = states
            result["warnings"].insert(0, "Could not refresh the report. Showing the last valid report without charging for this query.")
            return result
        return None

    async def prefetch(self):
        """One sequential refresher in the same process; never invokes payments."""
        while True:
            for symbol in self.settings.prefetch_assets:
                try:
                    await self.report(ASSETS[symbol])
                except NoProviders:
                    pass
                except Exception:
                    # Do not log upstream URLs, API keys or payment headers.
                    logging.getLogger(__name__).warning("Could not prefetch %s", symbol)
            await asyncio.sleep(min(60, self.settings.cache_seconds))

    async def report(self, asset):
        symbol, key = asset["symbol"], self.cache_key(asset)
        async with self.locks[symbol]:
            cached = self.snapshot(key, self.settings.cache_seconds)
            if cached:
                return cached
            if self.settings.demo:
                raw = demo_articles(asset)
                states = [{"provider": p, "status": "demo", "count": sum(a["provider"] == p for a in raw)}
                          for p in ("guardian", "newsapi", "gnews")]
            else:
                raw, states = await self.providers.all(asset)
                if not any(s["status"] == "ok" for s in states):
                    fallback = self.stale_fallback(key, states)
                    if fallback:
                        return fallback
                    raise NoProviders(states)
            articles, stats = rank_articles(raw, asset, self.settings.max_age_hours)
            if not articles and any(s["status"] not in {"ok", "demo", "disabled", "not_configured"} for s in states):
                fallback = self.stale_fallback(key, states)
                if fallback:
                    return fallback
            chosen_id, ai = (None, {"status": "demo_rules_only"}) if self.settings.demo else await select_with_ai(
                articles, asset, self.settings, self.store, self.http)
            if chosen_id:
                articles.sort(key=lambda a: a["id"] != chosen_id)
            best = articles[0] if articles and ai["status"] != "abstained" else None
            warnings = ["Editorial score, not a probability or a buy/sell signal.",
                        "Domain coverage does not prove accuracy or source independence."]
            if self.settings.demo:
                warnings.insert(0, "DEMO: all headlines are fictional; they are not market news.")
            elif any(s["status"] not in {"ok", "not_configured", "disabled"} for s in states):
                warnings.append("Partial coverage: at least one source could not be queried.")
            if self.settings.ai_enabled and ai.get("fallback"):
                warnings.append("AI unavailable: rule-based ranking was used.")
            if any("gdelt" in a["providers"] for a in articles):
                warnings.append("GDELT provides headlines and links. Its timestamp records detection, not verified publication; the full article has not been read.")
            report = {
                "schema_version": "2.1", "symbol": symbol, "name": asset["name"],
                "status": "ok" if best else "no_relevant_news", "demo": self.settings.demo,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "window_hours": self.settings.max_age_hours, "language": self.settings.language,
                "freshness": {"status": "fresh", "age_seconds": 0,
                              "cache_seconds": self.settings.cache_seconds,
                              "stale_max_age_seconds": self.settings.stale_max_age_seconds},
                "attribution": [{"name": "The GDELT Project", "url": "https://www.gdeltproject.org/"}]
                               if any("gdelt" in a["providers"] for a in articles) else [],
                "cached": False, "selection_method": "semantic_and_rules" if chosen_id else "rules",
                "ai": ai, "best_article": best, "articles": articles, "stats": stats,
                "providers": states, "warnings": warnings,
                "recommendation": "NOT_A_TRADING_SIGNAL",
                "billing": {"charged": False},
            }
            fingerprint = {"symbol": symbol, "articles": [
                {k: a[k] for k in ("id", "title", "summary", "published_at", "observed_at", "date_basis", "coverage")}
                for a in articles], "selected_id": best["id"] if best else None,
                "method": report["selection_method"], "demo": self.settings.demo}
            report["report_id"] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:24]
            self.store.cache(key, report)
            return report
