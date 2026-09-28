# Trading News — Backend

Pay-per-report news intelligence for 49 crypto assets, delivered through an x402 v2 API on Algorand.

The service retrieves today's news, removes duplicates, ranks relevant stories and returns source links, publication dates and an explanation of the selection. Version 5.2 orders news entirely with explainable rules. It makes no OpenAI requests and generates no BUY / SELL / HOLD recommendation.

- API base URL: https://x402-trading-news.onrender.com
- API documentation: https://x402-trading-news.onrender.com/docs
- Frontend repository: https://github.com/x402nidia-sudo/x402nidia-sudo-trading-news-web

## Version requirement

This README documents the **5.2 rules-only update**. Extract `backend_sin_openai.zip` at the root of `x402nidia-sudo/X402-Trading-news`, replacing `main.py`, `news/config.py`, `news/providers.py`, `news/ranking.py`, `news/service.py` and `README.md`. Keep all other existing application files. Delete the obsolete `news/ai.py`; it is no longer imported. Apply the matching frontend update. Do not delete the database or persistent disk.

After deployment, `/health` must identify version `5.2.0`. `/api/v1/config` must include `selection_method: "rules"`, `ai_enabled: false`, `price_atomic` and only the NewsAPI/GNews providers.

## How it works

1. Select an asset from the existing 49-symbol catalog.
2. The backend queries the configured NewsAPI and/or GNews providers.
3. It keeps relevant news published today in UTC, groups duplicate coverage and calculates an explainable priority score.
4. The highest-scoring article leads the report. Every article includes the five score components and its selection reasons. Equal scores are resolved by newest publication date, then URL.
5. The customer authorizes an Algorand USDC payment. The backend verifies and settles it through the facilitator before releasing the report.
6. The purchased report and payment receipt are stored in SQLite. Reusing the same signed request recovers that purchase without a second settlement.

The default report price is **0.199 USDC**, configurable through `PRICE_USDC`. No customer seed phrase or private key is required by the server.

## Repository files

| Location | Purpose |
|---|---|
| `main.py` | FastAPI application, routes and deployment checks |
| `requirements.txt` | Pinned Python dependencies |
| `assets.json` | Existing 49-symbol catalog |
| `news/config.py`, `news/catalog.py` | Environment configuration and asset lookup |
| `news/providers.py`, `news/ranking.py` | News retrieval, relevance, dates and deduplication |
| `news/service.py` | Rule-based report orchestration and cache |
| `news/checkout.py`, `news/payments.py` | Unsigned payment preparation, x402 verification and settlement |
| `news/storage.py`, `news/__init__.py` | Durable storage and package initialization |

Upload the complete `news/` package alongside the three root application files. The website is deployed from its separate repository.

## Configure the existing Render service

Keep the existing service to preserve its registered API URLs:

https://dashboard.render.com/web/srv-d9pd8t8ae00c73eqchog

The previous application used **Node**. This backend requires the **Python 3** runtime.

1. Temporarily set Auto-Deploy to **Off** while uploading the update and configuring the service.
2. Configure the environment variables below. Use **Save only**, if offered, until the remaining settings are ready.
3. Change the instance to a paid instance type, the 7 USD/month, 0.5 CPU / 512 MB option shown in the dashboard, and attach a persistent disk at `/var/data`. A 1 GB disk is an initial option; review the price shown in Render before confirming. This code requires that mount when payments are enabled.
4. Open **Settings → Build → Source → Edit**. Select `x402nidia-sudo/X402-Trading-news` again, even if the same repository name was previously connected. This reconnects the current source after a repository replacement.
5. Set the following fields in the source/settings form:

