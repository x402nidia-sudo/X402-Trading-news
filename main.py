"""Trading News API. Retains all 49 registered market-signal resource URLs."""
from contextlib import asynccontextmanager
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
import os
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from news.catalog import ASSETS, get_asset
from news.config import Settings
from news.payments import PaymentGateway
from news.providers import Providers
from news.service import NewsService, NoProviders
from news.storage import Store
from news.checkout import Checkout


class CheckoutRequest(BaseModel):
    address: str = Field(min_length=58, max_length=58)


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
                                     headers={"User-Agent": "TradingNews/5.2"}) as http:
            store = Store(cfg.db_path)
            app.state.store = store
            app.state.news = NewsService(cfg, store, Providers(cfg, store, http), http)
            app.state.payments = PaymentGateway(cfg, store, http)
            app.state.checkout = Checkout(cfg, http, app.state.payments)
            yield

    app = FastAPI(title="Trading News · Agent API", version="5.2.0", lifespan=lifespan,
                  description=f"One asset report for {cfg.price_usdc} USDC via x402 v2 on Algorand. Use /api/v1/market-signal/{{symbol}}. Today's news ordered by explainable rules: asset relevance, recency, source priority, event and coverage. Includes scores, source links and dates. No OpenAI dependency or BUY/SELL/HOLD recommendation. Report days use UTC.")
    app.state.settings = cfg
    rate = OrderedDict()

    @app.middleware("http")
    async def headers(request, call_next):
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
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
        return {"status": "ok", "version": "5.2.0", "payments_enabled": cfg.payments}

    @app.get("/api/v1/config")
    async def config():
        return {"payments_enabled": cfg.payments, "price_usdc": cfg.price_usdc, "price_atomic": cfg.amount,
                "network": cfg.network_name, "network_caip": cfg.network,
                "asset_id": cfg.asset, "pay_to": cfg.pay_to, "api_url": cfg.public_url,
                "report_path": "/api/v1/market-signal/{symbol}", "day_timezone": "UTC",
                "providers": [{"name": p, "configured": bool(cfg.provider_keys.get(p))}
                              for p in ("newsapi", "gnews")], "ai_enabled": False,
                "selection_method": "rules"}

    @app.get("/api/v1/assets")
    async def assets():
        return {"assets": list(ASSETS.values())}

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

    @app.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse("/docs")

    return app


def production_app():
    return create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
