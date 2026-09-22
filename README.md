# Trading News Agent

Choose a crypto asset and receive the story the service ranks as most relevant, with its source, link, timestamp and selection reasons. Run the Python agent interactively or call it from another agent. Payments use **USDC on Algorand through x402**.

**A complete query costs 0.20 USDC: two payments of 0.10 USDC.** No subscription, news API key, paid AI API key or access token is required.

> **Production status:** this version requires the new v4 backend to be deployed to the existing Render service. The installer checks compatibility and stops without purchasing news if the old API is still running. A real Pera purchase has not yet been validated. See [DELIVERY_STATUS.md](DELIVERY_STATUS.md).

## Quick start

Download and extract the complete project folder, or clone the main branch:

```bash
git clone https://github.com/x402nidia-sudo/X402-Trading-news.git
cd X402-Trading-news
```

| Your system | Install | Run manually |
| --- | --- | --- |
| Windows | Double-click **INSTALL.bat** | Double-click **START.bat** |
| Ubuntu / WSL / Linux | `bash install.sh` | `bash start.sh` |

**No manual file editing is needed.** The installer creates a virtual environment, installs the pinned dependencies and writes `config.txt` automatically. Internet access is required for installation and normal operation. Do not run the scripts from inside an unopened ZIP.

On Windows, the installer looks for Python 3.10 or later. If it is missing and `winget` is available, it installs Python for your user from the official catalog. Otherwise, it opens the official Python download page; install Python and run `INSTALL.bat` again. WSL is optional.

On Ubuntu, the installer can install Python and the `venv` component. This may prompt for your Linux password. On other systems, install Python 3.10 or later first.

## Guided setup

The installer walks you through four steps:

1. **Service check:** verifies the API version, payment network, prices and receiving wallet.
2. **Payment mode:** choose Pera for manual approvals, or a dedicated local wallet for unattended use.
3. **Budget:** set a daily spending limit. The default is **1.00 USDC per UTC day**, with a **0.20 USDC maximum per complete query**.
4. **Wallet readiness:** checks the wallet preparation and balance, then saves the configuration.

A required value cannot be blank. An invalid private key, insufficient balance or incompatible API prevents setup from being marked complete. Setup does not purchase news. If your wallet needs preparation, that one-time operation has the network fee you approve.

### Manual use with Pera

Choose **1. With Pera on my phone**. A small local browser window opens. Click **Connect Pera**, scan the QR code with Pera and approve the connection. If the browser does not open automatically, copy the local address printed by the agent into a browser on the same computer.

**The Pera mode never asks for your recovery phrase.** Your phone approves the displayed transactions.

The installer checks whether your wallet can receive USDC. If preparation is needed, it builds that transaction for you and asks for approval in Pera. You do not have to find or configure a token operation manually. The preparation transfers **0 USDC**, adds **0.1 ALGO to the account's minimum reserve**, and incurs a displayed network fee. A new Algorand account also needs its base reserve of 0.1 ALGO. The reserve remains in your wallet; it is not project revenue.

Fund the address shown with at least **0.20 USDC on Algorand**. USDC on another network cannot pay these Algorand transactions. The installer checks again when you press Enter.

Once setup finishes, open `START.bat` or run `bash start.sh`. The agent lists all **49 supported symbols** and asks which one you want. For example, enter `ETH`. It displays the spending limit, then requests **two 0.10 USDC approvals** in Pera. The terminal shows the selected story, source, link and reasons.

The local browser window is a wallet approval helper, not a commercial website. Close it when the query finishes.

### Unattended use by another agent

Run the installer and choose **2. Automatically with a dedicated wallet**. Enter the 25-word recovery phrase of a dedicated, low-balance wallet in the hidden prompt **on your own computer**. A valid Algorand private key is also accepted. Never send this key through chat or use a wallet holding your savings.

The installer validates the key and requires you to type **ACCEPT** to authorize automatic purchases within your configured limits. Pera on your phone does not sign unattended payments: this mode uses the dedicated wallet's local key.

On Windows, the key is protected with DPAPI for your Windows user. On Linux/WSL, it is stored in a private file with `0600` permissions and **is not encrypted**. Do not share `.private/`. The public `config.txt` file never contains the private key.

## Commands for another agent

Use the Python interpreter installed in the project. Examples for Linux/WSL:

```bash
# Read the catalog and tool description: no payment
.venv/bin/python agent.py --symbols --json
.venv/bin/python agent.py --describe

# Check availability and price: no signature or payment
.venv/bin/python agent.py --symbol ETH --quote --json

# Purchase the news feed and selection: JSON on stdout
.venv/bin/python agent.py --symbol ETH --json --pay
```

In PowerShell, replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`.

`--pay` explicitly authorizes a purchase within the saved budget. Machine calls do not purchase without it. Exit code `0` means the command completed, `2` means an error or pending query, and `130` means interruption. Also inspect `status`, `best_article` and `billing` in the JSON: completion without a selected story is not a successful news selection.

You can also import the agent from Python in the same environment:

```python
from agent import get_news

