# Trading News — Backend

Explainable crypto news reports for people and software agents, purchased with USDC on Algorand through x402 v2.

The API covers 49 assets. It retrieves news from NewsAPI and GNews, removes duplicate coverage, ranks relevant stories, and explains the selection. Each story includes its source, publication date, importance and an indicative BUY / SELL / HOLD interpretation. The engine uses deterministic rules and makes no OpenAI requests.

This README describes backend **5.7.0** and the matching website.

| Resource | Link |
|---|---|
| Website | [trading-news-web.onrender.com](https://trading-news-web.onrender.com/) |
| API | [x402-trading-news.onrender.com](https://x402-trading-news.onrender.com/) |
| API for agents | [Interactive documentation](https://x402-trading-news.onrender.com/docs) |
| Frontend repository | [x402nidia-sudo-trading-news-web](https://github.com/x402nidia-sudo/x402nidia-sudo-trading-news-web) |

## What the service delivers

- A paid report for one selected asset and importance filter: all, red, orange or yellow.
- Today's matching stories, ordered by relevance, with the highest-ranked story first.
- Explainable scores, selection reasons, source dates and provider availability.
- Generated HTML news cards with extracted key points, importance, an indicative signal and the original source link.
- Free availability checks before purchase, without revealing today's headlines or summaries.
- Free previously collected news from the last seven days, strictly excluding today.
- Confirmed email subscriptions for one coin or all coins, with an independent importance filter.
- Persistent payment records and purchased reports, plus read-only checks for interrupted payments.

All report-day boundaries use **UTC**. English, Spanish, French and German are supported by the website and explanatory text. Headlines and excerpts remain in their source language.

## How news is ranked

NewsAPI and GNews retrieve articles. Local rules filter asset matches, reject invalid or unsuitable items, group duplicate headlines and assign a score out of 100:

| Component | Maximum points |
|---|---:|
| Asset relevance | 35 |
| Recency | 25 |
| Predefined source priority | 15 |
| Event importance | 15 |
| Similar coverage across publisher domains | 10 |

Ties use the newest publication date, then URL. Speculative headlines receive a reduced event score. Source weights are editorial priorities; they do not verify an article's accuracy. Coverage from multiple domains does not prove independent confirmation.

Importance is classified separately as **red / high**, **orange / medium**, or **yellow / low**. Selecting a color matches that exact category, not that category and everything above it.

BUY / SELL / HOLD is an indicative **per-story** interpretation of the available headline and excerpt. Uncertainty, conflicting evidence or weak attribution generally produces HOLD. Extracted key points come from provider text; the engine does not read and summarize the full publisher article, analyze charts or forecast returns.

**Current limitation:** new reports have `assessment: null` and `recommendation: null` at report level. Per-story results are in `articles[].insight.recommendation`. Version 5.7 does not calculate a combined BUY / SELL / HOLD assessment for the whole report.

The Guardian API, GDELT and OpenAI are not integrated in this version. NewsAPI or GNews may still return an article published by The Guardian.

## Price and x402 payments

The configured report price is **0.2 USDC** (`PRICE_USDC=0.2`), equivalent to **200000 atomic units**. This USDC amount is transferred to `PAYTO_ADDRESS`. The implementation does not split it into 0.199 USDC plus a 0.001 USDC contest fee.

Algorand network fees are separate and denominated in ALGO. The checkout uses sponsorship when the facilitator advertises a fee payer; otherwise it displays the customer's network fee before signing. The x402 integration includes `x402-global-challenge` metadata. Confirm attribution in the merchant dashboard after a successful real settlement.

The existing 49 registered base routes remain `/api/v1/market-signal/{symbol}`. A filtered purchase uses the canonical URL returned by checkout, for example `/api/v1/market-signal/BTC?importance=high`. For all importance levels, omit the query string.

1. The client checks news availability and requests the paid resource.
2. The API returns HTTP 402 and payment requirements.
3. An Algorand-capable client signs the advertised payment and repeats the same request with `PAYMENT-SIGNATURE`.
4. The backend verifies the payment, prepares the report and settles through the facilitator.
5. A successful response includes the report, `PAYMENT-RESPONSE` and `billing.receipt`.

The backend checks for usable matching news before settlement. It does not settle payment for an empty report or when all sources are unavailable. Partial provider coverage is explicitly identified.

The same signed request retrieves an already stored purchase without another settlement. An uncertain payment must be reconciled before preparing a replacement. The website performs read-only status checks automatically; there is no manual Recover button in the current UI.

Only Algorand USDC is accepted by this checkout. Cross-chain conversion and card payments are not implemented. Customer seeds and private keys never belong in backend configuration.

## Essential application files

| Location | Purpose |
|---|---|
| `main.py` | FastAPI routes, lifecycle and deployment checks |
| `requirements.txt` | Python dependencies |
| `assets.json` | 49-asset catalog |
| `news/__init__.py`, `news/config.py`, `news/catalog.py` | Package, settings and asset lookup |
| `news/providers.py`, `news/ranking.py`, `news/service.py` | Retrieval, ranking, reports and history |
| `news/insights.py` | Importance, per-story signals and HTML cards |
| `news/checkout.py`, `news/payments.py` | Payment preparation, x402 settlement and reconciliation |
| `news/storage.py`, `news/alerts.py` | SQLite persistence and email alerts |

The complete `news/` package is required. Keep `.gitignore` to exclude local secrets and runtime data. `README.md` documents the application. The frontend lives in its separate repository. Do not upload `.env`, database files, virtual environments or `__pycache__`.

## Render configuration

Use the existing backend Web Service so its public API URL remains unchanged. The website must be deployed separately as a Static Site.

| Setting | Value |
|---|---|
| Repository | `https://github.com/x402nidia-sudo/X402-Trading-news` |
| Branch | `main` |
| Runtime | Python 3 |
| Root Directory | Empty |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python -m uvicorn main:production_app --factory --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/health` |
| Persistent disk mount | `/var/data` |

Use one instance and one application worker. On Render, enabled payments require the actual persistent disk to be mounted at `/var/data`; setting a path alone does not attach a disk. Preserve the existing database path when updating an installation.

### Backend environment variables

| Variable | Value / purpose |
|---|---|
| `DEMO_MODE` | `false` |
| `PAYMENTS_ENABLED` | `true` |
| `PUBLIC_BASE_URL` | `https://x402-trading-news.onrender.com` |
| `WEB_BASE_URL` | `https://trading-news-web.onrender.com` — links in email and news cards |
| `WEB_ORIGINS` | `https://trading-news-web.onrender.com` — exact allowed browser origin |
| `ALGORAND_NETWORK` | `mainnet` |
| `PAYTO_ADDRESS` | `EH5BHWISPB7MEIITJIWF2VB3YFN2RZLJMWBRV6CBJV76FBAEAALL6XKSQE` |
| `PRICE_USDC` | `0.2` — use a decimal point |
| `FACILITATOR_URL` | `https://facilitator.goplausible.xyz` |
| `DATABASE_PATH` | `/var/data/trading-news.sqlite3`, or the existing database filename under that mount |
| `NEWSAPI_KEY` | Your NewsAPI key, if using this provider |
| `GNEWS_API_KEY` | Your GNews key, if using this provider |
| `EMAIL_REMITENTE` | Sending Gmail address, required for alerts |
| `EMAIL_PASSWORD` | Gmail app password for that address, required for alerts |
| `ALERT_INTERVAL_HOURS` | `0.5` — thirty minutes |

At least one news provider needs a valid key and sufficient quota. Keep secrets exclusively in backend environment variables. `AI_RERANK`, `OPENAI_API_KEY`, `OPENAI_MODEL` and `AI_DAILY_LIMIT` are unused and can be removed. No additional environment variable is needed for subscription importance.

Optional defaults: `CACHE_SECONDS=900`, `MAX_AGE_HOURS=24`, `NEWS_LANGUAGE=en`, `NEWSAPI_DAILY_LIMIT=90`, `GNEWS_DAILY_LIMIT=90`, `REQUESTS_PER_MINUTE=60`. Retrieval language accepts `en`, `es` or `all`; it is independent of the interface language. Production reports remain limited to today's UTC news.

`WEB_ORIGINS` supports comma-separated exact origins. Do not use `*`, paths or a trailing slash. Changing `PAYTO_ADDRESS` also requires updating and rebuilding the frontend's pinned payment validation.

### Email alerts

An asynchronous task runs inside the backend process while email credentials are configured. A separate AI agent or Render Cron Job is not required.

Users select a coin, choose that coin or all coins, select an importance level and enter their email. The subscription only becomes active after email confirmation. Preference changes also require confirmation; existing preferences remain active until then.

The task checks due subscriptions approximately every thirty minutes and queries their assets. An all-coins subscription includes the full catalog. Checks share cached results and respect provider limits; an outage or exhausted quota can delay coverage. The default 90 requests per provider per day is not enough to guarantee fresh queries for all 49 assets every thirty minutes. Adjust application limits and provider plans to the intended workload.

Notifications identify only the coin or coins, importance icons and a link to the website to buy the report. They do not reveal headlines, excerpts or publisher links. Delivery records suppress repeat alerts for already notified stories. Emails include an unsubscribe link.

Subscription addresses, coin scope, language, importance, confirmation state and pending preferences are stored in the SQLite table `news_subscriptions`. Delivery tracking is in `news_alert_deliveries`, in the same file configured by `DATABASE_PATH` on the persistent disk. Treat this database as private data and preserve it across deployments.

## API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, version and payment flag |
| GET | `/api/v1/config` | Public configuration; no provider or SMTP secrets |
| GET | `/api/v1/assets` | Asset catalog |
| GET | `/api/v1/news/{symbol}` | Today's availability and previous news; optional `importance` |
| GET | `/api/v1/history` | Collected archive; `symbol=ALL` or an asset, plus `importance` |
| POST | `/api/v1/checkout/{symbol}` | Unsigned quote; body `{"address":"YOUR_ALGORAND_ADDRESS"}`; optional `importance` |
| GET | `/api/v1/market-signal/{symbol}` | HTTP 402 challenge or purchased report; canonical importance query |
| POST | `/api/v1/payments/status` | Read-only reconciliation; body `{"signature":"ORIGINAL_PAYMENT_SIGNATURE"}` |
| GET | `/news/{symbol}/{article_id}` | Generated HTML card; optional `lang` |
| POST | `/api/v1/alerts/subscribe` | Request a confirmed subscription |
| POST | `/api/v1/alerts/confirm` | Confirm using the emailed token |
| POST | `/api/v1/alerts/unsubscribe` | Unsubscribe using the emailed token |
| GET | `/.well-known/x402.json` | x402 discovery and registered base resources |
| GET | `/docs` | Interactive API documentation |

Subscription body example: `{"email":"reader@example.com","symbol":"BTC","language":"en","importance":"high"}`. Use `"ALL"` for all coins. Confirmation and unsubscribe bodies contain `{"token":"TOKEN_FROM_EMAIL"}`.

Reports use schema `2.3`. Important fields include `best_article`, `articles`, `stats`, `providers`, `selection_method`, `day_utc` and `billing`. Each article includes score components and `insight` with importance, recommendation and extracted key points.

Today's HTML cards require the original proof of a stored purchase containing that article. Opening a card does not initiate payment. Historical cards are public while available; the archive is collected coverage, not a complete historical database.

### Read-only examples

```bash
curl -sS https://x402-trading-news.onrender.com/health
curl -sS https://x402-trading-news.onrender.com/api/v1/config
curl -sS 'https://x402-trading-news.onrender.com/api/v1/news/BTC?importance=high'
curl -sS 'https://x402-trading-news.onrender.com/api/v1/history?symbol=ALL&importance=all'
```

An agent purchasing a report must implement the advertised Algorand x402 signing flow. Opening `/docs` alone does not supply a funded wallet or automatically complete a payment.

## Local startup

From the repository root, using Python 3.12 or a compatible Python environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PAYMENTS_ENABLED=false PUBLIC_BASE_URL=http://127.0.0.1:8000 python -m uvicorn main:production_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

This starts public endpoints with purchases disabled. Real news still requires provider credentials. On Windows, use the equivalent PowerShell environment and activation commands. Leave email credentials unset if testing should not send email.

After deployment, `/health` should identify `5.7.0`; `/api/v1/config` should show `price_usdc: "0.2"`, `price_atomic: "200000"`, `selection_method: "rules"`, `ai_enabled: false` and `alert_importance_enabled: true`. A healthy process does not prove that live news, SMTP, wallet signing or facilitator settlement succeeds. Verify those integrations separately before presenting a successful live purchase.

Reference documentation: [Render FastAPI](https://render.com/docs/deploy-fastapi), [Render disks](https://render.com/docs/disks), [NewsAPI](https://newsapi.org/docs), [GNews](https://docs.gnews.io/), [Pera Connect](https://docs.perawallet.app/references/pera-connect/).
