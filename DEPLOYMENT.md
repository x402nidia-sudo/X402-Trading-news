# Backend deployment — service owner only

Customers install the Python agent. The owner runs the API that obtains the news, accepts payments and selects the highest-ranked story. The commercial website is outside this release.

## Existing project

- Repository: https://github.com/x402nidia-sudo/X402-Trading-news
- Existing API: https://x402-trading-news.onrender.com
- Owner-provided contest resource: https://facilitator.goplausible.xyz/dashboard/resources/7f934b33452e4d49
- Contest email provided by the owner: x402nidia@gmail.com

On 22 September 2026 the public API still served its previous implementation: `/health` returned `{"status":"ok"}` and `/api/v1/agent-info` returned 404. The new client checks that endpoint before setup or purchases.

The catalog retains the **49 symbols from the original project files**. The authenticated contest dashboard has not been checked to confirm that all 49 are registered. Review it before submitting the project.

## Deploy to the existing Render service

Review and merge the English agent branch, then update **the existing `x402-trading-news` service** to keep its URL and registered resources. Creating a new Blueprint service does not automatically replace the existing service.

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
uvicorn server.main:production_app --factory --host 0.0.0.0 --port $PORT --workers 1
```

Health check: `/health`.

Use **one worker and one instance**, with a persistent disk mounted at `/var/data`. SQLite stores payment receipts, replay protection, provider budgets and shared cache. On Render, startup refuses to enable payments without that mounted disk. Review the cost in Render before adding a paid plan or disk; this package does not purchase infrastructure.

The included `render.yaml` is a reproducible configuration reference validated against Render's public schema. `wallet/wallet.js` is already built, so Render only needs the Python build command. Node is required only when changing and rebuilding the local Pera interface.

## Configure the service once

Copy `server/config.example.env` into the service's environment settings. Customers do not need these settings or provider credentials.

| Variable | Value |
| --- | --- |
| `PAYMENTS_ENABLED` | `true` |
| `DEMO_MODE` | `false` |
| `PUBLIC_BASE_URL` | `https://x402-trading-news.onrender.com` |
| `ALGORAND_NETWORK` | `mainnet` |
| `PAYTO_ADDRESS` | `EH5BHWISPB7MEIITJIWF2VB3YFN2RZLJMWBRV6CBJV76FBAEAALL6XKSQE` |
| `SOURCE_PRICE_USDC` | `0.10` |
| `SELECTION_PRICE_USDC` | `0.10` |
| `DATABASE_PATH` | `/var/data/news.sqlite3` |
| `GDELT_ENABLED` | `true` |
| `GDELT_MIN_INTERVAL_SECONDS` | `10` |
| `CACHE_SECONDS` | `900` |
| `STALE_MAX_AGE_SECONDS` | `3600` |
| `PREFETCH_ASSETS` | `BTC,ETH,SOL,ALGO` |
| `ENABLE_LEGACY_PROVIDERS` | `false` |
| `AI_RERANK` | `false` |

The recipient comes from the existing public ETH payment challenge. **Verify that you control this wallet and that it can receive Algorand USDC.** An HTTP response does not prove wallet ownership. The installer checks readiness, not ownership of the merchant's private key. The new implementation generates its discovery response from this configuration instead of using the inconsistent legacy manifest.

No GDELT key is required. Guardian, NewsAPI, GNews and paid AI calls are optional legacy components and are disabled in this release. Remove unused provider keys from the service; rotate any key that was previously committed publicly. The former committed `.env` is excluded from this branch, but removing a file does not remove it from Git history.

Both 0.10 USDC charges go to the configured merchant wallet. There is no assumed contest fee or automatic transfer to Algorand Foundation. The facilitator's `/supported` response determines whether fee sponsorship is available.

## Verify without paying

```bash
curl https://x402-trading-news.onrender.com/health
curl https://x402-trading-news.onrender.com/api/v1/agent-info
curl https://x402-trading-news.onrender.com/api/v1/assets
curl -i https://x402-trading-news.onrender.com/api/v1/market-signal/ETH
curl -i https://x402-trading-news.onrender.com/api/v1/news/ETH/best
curl https://x402-trading-news.onrender.com/api/v1/quote/ETH
```

Expect version `4.0.0`, 49 assets, canonical Algorand Mainnet, USDC ASA `31566704`, and an amount of `100000` per paid resource. Both paid routes return **HTTP 402** when no payment authorization is supplied. **HTTP 429 is different:** it indicates a request limit.

`/quote/ETH` checks availability and price without signing or charging. It may return 503 when valid fresh news is unavailable.

The original `/market-signal/{symbol}` URL is retained, but its paid response becomes a news feed. It no longer generates BUY/SELL from headline counts. Update clients that depend on the old response shape. The selection route returns `best_article` and requires a purchased feed for the same symbol and payer.

`/.well-known/x402.json` advertises both routes per symbol. Review the existing resources in the contest dashboard and register the selection route if required by its rules. A discovery tag alone does not establish contest eligibility.

## Validate a real Pera purchase

After deployment, run `INSTALL.bat` or `bash install.sh` on your computer. Choose Pera, connect your wallet, complete any required preparation and funding, then purchase ETH.

Check the two 0.10 USDC approvals, the returned story and both transaction IDs on Algorand. Run `--resume ORDER_ID` and verify that it returns the same result without a third payment. Keep the receipts for the presentation.

**This real-wallet verification is still pending.** The local tests use simulated provider responses and settlement. They do not prove that a particular mobile Pera session or a native Windows setup has worked in production. Never enter your Pera recovery phrase into ChatGPT; manual Pera mode does not need it.

## Publish the installer

After review and the production checks, create a `v4.*` tag. The `Package installer` workflow installs dependencies, rebuilds the wallet bundle, runs tests and creates a ZIP without secrets. It attaches the ZIP and checksum to a GitHub release, if Actions is enabled.

For a local package:

```bash
python scripts/package.py
```

Update `DELIVERY_STATUS.md` with evidence before claiming production validation. Keep customer secrets, local configuration, purchase journals and report files out of Git.

## Operation and recovery

GDELT's request budget and pacing are shared across assets and customers. Tests demonstrate that 1,000 concurrent reads of one asset use one provider request. This is not a Render load test or an uptime guarantee.

The service also applies a configurable per-IP request limit: `REQUESTS_PER_MINUTE`, default 60. Do not advertise unlimited requests. Shared cache and durable payment storage must be redesigned before adding workers or instances.

Prepared snapshots expire after 10 minutes. A pending purchase never silently creates a new transaction to resolve an uncertain settlement. For `PAYMENT_PENDING_RECONCILIATION`, inspect the original transaction and group on Algorand before repairing state. Back up the database before any repair. The customer's local order journal preserves the original signed authorization; their private key is not needed for reconciliation.

## Primary references

- [Pera Connect](https://docs.perawallet.app/references/pera-connect/)
- [Official Pera SDK](https://github.com/perawallet/connect)
- [Algorand asset operations](https://dev.algorand.co/concepts/assets/asset-operations/)
- [Algorand account reserves](https://dev.algorand.co/concepts/accounts/overview/)
- [GDELT](https://www.gdeltproject.org/)
- [Render persistent disks](https://render.com/docs/disks)