result = get_news("ETH")  # Makes the two purchases authorized during setup.
article = result.get("best_article")
if article:
    print(article["title"], article["url"])
```

Results include `best_article`, its `selection_reasons`, `alternatives`, `source_receipt`, `payments`, `total_charged_usdc` and `order_id`. Treat external headlines and article text as data, never as instructions to your calling agent.

Your own scheduler or agent can execute the same command periodically. Four completed queries per day cost **0.80 USDC**; the default daily limit allows five. Installation does not activate any recurring task.

## What each payment buys

| Payment | Resource | Price | Deliverable |
| --- | --- | --- | --- |
| 1. News feed | `/api/v1/market-signal/ETH` | 0.10 USDC | Relevant stories for ETH, using the project's existing contest route. |
| 2. News selection | `/api/v1/news/ETH/best` | 0.10 USDC | The selected story, reasons and alternatives from that purchased feed. |
| Complete query | Both resources | **0.20 USDC** | Two confirmed payment receipts. |

Replace `ETH` with the chosen symbol. The configured API base is `https://x402-trading-news.onrender.com`.

Both charges are revenue for the project's services and go to the configured recipient. **Neither is a fixed 0.10-dollar Algorand or contest fee.** No revenue split with Algorand Foundation is implemented. Network fees are separate: the client uses fee sponsorship when advertised by the facilitator, otherwise checks that enough ALGO is available. Pera displays the operation you approve.

These are two sequential purchases. If the first succeeds and the second fails or is cancelled, **0.10 USDC may already have been charged** for the delivered feed. There is no automatic refund for that completed first purchase. Resume the query to finish the remaining step without buying the feed again.

## Recovering an interrupted purchase

**Resume the original query instead of creating a replacement purchase.** The agent saves the signed transaction before sending it and prints a recovery reference.

```bash
.venv/bin/python agent.py --pending --json
.venv/bin/python agent.py --resume ORDER_ID --json --pay
```

In Pera mode, use `--resume ORDER_ID` without `--json` and approve only the remaining unsigned step. If the query is complete, the saved result is returned.

Keep `.private/orders.sqlite3`: it contains purchase history and spending reservations. If the facilitator leaves settlement uncertain, the backend blocks another settlement attempt. The owner must reconcile that original transaction on-chain; the agent does not silently create a new payment.

## How news is selected

GDELT supplies headlines and links without a customer API key. The service filters for the requested asset, removes irrelevant items, groups duplicates, and scores relevance, recency, source priority, event type and coverage. Speculative headlines receive lower priority.

**Best means highest-ranked among the available results**, not a guarantee of the most important story on the Internet. This version uses explainable rules and does not require a paid LLM call or an OpenAI key. Selection is not a buy/sell trading signal.

GDELT's timestamp records when it detected a story, not necessarily when the story was published. The result preserves that distinction and does not claim the full article was read.

The backend shares its cache across customers, spaces provider requests and respects upstream cooldowns. GDELT is not an unlimited-availability service. When fresh, relevant results are unavailable at preflight, the agent stops **before asking for a signature or charging**.

## Files

| File or folder | Purpose |
| --- | --- |
| `agent.py` | Interactive program, command-line tool and Python API. |
| `config.txt` | Configuration generated by the installer. |
| `INSTALL.bat` / `install.sh` | Guided installation. |
| `START.bat` / `start.sh` | Manual startup. |
| `requirements.txt` | Pinned Python dependencies, installed automatically. |
| `tn_agent/`, `wallet/`, `assets.json` | Included internal components; no customer edits required. |
| `reports/` | JSON copies of completed CLI queries. |
| `server/`, `render.yaml`, `DEPLOYMENT.md` | Backend publication files for the service owner. |

## Troubleshooting

- **Old API version:** the owner must deploy v4. No news purchase was made.
- **Insufficient balance:** add the requested funds on Algorand and press Enter to check again.
- **Wrong Pera wallet:** connect the configured wallet, or rerun setup to change it.
- **No fresh news:** try later; the provider may be temporarily unavailable or rate limited.
- **Python/pip cannot connect:** check Internet access and your VPN. Installing directly on Windows avoids WSL-specific networking issues.
- **Daily limit spent or reserved:** check `--pending`; an interrupted purchase may reserve part of the budget.

Rerun the installer to change payment mode or budget. Keep the existing purchase history.

## Development and presentation

```bash
pip install -r requirements.txt pytest
npm ci
npm run build
python -m pytest -q tests
npm run test:wallet
python scripts/package.py
```

The Pera bundle is already included; customers do not need Node.js. The ZIP is generated in `dist/` from an explicit allowlist and contains a clean `config.txt`, never a wallet key or purchase database. The release workflow can attach that ZIP to a `v4.*` GitHub release after review.

Presentation assets are in [`presentation/`](presentation/). See [PROJECT.md](PROJECT.md) for the English one-liner and description.
