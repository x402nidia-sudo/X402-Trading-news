x402-quant-signals
A high-performance, pay-per-call REST API serving quantitative trading signals, monetized entirely on-chain via the x402 protocol on the Algorand Mainnet.

📌 Overview
x402-quant-signals demonstrates a fully functional machine-to-machine economy. This project provides programmatic access to technical market indicators (such as Hull Moving Average (HMA), Exponential Moving Averages (EMA), and MACD crossovers) hidden behind an L402 payment gateway.

Consumers must programmatically settle micro-transactions in USDC on the Algorand blockchain to receive the API payload, completely eliminating the need for traditional subscription models or API key billing architectures.

Global Challenge Participation: This endpoint actively participates in the x402 Global Challenge, injecting the mandatory x402-global-challenge tag within the bazaar extension metadata inside the CAIP-2 Algorand Mainnet network configuration.

🏗 Architecture & Flow
The system consists of a local Uvicorn/FastAPI server and an automated Node.js client.

Request: The client requests market data from the API endpoint.

Challenge (HTTP 402): The server intercepts the request and responds with a 402 Payment Required status, including a Base64 encoded JSON payload detailing the required payment (Network, Asset, Amount, Payee, and Bazaar tags).

On-Chain Settlement: The client parses the challenge, signs an Algorand transaction using its private key, and submits the USDC payment to the network.

Verification: The client re-submits the HTTP request, including the transaction ID as the payment receipt in the authorization header. The server verifies the transaction validity directly on-chain before serving the protected quantitative data.

⚙️ Prerequisites
Python 3.x

Node.js (v18+ recommended)

An active Algorand Wallet funded with ALGO (for network fees) and USDC (for micro-payments).

Note: Ensure your wallet has opted-in to the USDC ASA ID on Algorand Mainnet.

🚀 Setup & Installation
1. Clone the repository:
   Bash
      git clone https://github.com/your-username/x402-quant-signals.git
      cd x402-quant-signals
2. Install Server Dependencies (Python):
   Bash
      pip install -r requirements.txt
3. Install Client Dependencies (Node.js):
   Bash
      npm install
4. Environment Variables:
Create a .env file in the root directory to store your private keys and Algorand node configuration securely. Do not commit this file to version control.

💻 Usage
To run the full flow locally and generate traffic:

1. Start the Server:
Launch the Uvicorn server to listen for incoming requests and issue L402 challenges.

   Bash
   python main.py
2. Run the Traffic Generator:
In a separate terminal, launch the automated client loop. This script (lanzador.py) executes the Node.js client (client.mjs) continuously with random delays, simulating organic, high-frequency API consumption and settling on-chain payments automatically.

   Bash
   python lanzador.py

🛡️ License
MIT License
