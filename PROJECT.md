# Trading News

## Project one-liner

Trading News is a Python agent that selects relevant crypto news with explainable ranking and two pay-per-use USDC payments on Algorand via x402.

## Project description

Trading News turns a supported crypto symbol into one selected news story, its source link and a clear explanation of why it ranks first. Users can run an interactive Python agent, while other agents can discover the symbol catalog and request structured JSON.

A guided installer installs dependencies, configures the agent and checks wallet readiness. Manual users approve purchases with Pera; unattended agents use a dedicated local wallet with spending limits. Each complete query purchases two distinct resources: a 0.10 USDC news feed and a 0.10 USDC selection, both using x402 on Algorand.

GDELT, shared caching and explainable rules avoid mandatory paid news and LLM subscriptions. Persistent receipts let interrupted queries resume without buying the first resource again. If fresh news is unavailable at preflight, the client does not sign a payment.

## Submission status

The package implements these flows and has passed local tests using simulated providers and settlement. Deployment of the new public API, a real Pera purchase and verification of the contest dashboard's registered catalog remain pending. Do not describe those steps as completed until verified. The presentation video is a visual explainer, with illustrative examples and no real transfers.

Owner-provided contest contact: x402nidia@gmail.com.
