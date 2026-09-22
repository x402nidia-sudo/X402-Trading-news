# Delivery status — 22 September 2026

## Included

- English Python agent with interactive use, a 49-symbol catalog, JSON CLI and `get_news` function.
- English Windows and Linux/WSL installers that install dependencies and generate `config.txt`.
- Pera connection and transaction signing through its official SDK in a temporary local window.
- Automatic mode with a dedicated local wallet, required key validation and durable spending limits.
- Automated USDC wallet readiness checks and preparation, subject to the user's approval.
- Two x402 resources priced at 0.10 USDC each: news feed and news selection.
- GDELT, shared cache, explainable ranking and durable payment recovery.
- English README, deployment instructions, presentation video and project description.

## Validation

The tests use disposable keys and cryptographic signatures with simulated provider/network/settlement responses. They cover two payments, lost-response recovery, uncertain settlement, daily limits, upstream 429 handling, deduplication, relevance ranking, amount/recipient/network checks, installer requirements, local-window isolation and Python/JavaScript interoperability. No funds are spent by these tests. See `TEST_RESULTS.txt`.

The Render Blueprint has been checked against Render's public schema. The remote browser in the development environment could not open the local Pera page (`ERR_BLOCKED_BY_CLIENT`). Local HTTP endpoints and signature handling were tested; a mobile Pera approval was not.

## Publication and remaining work

The English source is prepared in the repository's `codex/trading-news-agent-v4-english` branch for review. Publishing this branch does not deploy the backend or establish that a production purchase works.

Before a production launch:

1. Review and merge the branch, then deploy the new API to the existing Render service.
2. Confirm the Render workspace, receiving wallet ownership and persistent disk configuration. No additional paid infrastructure has been activated.
3. Complete native Windows/Pera setup and one real 0.20 USDC query with both receipts, then verify recovery.
4. Check the 49-symbol project catalog against the authenticated contest resource list.

The live API was checked on 22 September 2026: `/health` still returned `{"status":"ok"}` and `/api/v1/agent-info` returned 404. Its ETH challenge advertised 100000 atomic USDC units on Mainnet, ASA `31566704`, to `EH5BHWISPB7MEIITJIWF2VB3YFN2RZLJMWBRV6CBJV76FBAEAALL6XKSQE`. These are configuration observations, not proof of wallet ownership or a completed purchase.

The installer explicitly stops when it encounters the old API. It does not silently substitute fictional news or mark an incomplete setup as ready.
