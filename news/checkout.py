"""Prepare unsigned x402 Algorand transactions. Keys stay in the customer's wallet."""
import asyncio
import time
from types import SimpleNamespace

import httpx
from algosdk import encoding, transaction
from fastapi import HTTPException
from x402.mechanisms.avm.exact import ExactAvmScheme
from x402.schemas import PaymentRequirements

ALGOD = {"mainnet": "https://mainnet-api.algonode.cloud", "testnet": "https://testnet-api.algonode.cloud"}


class UnsignedSigner:
    def __init__(self, address):
        self.address = address
        self.indexes = []

    def sign_transactions(self, unsigned_txns, indexes_to_sign):
        self.indexes = indexes_to_sign
        return [None] * len(unsigned_txns)


class PreparedScheme(ExactAvmScheme):
    """Reuse the pinned official builder with params fetched asynchronously."""
    def __init__(self, signer, params):
        super().__init__(signer)
        self.node = SimpleNamespace(suggested_params=lambda: params)

    def _get_client(self, network):
        return self.node


class Checkout:
    def __init__(self, settings, http, gateway):
        self.cfg, self.http, self.gateway = settings, http, gateway
        self._params = None
        self._params_at = 0
        self._lock = asyncio.Lock()

    async def params(self):
        async with self._lock:
            if self._params and time.monotonic() - self._params_at < 15:
                return self._params
            try:
                r = await self.http.get(ALGOD[self.cfg.network_name] + "/v2/transactions/params", timeout=12)
                r.raise_for_status()
                d = r.json()
                if d["genesis-hash"] != self.cfg.network.split(":", 1)[1]:
                    raise ValueError("genesis")
                first, minimum = int(d["last-round"]), int(d.get("min-fee", 1000))
                # Stop during congestion instead of approving an open-ended network fee.
                if first <= 0 or not 1000 <= minimum <= 10000 or int(d.get("fee", 0)) > 0:
                    raise ValueError("params")
                self._params = transaction.SuggestedParams(
                    fee=minimum, first=first, last=first + 100,
                    gh=d["genesis-hash"], gen=d.get("genesis-id"), flat_fee=True, min_fee=minimum)
                self._params_at = time.monotonic()
                return self._params
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise HTTPException(503, "SAFE_PAYMENT_PREPARATION_UNAVAILABLE") from None

    async def prepare(self, address, resource):
        if not self.cfg.payments:
            raise HTTPException(503, "PURCHASES_DISABLED")
        if not encoding.is_valid_address(address):
            raise HTTPException(400, "INVALID_WALLET_ADDRESS")
        requirement = await self.gateway.requirement()
        fee_payer = requirement.get("extra", {}).get("feePayer")
        if address == fee_payer:
            raise HTTPException(400, "PAYER_MUST_DIFFER_FROM_FEE_SPONSOR")
        signer = UnsignedSigner(address)
        params = await self.params()
        payload = PreparedScheme(signer, params).create_payment_payload(PaymentRequirements.model_validate(requirement))
        if signer.indexes != [payload["paymentIndex"]]:
            raise HTTPException(503, "SINGLE_TRANSFER_PREPARATION_FAILED")
        decoded = [encoding.msgpack_decode(s) for s in payload["paymentGroup"]]
        challenge = self.gateway.challenge(requirement, resource)
        return {
            "challenge": challenge, "unsigned_transactions": payload["paymentGroup"],
            "sign_indexes": signer.indexes, "payment_index": payload["paymentIndex"],
            "transaction_ids": [t.get_txid() for t in decoded],
            "payer": address, "price_usdc": self.cfg.price_usdc,
            "network": self.cfg.network_name, "network_fee_microalgo": sum(t.fee for t in decoded),
            "customer_network_fee_microalgo": decoded[payload["paymentIndex"]].fee,
            "network_fee_sponsored": bool(fee_payer), "expires_at": int(time.time()) + 180,
        }
