"""Trading News API. Retains all 49 registered market-signal resource URLs."""
from contextlib import asynccontextmanager, suppress
import asyncio
from typing import Literal
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
import os
import json
import re
from datetime import datetime, timezone
from html import escape
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field
from news.catalog import ASSETS, get_asset
from news.config import Settings
from news.payments import PaymentGateway, decode_payment
from news.providers import Providers
from news.service import NewsService, NoProviders
from news.storage import Store
from news.checkout import Checkout
from news.alerts import Alerts
from news.insights import html_page, CSP
from news.ranking import parse_date


class CheckoutRequest(BaseModel):
    address: str = Field(min_length=58, max_length=58)


class AlertRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    symbol: str = Field(min_length=1, max_length=12)
    language: Literal["en", "es", "fr", "de"] = "en"


class AlertToken(BaseModel):
    token: str = Field(min_length=40, max_length=100)


def create_app(settings=None, transport=None):
    cfg = settings or Settings.from_env()
    cfg = replace(cfg, demo=False, max_age_hours=min(cfg.max_age_hours, 24))
    cfg.validate()
    if os.getenv("RENDER") and cfg.payments:
        if not Path(cfg.db_path).resolve().is_relative_to("/var/data") or not os.path.ismount("/var/data"):
            raise ValueError("Payments require a persistent Render disk at /var/data; set DATABASE_PATH=/var/data/news.sqlite3.")
    origins = [s.strip().rstrip("/") for s in os.getenv("WEB_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080").split(",") if s.strip()]
    if "*" in origins:
        raise ValueError("WEB_ORIGINS must contain exact website origins, not *.")
    origins = list(dict.fromkeys(origins + [cfg.public_url]))

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=15,
                                     limits=httpx.Limits(max_connections=12),
                                     headers={"User-Agent": "TradingNews/5.5.0"}) as http:
            store = Store(cfg.db_path)
            app.state.store = store
            app.state.news = NewsService(cfg, store, Providers(cfg, store, http), http)
            app.state.payments = PaymentGateway(cfg, store, http)
            app.state.checkout = Checkout(cfg, http, app.state.payments)
            app.state.alerts = Alerts(cfg, store, app.state.news)
            worker = asyncio.create_task(app.state.alerts.run()) if app.state.alerts.enabled else None
            try:
                yield
            finally:
                app.state.alerts.stop.set()
                if worker:
                    try:
                        await asyncio.wait_for(asyncio.shield(worker), timeout=20)
                    except TimeoutError:
                        worker.cancel()
                        with suppress(asyncio.CancelledError):
                            await worker

    app = FastAPI(title="Trading News", version="5.5.0", lifespan=lifespan, docs_url=None,
                  description=f"One asset report for {cfg.price_usdc} USDC via x402 v2 on Algorand. Use /api/v1/market-signal/{{symbol}}. Today's news ordered by explainable rules: asset relevance, recency, source priority, event and coverage. Includes scores, source links and dates. Includes indicative per-story BUY/SELL/HOLD impact signals based on explicit rules, without OpenAI. Free history excludes today. Report days use UTC.")
    app.state.settings = cfg
    rate = OrderedDict()

    @app.middleware("http")
    async def headers(request, call_next):
        if request.url.path.startswith(("/api/", "/news/")) and request.method != "OPTIONS":
            key = request.client.host if request.client else "unknown"
            now = time.monotonic()
            start, count = rate.get(key, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            if count >= cfg.request_limit:
                return JSONResponse({"detail": "RATE_LIMITED"}, status_code=429, headers={"Retry-After": "60"})
            rate[key] = (start, count + 1)
            rate.move_to_end(key)
            if len(rate) > 10000:
                rate.popitem(last=False)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Added last to wrap rate/error responses too. No cookies or shared secrets in the browser.
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "PAYMENT-SIGNATURE", "X-PAYMENT"],
                       expose_headers=["PAYMENT-REQUIRED", "PAYMENT-RESPONSE", "Retry-After"])

    @app.exception_handler(NoProviders)
    async def unavailable(request, exc):
        return JSONResponse({"detail": "SOURCES_UNAVAILABLE", "providers": exc.states,
                             "billing": {"charged": False}}, status_code=503)

    def asset_for(symbol):
        try:
            return get_asset(symbol)
        except KeyError:
            raise HTTPException(404, "UNSUPPORTED_ASSET") from None

    def resource(symbol):
        return cfg.public_url + "/api/v1/market-signal/" + symbol

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "5.5.0", "payments_enabled": cfg.payments}

    @app.get("/api/v1/config")
    async def config():
        return {"payments_enabled": cfg.payments, "price_usdc": cfg.price_usdc, "price_atomic": cfg.amount,
                "network": cfg.network_name, "network_caip": cfg.network,
                "asset_id": cfg.asset, "pay_to": cfg.pay_to, "api_url": cfg.public_url,
                "report_path": "/api/v1/market-signal/{symbol}", "day_timezone": "UTC",
                "providers": [{"name": p, "configured": bool(cfg.provider_keys.get(p))}
                              for p in ("newsapi", "gnews")], "ai_enabled": False,
                "selection_method": "rules", "email_alerts_enabled": app.state.alerts.enabled,
                "alert_interval_minutes": cfg.alert_interval_hours * 60}

    @app.get("/api/v1/assets")
    async def assets():
        return {"assets": list(ASSETS.values())}

    @app.get("/api/v1/news/{symbol}")
    async def preview(symbol: str):
        """Free availability check and recent headlines; does not initiate a payment."""
        return await app.state.news.preview(asset_for(symbol))

    @app.get("/api/v1/history")
    async def history(symbol: str = "ALL", importance: Literal["all", "high", "medium", "low"] = "all"):
        """Previously collected news from the last 7 days, strictly excluding today (UTC)."""
        asset = None if symbol == "ALL" else asset_for(symbol)
        return await app.state.news.history(asset, importance)

    @app.get("/news/{symbol}/{article_id}", response_class=HTMLResponse, include_in_schema=False)
    async def news_card(symbol: str, article_id: str, request: Request, lang: str = "en"):
        asset = asset_for(symbol)
        def page(article=None, status=200, error="unavailable"):
            return HTMLResponse(html_page(asset, lang, cfg.web_url, article, error), status_code=status,
                                headers={"Content-Security-Policy": CSP})
        if not re.fullmatch(r"[a-f0-9]{20}", article_id):
            return page(status=404)
        token = request.headers.get("payment-signature") or request.headers.get("x-payment")
        if token:
            # Read only: this route must never verify/settle a new payment or call providers.
            _, fingerprint, proof = decode_payment(token)
            purchased = app.state.payments.existing(fingerprint, resource(asset["symbol"]), proof)
            if purchased is None:
                return page(status=402, error="paywall")
            report = json.loads(purchased.body)
            article = next((a for a in report.get("articles", []) if a["id"] == article_id), None)
            return page(article) if article else page(status=404)
        article = app.state.store.cached("article:v1:" + asset["symbol"] + ":" + article_id, 30 * 86400)
        if not article:
            return page(status=404)
        published = parse_date(article.get("published_at"))
        if not published or published.date() >= datetime.now(timezone.utc).date():
            return page(status=402, error="paywall")
        return page(article)

    def check_origin(request):
        if request.headers.get("origin") and request.headers["origin"] not in origins:
            raise HTTPException(403, "ORIGIN_NOT_ALLOWED")

    @app.post("/api/v1/alerts/subscribe", status_code=202)
    async def subscribe(body: AlertRequest, request: Request):
        check_origin(request)
        symbol = "ALL" if body.symbol == "ALL" else asset_for(body.symbol)["symbol"]
        return await app.state.alerts.subscribe(body.email, symbol, body.language,
                                                request.client.host if request.client else "unknown")

    @app.post("/api/v1/alerts/confirm")
    async def confirm_alert(body: AlertToken, request: Request):
        check_origin(request)
        return app.state.alerts.confirm(body.token)

    @app.post("/api/v1/alerts/test", status_code=202)
    async def test_alert(body: AlertToken, request: Request):
        check_origin(request)
        return await app.state.alerts.test_email(body.token)

    @app.post("/api/v1/alerts/unsubscribe")
    async def unsubscribe_alert(body: AlertToken, request: Request):
        check_origin(request)
        return app.state.alerts.unsubscribe(body.token)

    @app.get("/alerts/{action}", include_in_schema=False)
    async def alert_page(action: Literal["confirm", "unsubscribe"], lang: str = "en"):
        return app.state.alerts.page(action, lang)

    @app.post("/api/v1/checkout/{symbol}")
    async def checkout(symbol: str, body: CheckoutRequest, request: Request):
        a = asset_for(symbol)
        if not cfg.payments:
            raise HTTPException(503, "PURCHASES_DISABLED")
        if request.headers.get("origin") and request.headers["origin"] not in origins:
            raise HTTPException(403, "ORIGIN_NOT_ALLOWED")
        # Check that useful, current content is available before asking for a signature.
        report = await app.state.news.report(a)
        if not report.get("best_article"):
            raise HTTPException(503, "NO_TODAY_NEWS")
        return await app.state.checkout.prepare(body.address, resource(a["symbol"]))

    @app.get("/api/v1/market-signal/{symbol}")
    async def report(symbol: str, request: Request):
        """402 -> sign the advertised Algorand payment -> repeat with PAYMENT-SIGNATURE.

        Returns the complete ranked report and receipt. Reusing the exact signed
        payload retrieves the same purchased report without another settlement.
        """
        a = asset_for(symbol)
        if request.query_params:
            raise HTTPException(400, "Use the symbol in the path; query parameters are not supported.")
        token = request.headers.get("payment-signature") or request.headers.get("x-payment")
        return await app.state.payments.access(token, resource(a["symbol"]), lambda: app.state.news.report(a))

    @app.get("/.well-known/x402.json")
    async def manifest():
        resources = [resource(s) for s in ASSETS]
        if not cfg.payments:
            return {"name": "Trading News", "payments_enabled": False, "resources": resources}
        challenge = app.state.payments.challenge(await app.state.payments.requirement(), resources[0])
        return {**challenge, "name": "Trading News", "resources": resources,
                "documentation": cfg.public_url + "/docs"}

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    async def docs(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        return get_swagger_ui_html(openapi_url=root_path + app.openapi_url, title="Trading News",
                                   swagger_ui_parameters=app.swagger_ui_parameters)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index():
        description = "News for each asset, ranked by relevance. With sources, dates and an explanation of the selection."
        return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading News</title>
<meta name="description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Trading News">
<meta property="og:title" content="Trading News">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{escape(cfg.public_url, quote=True)}/">
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fc;color:#142d4b;font-family:system-ui,sans-serif}}
main{{max-width:760px;margin:12vh auto;padding:clamp(24px,5vw,48px);background:#fff;border:1px solid #d4e2f5;border-top:4px solid #2878d5;border-radius:18px}}
h1{{font-size:clamp(36px,7vw,54px);margin:0 0 18px}}p{{color:#506a89;line-height:1.7}}
nav{{display:flex;gap:14px;flex-wrap:wrap;margin-top:30px}}a{{padding:12px 20px;border-radius:8px;color:#fff;background:#1b5eaa;text-decoration:none;font-weight:600}}
a.secondary{{background:#e9f1fc;color:#1b5eaa}}a:focus-visible{{outline:3px solid #142d4b;outline-offset:3px}}
</style></head><body><main><h1>Trading News</h1><p>{description}</p>
<nav aria-label="Trading News"><a href="{escape(cfg.web_url, quote=True)}">Open Trading News</a>
<a class="secondary" href="{escape(cfg.public_url, quote=True)}/docs">API for agents</a></nav>
</main></body></html>""", headers={"Content-Security-Policy": CSP})

    return app


def production_app():
    return create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
