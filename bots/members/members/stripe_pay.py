"""Stripe Checkout without the Stripe SDK: one POST to create a session, HMAC to verify webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx


class StripeError(Exception):
    pass


class Stripe:
    def __init__(self, secret_key: str, webhook_secret: str, client: httpx.Client | None = None):
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret
        self.client = client or httpx.Client(timeout=20)

    def checkout_url(
        self,
        *,
        amount_cents: int,
        currency: str,
        product_name: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
    ) -> str:
        data = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": product_name,
            "client_reference_id": metadata.get("telegram_user_id", ""),
        }
        for k, v in metadata.items():
            data[f"metadata[{k}]"] = v
        r = self.client.post("https://api.stripe.com/v1/checkout/sessions", data=data, auth=(self.secret_key, ""))
        body = r.json()
        if r.status_code >= 400:
            raise StripeError(body.get("error", {}).get("message", f"HTTP {r.status_code}"))
        return body["url"]

    def verify_webhook(self, payload: bytes, signature_header: str, tolerance: int = 300, now: float | None = None) -> dict:
        """Returns the event dict or raises StripeError. Implements Stripe's v1 signing scheme."""
        parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
        timestamp, v1 = parts.get("t"), parts.get("v1")
        if not timestamp or not v1:
            raise StripeError("Semnătură Stripe lipsă")
        now = now if now is not None else time.time()
        if abs(now - int(timestamp)) > tolerance:
            raise StripeError("Semnătură Stripe expirată")
        expected = hmac.new(self.webhook_secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, v1):
            raise StripeError("Semnătură Stripe invalidă")
        return json.loads(payload)

    @staticmethod
    def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
        """Builds a Stripe-Signature header (used by tests and the Stripe CLI-less local check)."""
        timestamp = timestamp or int(time.time())
        digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"
