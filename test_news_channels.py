"""Run: python -m unittest -v test_news_channels (no network, payments or email)."""
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from main import create_app
from news.catalog import ASSETS
from news.config import Settings
from news.payments import PaymentGateway
from news.providers import Providers
from news.ranking import parse_date
from news.rss import RSSProviders, parse_feed
from news.service import NewsService
from news.storage import Store


def feed(title="Bitcoin ETF approval", date=None):
    date = date or datetime.now(timezone.utc)
    return (f'<rss version="2.0"><channel><item><title>{title}</title>'
            f'<link>https://www.coindesk.com/news/test</link><pubDate>{format_datetime(date)}</pubDate>'
            '<description>Bitcoin ETF approval reported.</description></item></channel></rss>').encode()


class Sources(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Settings(db_path=self.tmp.name + "/news.sqlite3", provider_keys={"newsapi": "test", "gnews": "test"})
        self.store = Store(self.cfg.db_path)
        self.calls = []
        self.content, self.status = feed(), 200
        def handle(req):
            self.calls.append(str(req.url))
            if req.url.host == "www.coindesk.com":
                return httpx.Response(self.status, content=self.content, headers={"Retry-After": "120"})
            if req.url.host in {"newsapi.org", "gnews.io"}:
                return httpx.Response(200, json={"status": "ok", "articles": []})
            raise AssertionError("Unexpected network request")
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.rss = RSSProviders(self.cfg, self.store, self.http)
        self.api = NewsService(self.cfg, self.store, self.rss, self.http, channel="api")

    async def asyncTearDown(self):
        await self.http.aclose()
        self.tmp.cleanup()

    async def test_rss_shared_cache_and_no_paid_provider(self):
        btc, eth = await asyncio.gather(self.api.report(ASSETS["BTC"]), self.api.report(ASSETS["ETH"]))
        self.assertTrue(btc["news_found"])
        self.assertFalse(btc["is_template"])
        self.assertTrue(eth["is_template"])
        self.assertIn("ETH", eth["template"]["summary"])
        self.assertEqual(len(self.calls), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0], 0)

    async def test_old_news_and_importance_produce_templates(self):
        self.content = feed(date=datetime.now(timezone.utc) - timedelta(days=1))
        report = await self.api.report(ASSETS["BTC"], "low")
        self.assertTrue(report["is_template"])
        self.assertIsNone(report["best_article"])
        self.assertIsNone(report["template"]["published_at"])
        self.assertEqual(report["template"]["date"], datetime.now(timezone.utc).date().isoformat())
        self.assertIn("low", report["template"]["summary"])

    async def test_outage_is_not_claimed_as_no_news(self):
        self.status = 429
        report = await self.api.report(ASSETS["BTC"])
        self.assertEqual(report["template"]["reason"], "sources_unavailable")
        self.assertFalse(report["billing"]["charged"])
        await self.api.report(ASSETS["ETH"])
        self.assertEqual(len(self.calls), 1)

    async def test_web_and_api_caches_are_separate(self):
        await self.api.report(ASSETS["BTC"])
        web = NewsService(self.cfg, self.store, Providers(self.cfg, self.store, self.http), self.http)
        report = await web.report(ASSETS["BTC"])
        self.assertIsNone(report["best_article"])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(any("newsapi.org" in url for url in self.calls))
        self.assertTrue(any("gnews.io" in url for url in self.calls))

    def test_parser_rejects_entities_html_and_accepts_atom(self):
        for value in (b'<!DOCTYPE rss [<!ENTITY x "secret">]><rss/>', b'<html/>'):
            with self.assertRaises(ValueError):
                parse_feed(value, "source")
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Bitcoin</title><link href="https://example.com/a"/><published>2026-10-01T00:00:00Z</published></entry></feed>'
        self.assertEqual(parse_feed(atom, "source")[0]["url"], "https://example.com/a")

    async def test_same_host_redirect_is_followed_other_host_is_not(self):
        for location, expected, requests in (("/arc/outboundfeeds/rss", "ok", 2),
                                             ("http://www.coindesk.com/arc/outboundfeeds/rss", "ok", 2),
                                             ("https://example.com/feed", "http_error", 1)):
            calls = []
            def handle(req):
                calls.append(str(req.url))
                if req.url.path.endswith("/"):
                    return httpx.Response(308, headers={"Location": location})
                return httpx.Response(200, content=feed())
            with tempfile.TemporaryDirectory() as tmp:
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                    items, state = await RSSProviders(self.cfg, Store(tmp + "/db"), http).fetch(self.cfg.rss_feed_urls[0])
            self.assertEqual((state["status"], len(items), len(calls)), (expected, int(expected == "ok"), requests))
            self.assertTrue(all(call.startswith("https://www.coindesk.com/") for call in calls))
            self.assertEqual(state.get("location"), None if expected == "ok" else location)

    async def test_latest_returns_most_recent_stored_story(self):
        old = datetime.now(timezone.utc) - timedelta(days=1)
        self.content = feed(date=old)
        self.assertTrue((await self.api.report(ASSETS["BTC"]))["is_template"])  # alerts/preview unchanged
        report = await self.api.report(ASSETS["BTC"], latest=True)
        self.assertEqual((report["status"], report["is_template"], report["news_found"]), ("latest_available", False, True))
        self.assertEqual(parse_date(report["best_article"]["published_at"]).date(), old.date())
        self.assertTrue((await self.api.report(ASSETS["BTC"], "low", latest=True))["is_template"])
        self.assertTrue((await self.api.report(ASSETS["ETH"], latest=True))["is_template"])
        # Feed down later: the stored story is still served.
        with self.store.connect() as db:
            db.execute("DELETE FROM cache WHERE key NOT LIKE 'article:%'")
        self.status = 503
        report = await self.api.report(ASSETS["BTC"], latest=True)
        self.assertEqual(report["best_article"]["title"], "Bitcoin ETF approval")
        self.assertTrue(report["report_id"])

    async def test_payment_template_never_settles_and_paid_replays(self):
        self.cfg.payments = True
        requirement = {"amount": "200000"}
        url = self.cfg.public_url + "/api/v1/market-signal/BTC"
        payload = {"accepted": requirement, "resource": {"url": url}}
        calls = []
        txid = "A" * 52
        def payment_http(req):
            calls.append(req.url.path)
            if req.url.path == "/verify":
                return httpx.Response(200, json={"isValid": True})
            if req.url.path == "/settle":
                return httpx.Response(200, json={"success": True, "network": self.cfg.network, "transaction": txid})
            raise AssertionError(req.url)
        async with httpx.AsyncClient(transport=httpx.MockTransport(payment_http)) as client:
            gate = PaymentGateway(self.cfg, self.store, client)
            gate.requirement = AsyncMock(return_value=requirement)
            gate.challenge = Mock(return_value={"extensions": {}})
            body = AsyncMock(return_value={"best_article": None, "articles": [], "is_template": True, "template": {"title": "Sin noticias"}})
            with patch("news.payments.decode_payment", return_value=(payload, "fingerprint", "proof")), patch("news.payments.validate_transfer", return_value=(Mock(), [txid])):
                response = await gate.access("test", url, body, allow_template=True)
                self.assertFalse(json.loads(response.body)["billing"]["charged"])
                self.assertEqual(calls, ["/verify"])
                self.assertIsNone(self.store.payment("fingerprint"))
                with self.assertRaises(HTTPException) as exc:
                    await gate.access("test", url, body)
                self.assertEqual(exc.exception.detail, "NO_TODAY_NEWS")
                body.return_value = {"best_article": {"id": "1"}, "articles": [{"id": "1"}]}
                await gate.access("test", url, body, allow_template=True)
                response = await gate.access("test", url, body, allow_template=True)
                self.assertTrue(json.loads(response.body)["billing"]["replayed"])
                self.assertEqual(calls.count("/settle"), 1)
                with self.assertRaises(HTTPException):
                    await gate.access("test", url.replace("/api/v1/", "/api/web/v1/"), body)


