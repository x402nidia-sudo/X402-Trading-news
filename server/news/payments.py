"""x402 v2, facilitator verification and settlement with durable replay handling.

Never treats HTTP 200 alone as a successful settlement. Unknown settlement states
are held for operator reconciliation, never automatically charged again.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
import hmac
import json
import re
from algosdk import encoding
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import httpx
from x402.schemas import PaymentPayload, PaymentRequirements
from x402.extensions.bazaar import declare_discovery_extension
from .service import NoProviders


def encoded(data):
    return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def decode_payment(token):
    if not token or len(token) > 32768:
        raise HTTPException(400, "Invalid PAYMENT-SIGNATURE")
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        payload = PaymentPayload.model_validate(json.loads(raw))
        if payload.x402_version != 2:
            raise ValueError()
        group = payload.payload["paymentGroup"]
        index = payload.payload["paymentIndex"]
        if not isinstance(group, list) or not 1 <= len(group) <= 16 or type(index) is not int or not 0 <= index < len(group):
            raise ValueError()
        txn = encoding.msgpack_decode(group[index])
        # Identity is the signed payment transaction, independent of JSON formatting.
        if not hasattr(txn, "transaction") or not getattr(txn, "signature", None):
            raise ValueError()
        txid = txn.transaction.get_txid()
        fingerprint = hashlib.sha256((payload.accepted.network + ":" + txid).encode()).hexdigest()
        proof = hashlib.sha256(b"|".join(base64.b64decode(part, validate=True) for part in group)).hexdigest()
        return payload.model_dump(by_alias=True, exclude_none=True), fingerprint, proof
    except Exception:
        # Deliberately hide signature bytes, raw decoder errors and secrets.
        raise HTTPException(400, "Could not validate the PAYMENT-SIGNATURE format") from None


class PaymentGateway:
    def __init__(self, settings, store, http):
        self.settings, self.store, self.http = settings, store, http
        self.requirements_lock = asyncio.Lock()

    async def requirement(self):
        cfg = self.settings
        cache_key = "facilitator:" + cfg.facilitator + ":" + cfg.network
        async with self.requirements_lock:
            supported = self.store.cached(cache_key, 3600)
            if not supported:
                try:
                    response = await self.http.get(cfg.facilitator + "/supported", timeout=12)
                    response.raise_for_status()
                    supported = response.json()
                    if not isinstance(supported.get("kinds"), list):
                        raise ValueError()
                    self.store.cache(cache_key, supported)
                except (httpx.HTTPError, ValueError, AttributeError):
                    raise HTTPException(503, "Facilitator unavailable; you have not been charged") from None
        kind = next((k for k in supported["kinds"] if k.get("x402Version") == 2 and
                     k.get("scheme") == "exact" and k.get("network") == cfg.network), None)
        if not kind:
            raise HTTPException(503, "The facilitator does not advertise support for this network")
        extra = {"decimals": 6, "tag": "x402-global-challenge"}
        if kind.get("extra", {}).get("feePayer"):
            extra["feePayer"] = kind["extra"]["feePayer"]
        return PaymentRequirements(scheme="exact", network=cfg.network, asset=cfg.asset,
                                   amount=cfg.amount, pay_to=cfg.pay_to, max_timeout_seconds=300,
                                   extra=extra).model_dump(by_alias=True)

    def challenge(self, requirement, resource):
        extensions = declare_discovery_extension(input={}, input_schema={"type": "object", "properties": {}})
        extensions["bazaar"]["info"]["input"]["method"] = "GET"
        extensions["bazaar"]["info"].update({"name": "Trading News", "tags": ["x402-global-challenge", "news", "crypto"],
                                               "description": "Selected asset news with source links and provenance"})
        return {"x402Version": 2, "resource": {"url": resource, "description": "Trading News · per-asset report",
                                               "mimeType": "application/json"},
                "accepts": [requirement], "extensions": extensions}

    def existing(self, fingerprint, resource, proof):
        row = self.store.payment(fingerprint)
        if not row:
            return None
        if not hmac.compare_digest(row["proof"], proof):
            raise HTTPException(403, "The signature does not match the original payment")
        if row["resource"] != resource:
            raise HTTPException(409, "This payment belongs to a different resource")
        if row["state"] == "no_charge":
            body = json.loads(row["body"])
            body["billing"]["replayed"] = True
            return self.uncharged_response(body)
        if row["state"] == "settled":
            body, receipt = json.loads(row["body"]), json.loads(row["receipt"])
            body["billing"] = {"charged": True, "replayed": True, "receipt": receipt}
            return JSONResponse(body, headers={"PAYMENT-RESPONSE": encoded(receipt), "Cache-Control": "no-store"})
        if row["state"] == "invalid":
            raise HTTPException(403, "Payment rejected; no content was delivered")
        raise HTTPException(409, {"code": "PAYMENT_PENDING_RECONCILIATION", "state": row["state"],
                                  "message": "Do not create another payment automatically. Check the receipt on the network."})

    @staticmethod
    def uncharged_response(body):
        headers = {"Cache-Control": "no-store"}
        if body.get("status") == "unavailable":
            headers["Retry-After"] = str(body["retry_after"])
            return JSONResponse(body, status_code=503, headers=headers)
        return JSONResponse(body, headers=headers)

    async def access(self, token, resource, make_body):
        cfg = self.settings
        if not cfg.payments:
            raise HTTPException(503, "Purchases have not been enabled yet")
        payload = fingerprint = None
        if token:
            payload, fingerprint, proof = decode_payment(token)
            replay = self.existing(fingerprint, resource, proof)
            if replay is not None:
                return replay
        requirement = await self.requirement()
        if not token:
            challenge = self.challenge(requirement, resource)
            return JSONResponse(challenge, status_code=402, headers={"PAYMENT-REQUIRED": encoded(challenge),
                                                                     "Cache-Control": "no-store"})
        if payload["accepted"] != requirement or payload.get("resource", {}).get("url") != resource:
            raise HTTPException(403, "The network, price, recipient or resource does not match the advertised terms")
        # Verify before spending provider quotas; no settlement has occurred yet.
        request_body = {"x402Version": 2, "paymentPayload": payload, "paymentRequirements": requirement}
        try:
            response = await self.http.post(cfg.facilitator + "/verify", json=request_body, timeout=15)
            response.raise_for_status()
            verification = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Verification unavailable; no settlement was requested") from None
        if not isinstance(verification, dict) or verification.get("isValid") is not True:
            raise HTTPException(403, "The facilitator rejected the payment")
        # Fetch before payment: failures and empty results must not trigger settlement.
        try:
            body = await make_body()
        except NoProviders as exc:
            body = {"status": "unavailable", "detail": "No report is available. You have not been charged; please try again later.",
                    "providers": exc.states, "retry_after": exc.retry_after,
                    "billing": {"charged": False, "reason": "sources_unavailable"}}
        if not (body.get("best_article") or (body.get("kind") == "news_feed" and body.get("articles"))) or body.get("freshness", {}).get("status") == "stale":
            # Freeze this authorization's free result; a later retry cannot charge it.
            if not self.store.claim(fingerprint, resource, body, proof):
                return self.existing(fingerprint, resource, proof)
            self.store.payment_state(fingerprint, "no_charge")
            return self.uncharged_response(body)
        if not self.store.claim(fingerprint, resource, body, proof):
            return self.existing(fingerprint, resource, proof)
        self.store.payment_state(fingerprint, "settling")
        try:
            response = await self.http.post(cfg.facilitator + "/settle", json=request_body, timeout=40)
            response.raise_for_status()
            receipt = response.json()
            decoded_group = [encoding.msgpack_decode(part) for part in payload["payload"]["paymentGroup"]]
            transactions = [getattr(part, "transaction", part) for part in decoded_group]
            sender = transactions[payload["payload"]["paymentIndex"]].sender
            if not isinstance(receipt, dict) or receipt.get("success") is not True or receipt.get("transaction") not in {txn.get_txid() for txn in transactions} or receipt.get("network") != cfg.network or receipt.get("payer", sender) != sender:
                self.store.payment_state(fingerprint, "settlement_unconfirmed")
                raise HTTPException(502, "Settlement is unconfirmed; the report has not been delivered. Do not repeat the payment automatically.")
        except (httpx.HTTPError, ValueError):
            self.store.payment_state(fingerprint, "settlement_unknown")
            raise HTTPException(502, "The payment status is unknown; reconciliation is required before another attempt") from None
        self.store.payment_state(fingerprint, "settled", receipt)
        result = deepcopy(body)
        result["billing"] = {"charged": True, "replayed": False, "receipt": receipt}
        return JSONResponse(result, headers={"PAYMENT-RESPONSE": encoded(receipt), "Cache-Control": "no-store"})