| Render field | Value |
|---|---|
| Repository | `https://github.com/x402nidia-sudo/X402-Trading-news` |
| Branch | `main` |
| Runtime | `Python 3` |
| Root Directory | Leave empty |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:production_app --factory --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/health` |

6. Click **Deploy** in Update Source. If another deployment is needed after adjusting the settings, use **Manual Deploy → Clear build cache & deploy** to build the latest commit with the new configuration. Do not select a deleted historical commit.
7. Once checks pass, use **On Commit** for automatic deployment if desired. This repository does not include a CI workflow, so do not select **After CI Checks Pass** unless you add one.

The existing service keeps its URL when its source/runtime is updated. A historical commit link can return 404 after the old repository history is deleted; the new deployment must use the current `main` branch.

If the repository is missing from Render's selector, configure the [Render GitHub app](https://github.com/apps/render/installations/new) for the `x402nidia-sudo` account and grant access to the new repositories.

### Backend environment variables

Configure these in the backend service's **Environment** page. Values are strings; use `true`/`false` for booleans and a decimal point for the price.

| Variable | Value / purpose |
|---|---|
| `PYTHON_VERSION` | `3.12.12` |
| `DEMO_MODE` | `false` |
| `PAYMENTS_ENABLED` | `true` |
| `PUBLIC_BASE_URL` | `https://x402-trading-news.onrender.com` |
| `ALGORAND_NETWORK` | `mainnet` |
| `PAYTO_ADDRESS` | `EH5BHWISPB7MEIITJIWF2VB3YFN2RZLJMWBRV6CBJV76FBAEAALL6XKSQE` — public receiving address pinned by the supplied frontend |
| `PRICE_USDC` | `0.199` — up to six decimal places |
| `FACILITATOR_URL` | `https://facilitator.goplausible.xyz` |
| `DATABASE_PATH` | `/var/data/trading-news.sqlite3` |
| `WEB_ORIGINS` | The actual frontend origin assigned by Render, such as `https://YOUR-WEB-SITE.onrender.com`; replace the example |
| `NEWSAPI_KEY` | Your NewsAPI key, if using NewsAPI |
| `GNEWS_API_KEY` | Your GNews key, if using GNews |

At least one news provider must have a working key. Both can be enabled. NewsAPI and GNews retrieve news; local rules rank their results. The Guardian API is not used. Remove obsolete Guardian variables. You can also remove `AI_RERANK`, `OPENAI_API_KEY`, `OPENAI_MODEL` and `AI_DAILY_LIMIT`: version 5.2 ignores them, even if they remain configured.