class Routes(unittest.TestCase):
    def test_routing_preview_and_402_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=tmp + "/db", payments=True,
                           pay_to="DIPQL34NWQTXO6ZNLNYOYILJZ7UBLMY5KMTJSOCISGYDSPV2PR6VS6UP2Q",
                           provider_keys={"newsapi": "test", "gnews": "test"})
            calls = []
            def handler(req):
                calls.append(req.url.host)
                if req.url.host == "www.coindesk.com":
                    return httpx.Response(200, content=b'<rss><channel/></rss>')
                if req.url.host in {"newsapi.org", "gnews.io"}:
                    return httpx.Response(200, json={"status": "ok", "articles": []})
                raise AssertionError(req.url)
            with patch.dict("os.environ", {"RENDER": ""}), TestClient(create_app(cfg, httpx.MockTransport(handler))) as client:
                app = client.app
                app.state.payments.requirement = AsyncMock(return_value={})
                app.state.payments.challenge = Mock(side_effect=lambda req, url: {"resource": {"url": url}})
                for prefix in ("/api/v1", "/api/web/v1"):
                    r = client.get(prefix + "/market-signal/BTC")
                    self.assertEqual(r.status_code, 402)
                    self.assertIn(prefix, r.json()["resource"]["url"])
                self.assertEqual(calls, [])
                r = client.get("/api/v1/news/BTC")
                self.assertTrue(r.json()["is_template"])
                self.assertEqual(calls, ["www.coindesk.com"])
                client.get("/api/web/v1/news/BTC")
                self.assertEqual(set(calls), {"www.coindesk.com", "newsapi.org", "gnews.io"})
                r = client.post("/api/v1/checkout/BTC", json={"address": cfg.pay_to})
                self.assertFalse(r.json()["billing"]["charged"])
                r = client.post("/api/web/v1/checkout/BTC", json={"address": cfg.pay_to})
                self.assertEqual(r.status_code, 503)
                self.assertIs(app.state.alerts.news, app.state.news)
                self.assertEqual(client.get("/api/v1/market-signal/INVALID").status_code, 404)


    def test_paid_api_route_charges_latest_story_web_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=tmp + "/db", payments=True,
                           pay_to="DIPQL34NWQTXO6ZNLNYOYILJZ7UBLMY5KMTJSOCISGYDSPV2PR6VS6UP2Q",
                           provider_keys={"newsapi": "test", "gnews": "test"})
            txid, calls = "A" * 52, []
            def handler(req):
                calls.append(req.url.path)
                if req.url.host == "www.coindesk.com":
                    return httpx.Response(200, content=feed(date=datetime.now(timezone.utc) - timedelta(days=1)))
                if req.url.host in {"newsapi.org", "gnews.io"}:
                    return httpx.Response(200, json={"status": "ok", "articles": []})
                if req.url.path == "/verify":
                    return httpx.Response(200, json={"isValid": True})
                if req.url.path == "/settle":
                    return httpx.Response(200, json={"success": True, "network": cfg.network, "transaction": txid})
                raise AssertionError(req.url)
            with patch.dict("os.environ", {"RENDER": ""}), TestClient(create_app(cfg, httpx.MockTransport(handler))) as client:
                app = client.app
                app.state.payments.requirement = AsyncMock(return_value={})
                app.state.payments.challenge = Mock(return_value={"extensions": {}})
                app.state.checkout.prepare = AsyncMock(return_value={"prepared": True})
                for prefix, fingerprint in (("/api/v1", "api"), ("/api/web/v1", "web")):
                    url = cfg.public_url + prefix + "/market-signal/BTC"
                    payload = {"accepted": {}, "resource": {"url": url}}
                    with patch("news.payments.decode_payment", return_value=(payload, fingerprint, "proof")), patch("news.payments.validate_transfer", return_value=(Mock(), [txid])):
                        r = client.get(prefix + "/market-signal/BTC", headers={"PAYMENT-SIGNATURE": "test"})
                    if prefix == "/api/v1":
                        self.assertEqual((r.status_code, r.json()["billing"]["charged"], r.json()["status"]), (200, True, "latest_available"))
                    else:
                        self.assertEqual((r.status_code, r.json()["detail"]), (503, "NO_TODAY_NEWS"))
                self.assertEqual(calls.count("/settle"), 1)
                self.assertTrue(client.get("/api/v1/news/BTC").json()["is_template"])  # free preview never shows it
                self.assertEqual(client.post("/api/v1/checkout/BTC", json={"address": cfg.pay_to}).json(), {"prepared": True})


if __name__ == "__main__":
    unittest.main()
