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
    def __init__(self, settings, store, providers, http, channel="web"):
        self.settings, self.store, self.providers, self.http = settings, store, providers, http
        self.channel = channel
        self.archive_prefix = "article:rss:" if channel == "api" else "article:v1:"
        self.locks = {s: asyncio.Lock() for s in ASSETS}

    async def snapshot(self, asset):
        symbol = asset["symbol"]
        config_signature = {"demo": self.settings.demo, "hours": self.settings.max_age_hours,
                            "language": self.settings.language, "asset": asset,
                            "providers": getattr(self.providers, "cache_identity", sorted(k for k, v in self.settings.provider_keys.items() if v)),
                            "channel": self.channel}
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
                self.store.cache(self.archive_prefix + symbol + ":" + article["id"], public)
            with self.store.connect() as db:
                db.execute("DELETE FROM cache WHERE key LIKE ? AND created<?", (self.archive_prefix + "%", time.time() - 30 * 86400))
            snapshot = {"report": report, "latest": latest[:40]}
            self.store.cache(key, snapshot)
            return snapshot

    @staticmethod
    def filter_report(report, importance="all"):
        if importance == "all":
            return report
        if importance not in {"high", "medium", "low"}:
            raise ValueError("Invalid importance")
        result = deepcopy(report)
        result["articles"] = [a for a in result["articles"]
                              if a.get("insight", {}).get("importance", {}).get("level") == importance]
        result["best_article"] = next(iter(result["articles"]), None)
        result["status"] = "ok" if result["best_article"] else "no_relevant_news"
        result["importance"] = importance
        result["stats"]["unique"] = len(result["articles"])
        result["report_id"] = hashlib.sha256((report["report_id"] + ":" + importance).encode()).hexdigest()[:24]
        return result

    def latest_stored(self, symbol, importance="all"):
        """Most recent archived story for the asset (kept 30 days), whatever its day."""
        with self.store.connect() as db:
            rows = db.execute("SELECT body FROM cache WHERE key LIKE ?", (self.archive_prefix + symbol + ":%",)).fetchall()
        stories = []
        for row in rows:
            try:
                article = json.loads(row["body"])
                if importance in {"all", article["insight"]["importance"]["level"]}:
                    stories.append((parse_date(article["published_at"]).timestamp(), article["id"], article))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        return max(stories, key=lambda s: s[:2])[2] if stories else None

    async def report(self, asset, importance="all", latest=False):
        try:
            report = self.filter_report((await self.snapshot(asset))["report"], importance)
        except NoProviders as exc:
            if self.channel != "api":
                raise
            now = datetime.now(timezone.utc)
            report = {"schema_version": "2.4", "symbol": asset["symbol"], "name": asset["name"],
                      "status": "sources_unavailable", "day_utc": now.date().isoformat(),
                      "generated_at": now.isoformat(), "best_article": None, "articles": [],
                      "providers": exc.states, "stats": {"unique": 0}, "warnings": [],
                      "assessment": None, "recommendation": None, "billing": {"charged": False}}
        story = self.latest_stored(asset["symbol"], importance) if latest and not report["articles"] else None
        if story:
            # Nothing today (or sources down): return the most recent stored story, labelled as such.
            report = {**report, "status": "latest_available", "best_article": story, "articles": [story],
                      "stats": {**report["stats"], "unique": 1},
                      "warnings": report["warnings"] + [f"No story for {asset['symbol']} today (UTC); this is the most recent stored one."],
                      "report_id": hashlib.sha256(":".join((asset["symbol"], "latest", importance, story["id"])).encode()).hexdigest()[:24]}
        if self.channel == "api":
            report = deepcopy(report)
            report.update(schema_version="2.4", channel="api", news_found=bool(report["articles"]),
                          is_template=not bool(report["articles"]), importance=importance,
                          language="source", template=None)
            if not report["articles"]:
                unavailable = report["status"] == "sources_unavailable"
                partial = any(s["status"] != "ok" for s in report["providers"])
                message = ("No se pudieron consultar las fuentes de noticias" if unavailable else
                           "No se encontraron noticias en las fuentes consultadas")
                message += f" para {asset['name']} ({asset['symbol']}) el {report['day_utc']} (UTC)."
                if importance != "all":
                    message += f" Filtro de importancia: {importance}."
                if partial and not unavailable:
                    message += " La cobertura es parcial porque alguna fuente no respondió."
                report["template"] = {"is_template": True, "title": "Fuentes no disponibles" if unavailable else "Sin noticias",
                                      "summary": message, "symbol": asset["symbol"], "date": report["day_utc"],
                                      "reason": report["status"], "url": None, "published_at": None}
                report["report_id"] = hashlib.sha256(json.dumps(report["template"], sort_keys=True).encode()).hexdigest()[:24]
        return report

    async def history(self, asset=None, importance="all"):
        """Read public, archived stories; today's content is never returned here.

        A selected coin can refresh its own archive. ALL reads the collected
        archive without spending provider quotas on 49 simultaneous searches.
        """
        failure = None
        partial = False
        if asset:
            try:
                snapshot = await self.snapshot(asset)
                partial = any(s["status"] not in {"ok", "not_configured"}
                              for s in snapshot["report"]["providers"])
            except NoProviders as exc:
                failure, partial = exc, True
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = now - timedelta(days=7)
        pattern = self.archive_prefix + (asset["symbol"] + ":%" if asset else "%")
        with self.store.connect() as db:
            rows = db.execute("SELECT key,body FROM cache WHERE key LIKE ? AND created>=?",
                              (pattern, time.time() - 30 * 86400)).fetchall()
        articles = []
        for row in rows:
            try:
                symbol = row["key"].split(":")[2]
                if symbol not in ASSETS:
                    continue
                article = json.loads(row["body"])
                published = parse_date(article.get("published_at"))
                if published is None or not cutoff <= published < midnight:
                    continue
                insight = article["insight"]
                level = insight["importance"]["level"]
                if importance != "all" and level != importance:
                    continue
                public = {k: article[k] for k in ("id", "title", "source", "published_at")}
                public.update({"symbol": symbol, "name": ASSETS[symbol]["name"],
                               "importance": {"level": level},
                               "recommendation": {"signal": insight["recommendation"]["signal"]}})
                articles.append((published, public))
            except (KeyError, TypeError, ValueError):
                continue
        if failure and not articles:
            raise failure
        articles.sort(key=lambda pair: (-pair[0].timestamp(), pair[1]["symbol"], pair[1]["id"]))
        return {"symbol": asset["symbol"] if asset else "ALL", "importance": importance,
                "name": asset["name"] if asset else None, "day_utc": now.date().isoformat(),
                "checked_at": now.isoformat(), "latest_window_days": 7,
                "partial_sources": partial, "scope": "collected_archive",
                "has_more": len(articles) > 200, "limit": 200,
                "articles": [a for _, a in articles[:200]]}

    async def preview(self, asset, importance="all"):
        snapshot = await self.snapshot(asset)
        report = self.filter_report(snapshot["report"], importance)
        # Strict UTC boundary: no current/future headline, summary or signal in public previews.
        fields = ("id", "title", "source", "published_at")
        today = datetime.now(timezone.utc).date()
        history = [a for a in snapshot["latest"] if parse_date(a["published_at"]).date() < today][:10]
        return {"symbol": asset["symbol"], "name": asset["name"], "importance": importance,
                "status": "available" if report["best_article"] else "no_today_news",
                "has_today_news": bool(report["best_article"]), "day_utc": report["day_utc"],
                "checked_at": report["generated_at"], "latest_window_days": 7,
                "partial_sources": any(s["status"] not in {"ok", "not_configured"} for s in report["providers"]),
                "articles": [{**{k: a[k] for k in fields},
                              "importance": a["insight"]["importance"],
                              "recommendation": {"signal": a["insight"]["recommendation"]["signal"]}}
                             for a in history]}
