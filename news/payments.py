"""x402 v2, facilitator verification and settlement with durable replay handling.

Never treats HTTP 200 alone as a successful settlement. Unknown settlement states
are held for operator reconciliation, never automatically charged again.
"""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import logging
import re
from algosdk import encoding, transaction
from nacl.signing import VerifyKey
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import httpx
from x402.schemas import PaymentPayload, PaymentRequirements
from x402.extensions.bazaar import declare_discovery_extension

LOG = logging.getLogger("tradingnews.payments")


def encoded(data):
    return base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def decode_payment(token):
    if not token or len(token) > 32768:
        raise HTTPException(400, "INVALID_PAYMENT_SIGNATURE")
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
        raise HTTPException(400, "INVALID_PAYMENT_SIGNATURE_FORMAT") from None


def validate_transfer(payload, cfg, requirement):
    """Verify the signed bytes, not only client-controlled x402 metadata."""
    try:
        raw = payload["payload"]
        sponsor = requirement.get("extra", {}).get("feePayer")
        pi = 1 if sponsor else 0
        if not (raw["paymentIndex"] == pi and len(raw["paymentGroup"]) == pi + 1): raise ValueError("Invalid payment")
        group = [encoding.msgpack_decode(x) for x in raw["paymentGroup"]]
        signed = group[pi]
        if not (isinstance(signed, transaction.SignedTransaction) and not signed.authorizing_address): raise ValueError("Invalid payment")
        txn = signed.transaction
        if not (txn.type == "axfer" and txn.receiver == cfg.pay_to): raise ValueError("Invalid payment")
        if not (txn.amount == int(cfg.amount) and str(txn.index) == cfg.asset): raise ValueError("Invalid payment")
        if not (not txn.rekey_to and not txn.close_assets_to and not txn.revocation_target and not txn.lease): raise ValueError("Invalid payment")
        if not (txn.genesis_hash == cfg.network.split(":", 1)[1]): raise ValueError("Invalid payment")
        if not (0 < txn.last_valid_round - txn.first_valid_round <= 1000): raise ValueError("Invalid payment")
        if not (txn.sender != sponsor): raise ValueError("Invalid payment")
        VerifyKey(encoding.decode_address(txn.sender)).verify(
            b"TX" + base64.b64decode(encoding.msgpack_encode(txn)), base64.b64decode(signed.signature))
        txns = [txn]
        if sponsor:
            f = group[0]
            if not (isinstance(f, transaction.PaymentTxn)): raise ValueError("Invalid payment")
            if not (f.sender == f.receiver == sponsor and f.amt == 0): raise ValueError("Invalid payment")
            if not (not f.rekey_to and not f.close_remainder_to and not f.lease): raise ValueError("Invalid payment")
            if not (2000 <= f.fee <= 20000 and txn.fee == 0): raise ValueError("Invalid payment")
            if not (f.genesis_hash == txn.genesis_hash and f.first_valid_round == txn.first_valid_round): raise ValueError("Invalid payment")
            if not (f.last_valid_round == txn.last_valid_round and txn.group and f.group == txn.group): raise ValueError("Invalid payment")
            txns = [f, txn]
            ungrouped = deepcopy(txns)
            for t in ungrouped: t.group = None
            if not (transaction.calculate_group_id(ungrouped) == txn.group): raise ValueError("Invalid payment")
        else:
            if not (1000 <= txn.fee <= 10000 and not txn.group): raise ValueError("Invalid payment")
        return txn, [t.get_txid() for t in txns]
    except Exception:
        raise HTTPException(403, "INVALID_PAYMENT_TRANSACTION") from None


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
                    raise HTTPException(503, "FACILITATOR_UNAVAILABLE") from None
        kind = next((k for k in supported["kinds"] if k.get("x402Version") == 2 and
                     k.get("scheme") == "exact" and k.get("network") == cfg.network), None)
        if not kind:
            raise HTTPException(503, "UNSUPPORTED_FACILITATOR_NETWORK")
        extra = {"decimals": 6, "tag": "x402-global-challenge"}
        if kind.get("extra", {}).get("feePayer"):
            extra["feePayer"] = kind["extra"]["feePayer"]
        return PaymentRequirements(scheme="exact", network=cfg.network, asset=cfg.asset,
                                   amount=cfg.amount, pay_to=cfg.pay_to, max_timeout_seconds=300,
                                   extra=extra).model_dump(by_alias=True)

    def challenge(self, requirement, resource):
        extensions = declare_discovery_extension(input={}, input_schema={"type": "object", "properties": {}})
        extensions["bazaar"]["info"]["input"]["method"] = "GET"
        schema_input = extensions["bazaar"]["schema"]["properties"]["input"]
        schema_input["properties"]["method"] = {"type": "string", "enum": ["GET"]}
        schema_input.setdefault("required", []).append("method")
        extensions["x402-merchant"] = {"info": {"name": "Trading News"}, "schema": {
            "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}
        extensions["bazaar"]["info"].update({"name": "Trading News", "tags": ["x402-global-challenge", "news", "crypto"],
                                               "description": "Selected asset news with source links and provenance"})
        return {"x402Version": 2, "resource": {"url": resource, "description": "Trading News · ranked asset news report",
                                               "mimeType": "application/json"},
                "accepts": [requirement], "extensions": extensions}

    def existing(self, fingerprint, resource, proof):
        row = self.store.payment(fingerprint)
        if not row:
            return None
        if not hmac.compare_digest(row["proof"], proof):
            raise HTTPException(403, "PAYMENT_PROOF_MISMATCH")
        if row["resource"] != resource:
            raise HTTPException(409, "PAYMENT_BELONGS_TO_ANOTHER_RESOURCE")
        if row["state"] == "settled":
            body, receipt = json.loads(row["body"]), json.loads(row["receipt"])
            body["billing"] = {"charged": True, "replayed": True, "receipt": receipt}
            return JSONResponse(body, headers={"PAYMENT-RESPONSE": encoded(receipt), "Cache-Control": "no-store"})
        if row["state"] == "invalid":
            raise HTTPException(403, "PAYMENT_REJECTED")
        if row["state"] == "expired":
            raise HTTPException(410, "PAYMENT_EXPIRED_NO_CHARGE")
        raise HTTPException(409, {"code": "PAYMENT_PENDING_RECONCILIATION", "state": row["state"],
                                  "message": "Do not create a new payment. Recover the same request or reconcile its on-chain receipt."})

    async def status(self, token):
        """Reconcile the same signed transfer without ever submitting it again.

        Only an indexer response past LastValid, with no matching transaction,
        proves it is safe to release an expired purchase. A timeout, a lagging
        indexer, or an algod 404 alone is not proof that no payment occurred.
        """
        cfg = self.settings
        payload, fingerprint, proof = decode_payment(token)
        row = self.store.payment(fingerprint)
        resource = payload.get("resource", {}).get("url")
        if row and (not hmac.compare_digest(row["proof"], proof) or row["resource"] != resource):
            raise HTTPException(403, "PAYMENT_PROOF_MISMATCH")
        # A price change must not prevent recovery of a previously signed purchase.
        try:
            original_price = format(Decimal(payload["accepted"]["amount"]) / 1_000_000, "f")
        except (KeyError, InvalidOperation, ValueError):
            raise HTTPException(400, "INVALID_PAYMENT_TRANSACTION") from None
        txn, _ = validate_transfer(payload, replace(cfg, price_usdc=original_price), payload["accepted"])
        txid = txn.get_txid()
        result = {"transaction": txid, "can_retry": False, "state": "unknown"}

        def confirmed():
            if not row:
                return {**result, "state": "confirmed_without_report"}
            receipt = {"success": True, "network": cfg.network, "transaction": txid, "payer": txn.sender}
            self.store.payment_state(fingerprint, "settled", receipt)
            return {**result, "state": "settled"}

        if row and row["state"] == "settled":
            return {**result, "state": "settled"}
        try:
            response = await self.http.get(
                f"https://{cfg.network_name}-api.algonode.cloud/v2/transactions/pending/{txid}", timeout=10)
            if response.status_code == 200 and response.json().get("confirmed-round", 0) > 0:
                return confirmed()
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
        try:
            response = await self.http.get(f"https://{cfg.network_name}-idx.algonode.cloud/v2/transactions",
                                           params={"txid": txid}, timeout=12)
            response.raise_for_status()
            indexed = response.json()
            transactions = indexed.get("transactions")
            current_round = indexed.get("current-round")
            if isinstance(transactions, list):
                if any(t.get("id") == txid and t.get("confirmed-round", 0) > 0 for t in transactions):
                    return confirmed()
                if not transactions and type(current_round) is int and current_round > txn.last_valid_round:
                    # Recheck in case a concurrent recovery just stored the receipt.
                    latest = self.store.payment(fingerprint)
                    if latest and latest["state"] == "settled":
                        return {**result, "state": "settled"}
                    if row:
                        self.store.payment_state(fingerprint, "expired")
                    return {**result, "state": "expired_not_paid", "can_retry": True}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
        return result

    async def access(self, token, resource, make_body):
        cfg = self.settings
        if not cfg.payments:
            raise HTTPException(503, "PURCHASES_DISABLED")
        payload = fingerprint = None
        if token:
            payload, fingerprint, proof = decode_payment(token)
            row = self.store.payment(fingerprint)
            if row and row["state"] not in {"settled", "invalid", "expired"} and row["resource"] == resource and hmac.compare_digest(row["proof"], proof):
                # A lost facilitator response must never trigger a second settlement.
                txn = encoding.msgpack_decode(payload["payload"]["paymentGroup"][payload["payload"]["paymentIndex"]]).transaction
                try:
                    response = await self.http.get(f"https://{cfg.network_name}-api.algonode.cloud/v2/transactions/pending/{txn.get_txid()}", timeout=12)
                    pending = response.json() if response.status_code == 200 else {}
                    if pending.get("confirmed-round", 0) > 0:
                        receipt = {"success": True, "network": cfg.network, "transaction": txn.get_txid(), "payer": txn.sender}
                        self.store.payment_state(fingerprint, "settled", receipt)
                except (httpx.HTTPError, ValueError, TypeError):
                    pass
            replay = self.existing(fingerprint, resource, proof)
            if replay is not None:
                return replay
        requirement = await self.requirement()
        if not token:
            challenge = self.challenge(requirement, resource)
            return JSONResponse(challenge, status_code=402, headers={"PAYMENT-REQUIRED": encoded(challenge),
                                                                     "Cache-Control": "no-store"})
        if payload["accepted"] != requirement or payload.get("resource", {}).get("url") != resource:
            raise HTTPException(403, "PAYMENT_REQUIREMENTS_MISMATCH")
        txn, txids = validate_transfer(payload, cfg, requirement)
        payload["extensions"] = {**payload.get("extensions", {}), **self.challenge(requirement, resource)["extensions"]}
        # Verify before spending provider quotas; no settlement has occurred yet.
        request_body = {"x402Version": 2, "paymentPayload": payload, "paymentRequirements": requirement}
        try:
            response = await self.http.post(cfg.facilitator + "/verify", json=request_body, timeout=15)
            response.raise_for_status()
            verification = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning("Payment verification unavailable tx=%s error=%s http=%s", txn.get_txid(),
                        type(exc).__name__, getattr(getattr(exc, "response", None), "status_code", "-"))
            raise HTTPException(503, "VERIFICATION_UNAVAILABLE_NO_SETTLEMENT") from None
        if not isinstance(verification, dict) or verification.get("isValid") is not True:
            reason = verification.get("invalidReason", "unknown") if isinstance(verification, dict) else "invalid_response"
            safe_reason = reason if isinstance(reason, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", reason) else "unspecified"
            LOG.warning("Payment verification rejected tx=%s reason=%s", txn.get_txid(), safe_reason)
            raise HTTPException(403, "FACILITATOR_REJECTED_PAYMENT")
        # Fetch before payment: failures and empty results must not trigger settlement.
        body = await make_body()
        if not body.get("best_article"):
            raise HTTPException(503, "NO_TODAY_NEWS")
        if not self.store.claim(fingerprint, resource, body, proof):
            return self.existing(fingerprint, resource, proof)
        self.store.payment_state(fingerprint, "settling")
        try:
            response = await self.http.post(cfg.facilitator + "/settle", json=request_body, timeout=40)
            response.raise_for_status()
            receipt = response.json()
            if not isinstance(receipt, dict) or receipt.get("success") is not True or not re.fullmatch(r"[A-Z2-7]{52}", str(receipt.get("transaction", ""))) or receipt.get("network") != cfg.network or receipt["transaction"] not in txids:
                LOG.warning("Payment settlement unconfirmed tx=%s", txn.get_txid())
                self.store.payment_state(fingerprint, "settlement_unconfirmed")
                raise HTTPException(502, "SETTLEMENT_UNCONFIRMED_RECOVER_OR_RECONCILE")
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning("Payment settlement unknown tx=%s error=%s http=%s", txn.get_txid(),
                        type(exc).__name__, getattr(getattr(exc, "response", None), "status_code", "-"))
            self.store.payment_state(fingerprint, "settlement_unknown")
            raise HTTPException(502, "SETTLEMENT_UNKNOWN_RECOVER_OR_RECONCILE") from None
        self.store.payment_state(fingerprint, "settled", receipt)
        result = deepcopy(body)
        result["billing"] = {"charged": True, "replayed": False, "receipt": receipt}
        return JSONResponse(result, headers={"PAYMENT-RESPONSE": encoded(receipt), "Cache-Control": "no-store"})
