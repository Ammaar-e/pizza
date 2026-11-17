"""Payment processor adapters.

* CashProcessor – in-store cash with change calculation.
* StripeTerminalProcessor – card-present payments through Stripe Terminal
  (PaymentIntent with ``card_present``). The POS never sees card data; the
  reader tokenizes the card and Stripe returns a PaymentIntent we confirm/capture.
  Requires the store's ``payment_secrets['stripe_secret_key']``.
* ExternalReferenceProcessor – records an online payment captured elsewhere
  (e.g. website checkout) by its gateway reference; never card data.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings


class ProcessorError(Exception):
    pass


class ProcessorNotConfigured(ProcessorError):
    pass


@dataclass
class ProcessorResult:
    status: str  # authorized | captured | failed | pending
    reference: str = ""
    card_brand: str = ""
    card_last4: str = ""
    failure_reason: str = ""
    raw: dict | None = None


class CashProcessor:
    name = "cash"

    def charge(self, *, amount_cents: int, tendered_cents: int | None, **_) -> ProcessorResult:
        if tendered_cents is None or tendered_cents < amount_cents:
            return ProcessorResult(status="failed", failure_reason="Tendered cash is less than amount due")
        return ProcessorResult(status="captured")

    def refund(self, payment, amount_cents: int) -> ProcessorResult:
        return ProcessorResult(status="captured")


class ExternalReferenceProcessor:
    name = "external"

    def charge(self, *, amount_cents: int, reference: str = "", **_) -> ProcessorResult:
        if not reference:
            return ProcessorResult(status="failed", failure_reason="Online payment reference is required")
        return ProcessorResult(status="captured", reference=reference)

    def refund(self, payment, amount_cents: int) -> ProcessorResult:
        # The refund must be executed in the originating gateway; we record it as pending
        # until reconciled so staff can see it is not yet confirmed.
        return ProcessorResult(status="pending", failure_reason="Refund must be completed in the online gateway")


class StripeTerminalProcessor:
    """Stripe Terminal card-present flow (https://docs.stripe.com/terminal)."""

    name = "stripe"
    API = "https://api.stripe.com/v1"

    def __init__(self, store):
        self.store = store
        self.secret = (store.payment_secrets or {}).get("stripe_secret_key")
        self.reader_id = (store.payment_config or {}).get("stripe_reader_id")

    def _auth(self):
        if not self.secret:
            raise ProcessorNotConfigured("Card processing is not configured for this store")
        return (self.secret, "")

    def _post(self, path: str, data: dict, idempotency_key: str | None = None) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        r = requests.post(f"{self.API}{path}", data=data, auth=self._auth(), headers=headers,
                          timeout=settings.PROVIDER_HTTP_TIMEOUT)
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            raise ProcessorError(body.get("error", {}).get("message", f"Stripe error {r.status_code}"))
        return body

    def charge(self, *, amount_cents: int, currency: str, idempotency_key: str, order_ref: str,
               **_) -> ProcessorResult:
        """Create a card_present PaymentIntent and hand it to the configured reader.

        The payment completes asynchronously on the reader; status is 'pending'
        until ``confirm`` observes the captured PaymentIntent.
        """
        pi = self._post("/payment_intents", {
            "amount": amount_cents, "currency": currency.lower(), "payment_method_types[]": "card_present",
            "capture_method": "automatic", "metadata[order]": order_ref,
        }, idempotency_key=idempotency_key)
        if not self.reader_id:
            raise ProcessorNotConfigured("No Stripe Terminal reader configured for this store")
        self._post(f"/terminal/readers/{self.reader_id}/process_payment_intent", {"payment_intent": pi["id"]})
        return ProcessorResult(status="pending", reference=pi["id"], raw={"payment_intent": pi.get("id")})

    def confirm(self, reference: str) -> ProcessorResult:
        r = requests.get(f"{self.API}/payment_intents/{reference}", auth=self._auth(),
                         params={"expand[]": "latest_charge"}, timeout=settings.PROVIDER_HTTP_TIMEOUT)
        pi = r.json()
        if r.status_code >= 400:
            raise ProcessorError(pi.get("error", {}).get("message", "Stripe error"))
        if pi.get("status") == "succeeded":
            charge = pi.get("latest_charge") or {}
            card = (charge.get("payment_method_details") or {}).get("card_present") or {}
            return ProcessorResult(status="captured", reference=reference, card_brand=card.get("brand", ""),
                                   card_last4=card.get("last4", ""))
        if pi.get("status") in ("canceled", "requires_payment_method") and pi.get("last_payment_error"):
            return ProcessorResult(status="failed", reference=reference,
                                   failure_reason=pi["last_payment_error"].get("message", "Declined"))
        return ProcessorResult(status="pending", reference=reference)

    def refund(self, payment, amount_cents: int) -> ProcessorResult:
        body = self._post("/refunds", {"payment_intent": payment.processor_reference, "amount": amount_cents})
        return ProcessorResult(status="captured" if body.get("status") == "succeeded" else "pending",
                               reference=body.get("id", ""))


def processor_for(method: str, store):
    if method == "cash":
        return CashProcessor()
    if method == "card":
        provider = (store.payment_config or {}).get("card_processor", "stripe")
        if provider == "stripe":
            return StripeTerminalProcessor(store)
        raise ProcessorNotConfigured(f"Unsupported card processor '{provider}'")
    if method == "online":
        return ExternalReferenceProcessor()
    raise ProcessorError(f"Unsupported payment method '{method}'")