Keep API keys in Render. Do not commit them to either repository or put them in browser JavaScript. News providers have their own plan and commercial-use requirements; removing OpenAI does not remove these. See [NewsAPI pricing](https://newsapi.org/pricing) and [GNews pricing](https://gnews.io/pricing).

Optional limits retain these defaults: `CACHE_SECONDS=900`, `MAX_AGE_HOURS=24`, `NEWS_LANGUAGE=en`, `NEWSAPI_DAILY_LIMIT=90`, `GNEWS_DAILY_LIMIT=90`, `REQUESTS_PER_MINUTE=60`. These are application limits, not guarantees about a provider subscription. `NEWS_LANGUAGE` controls retrieval language (`en`, `es` or `all`), independently of the frontend interface language.

Use the actual frontend origin in `WEB_ORIGINS`, without a trailing slash or path. Multiple origins are comma-separated. Wildcard `*` is rejected. While the website is not yet deployed, the API can start with only the existing local defaults; add the final website origin before enabling browser purchases.

Run **one application worker and one instance**. The SQLite database stores payment state and purchased responses. This implementation cannot run enabled payments on Render's Free instance without its required persistent disk.

## API for agents

| Method | Path | Result |
|---|---|---|
| GET | `/health` | Application status, version and payment flag |
| GET | `/api/v1/config` | Public payment configuration; no API keys |
| GET | `/api/v1/assets` | Supported asset catalog |
| GET | `/api/v1/market-signal/{symbol}` | HTTP 402 challenge, or the paid report after successful settlement |
| POST | `/api/v1/checkout/{symbol}` | Unsigned wallet transaction quote; JSON body: `{"address":"YOUR_ALGORAND_ADDRESS"}` |
| GET | `/.well-known/x402.json` | Discovery manifest and the existing resource URLs |
| GET | `/docs` | Interactive API documentation |

The paid resource remains `/api/v1/market-signal/{symbol}`. There is one report purchase; a second news endpoint purchase is not required.

An AVM-capable x402 client requests the resource, reads the HTTP 402 challenge, signs the exact advertised payment and repeats the same GET with `PAYMENT-SIGNATURE`. Successful delivery includes the `PAYMENT-RESPONSE` receipt header and `billing.receipt` in the JSON body.

New reports use schema `2.2` and include `best_article`, `articles`, `stats`, `providers`, source links, publication dates and `selection_reasons`. Each article contains `score` and `components`. `selection_method` is `rules`, `ai.status` is `disabled`, and `assessment` and `recommendation` are `null`. These compatibility fields do not invoke an AI service. Agents should interpret the result as ranked news, not a trading signal; the registered route name remains unchanged.

The maximum score is 100: asset relevance 35, recency 25, predefined source priority 15, event 15, and coverage across domains 10. Speculative headlines reduce the event component. Source weights are editorial priorities, not factual verification; similar coverage does not prove independent confirmation. Selection uses retrieved headlines and excerpts, not full articles.

Only relevant items dated today in UTC are eligible. Missing dates, irrelevant assets and old items are excluded, and duplicate headlines are grouped. The rules are optimized for English and Spanish; they do not translate news content. The website explains the selection in English, Spanish, French and German.

If no provider is available or no relevant news remains, the backend blocks settlement. A failed provider can still yield a report from another available provider, marked as partial coverage. No AI credentials or AI quota are required. `/health` is a liveness check, not verification that external providers or payment settlement work.

## Payment recovery and network fees

Previously purchased reports remain recoverable as originally stored, including any historical assessment, without an OpenAI call. Unsold caches from earlier versions are not used for new purchases. Keep the exact signed purchase request until its result is known. If the connection is interrupted, repeat that request to recover the stored report. A pending or unknown settlement must be reconciled rather than replaced with another payment.

Algorand network fees are separate from the USDC report price. When the facilitator advertises a fee payer, the sponsored group covers those fees; otherwise the customer's network fee is shown before signing. The application includes `x402-global-challenge` metadata. It does not create a separate contest-fee transfer, and attribution must be checked in the merchant dashboard after a real purchase.

Payment accepts Algorand USDC. Cross-network bridging and automatic token conversion are not included.

## Local API check

With Python 3.12 and an isolated environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PAYMENTS_ENABLED=false PUBLIC_BASE_URL=http://127.0.0.1:8000 uvicorn main:production_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

This checks startup and public endpoints without enabling purchases. For Windows, activate `.venv\Scripts\Activate.ps1` and set environment variables using PowerShell syntax. The supplied frontend remains configured for the production API.

## Deployment checks

- `/health` returns HTTP 200 with version `5.2.0`.
- `/api/v1/config` returns `price_usdc: "0.199"` and `price_atomic: "199000"` at the default price.
- `/api/v1/assets` returns 49 assets.
- `/api/v1/market-signal/BTC` without a signature returns HTTP 402 when payments and the facilitator are available.
- The frontend shows the expected price and recipient, and its origin is allowed by the backend.
- A deliberate real purchase returns a confirmed receipt; recovering that purchase does not cause a second settlement.

Version 5.2 passed 34 backend checks and 15 JavaScript/UI/wallet checks using simulated providers and payments. No real payment was made. Live API credentials, wallet signing, production settlement and contest attribution require deployment verification.

Render references: [change the source/runtime](https://render.com/docs/native-runtimes#changing-a-services-runtime), [persistent disks](https://render.com/docs/disks), [manual deploys](https://render.com/docs/deploys), [GitHub access](https://render.com/docs/git-provider).
