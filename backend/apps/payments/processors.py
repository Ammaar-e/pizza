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


class BBPOSProcessor:
    """Card-present payments taken on a BBPOS WisePad 3 through the companion **bbpos** service
    (Stripe Terminal). The POS app creates and collects the charge in bbpos; this processor then
    verifies the bbpos transaction **server-to-server** before the payment is recorded here, so a
    client can never mark an order paid by itself.

    Store configuration:
      payment_config:  {"card_processor": "bbpos", "bbpos_api_url": "https://pos-api.example.com",
                        "bbpos_allow_mock": false}
      payment_secrets: {"bbpos_email": "...", "bbpos_password": "..."}   (encrypted at rest)
    The bbpos service account needs a role with ``pos.charge`` (verification) and, for refunds,
    ``transactions.refund`` (bbpos manager or admin).
    """

    name = "bbpos"
    TOKEN_CACHE = "bbpos-token:{store}"

    def __init__(self, store):
        self.store = store
        cfg = store.payment_config or {}
        self.base = (cfg.get("bbpos_api_url") or "").rstrip("/")
        self.allow_mock = bool(cfg.get("bbpos_allow_mock"))
        secrets = store.payment_secrets or {}
        self.email = secrets.get("bbpos_email")
        self.password = secrets.get("bbpos_password")

    def _require_config(self):
        if not self.base or not self.email or not self.password:
            raise ProcessorNotConfigured("The bbpos card service is not configured for this store "
                                         "(bbpos_api_url, bbpos_email, bbpos_password)")

    def _token(self, fresh: bool = False) -> str:
        from django.core.cache import cache

        key = self.TOKEN_CACHE.format(store=self.store.pk)
        if not fresh and (tok := cache.get(key)):
            return tok
        try:
            r = requests.post(f"{self.base}/api/auth/login", json={"email": self.email, "password": self.password,
                                                                   "client": "MOBILE"},
                              timeout=settings.PROVIDER_HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise ProcessorError(f"Cannot reach the bbpos service: {e.__class__.__name__}")
        if r.status_code != 200:
            raise ProcessorNotConfigured(f"bbpos rejected the service account (HTTP {r.status_code})")
        body = r.json()
        cache.set(key, body["accessToken"], max(60, int(body.get("expiresIn", 900)) - 60))
        return body["accessToken"]

    def _call(self, method: str, path: str, json_body=None) -> requests.Response:
        self._require_config()
        for attempt in (0, 1):
            try:
                r = requests.request(method, f"{self.base}{path}", json=json_body,
                                     headers={"Authorization": f"Bearer {self._token(fresh=attempt == 1)}"},
                                     timeout=settings.PROVIDER_HTTP_TIMEOUT)
            except requests.RequestException as e:
                raise ProcessorError(f"Cannot reach the bbpos service: {e.__class__.__name__}")
            if r.status_code != 401:
                return r
        return r

    def verify(self, reference: str, expected_cents: int, currency: str) -> ProcessorResult:
        if not reference:
            return ProcessorResult(status="failed", failure_reason="A bbpos transaction id is required")
        # POST /confirm makes bbpos re-check the PaymentIntent with Stripe (idempotent).
        r = self._call("POST", f"/api/payments/{reference}/confirm")
        if r.status_code == 404:
            return ProcessorResult(status="failed", failure_reason="Unknown bbpos transaction")
        if r.status_code >= 400:
            raise ProcessorError(f"bbpos verification failed (HTTP {r.status_code})")
        tx = r.json()
        if tx.get("mock") and not self.allow_mock:
            return ProcessorResult(status="failed", failure_reason="Mock card payments are not accepted for this store")
        if tx.get("status") != "SUCCEEDED":
            return ProcessorResult(status="failed", reference=reference,
                                   failure_reason=f"Card payment is {tx.get('status', 'unknown').lower()}")
        if int(tx.get("amountCents", -1)) != expected_cents:
            return ProcessorResult(status="failed", reference=reference,
                                   failure_reason=f"Card charged {tx.get('amountCents')} cents but {expected_cents} "
                                                  "were expected")
        if str(tx.get("currency", "")).upper() != currency.upper():
            return ProcessorResult(status="failed", reference=reference, failure_reason="Currency mismatch")
        return ProcessorResult(status="captured", reference=reference, card_brand=(tx.get("cardBrand") or "")[:20],
                               card_last4=(tx.get("last4") or "")[:4], raw={"bbpos": tx})

    def charge(self, *, amount_cents: int, currency: str, reference: str = "", tip_cents: int = 0,
               **_) -> ProcessorResult:
        return self.verify(reference, amount_cents + (tip_cents or 0), currency)

    def confirm(self, reference: str) -> ProcessorResult:
        return self.verify(reference, -1, self.store.franchisee.headquarters.currency)

    def refund(self, payment, amount_cents: int) -> ProcessorResult:
        import uuid

        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pizza-refund:{payment.pk}:{payment.refunded_cents}:{amount_cents}"))
        r = self._call("POST", f"/api/transactions/{payment.processor_reference}/refunds",
                       {"amountCents": amount_cents, "idempotencyKey": key})
        body = r.json() if r.content else {}
        code = (body.get("error") or {}).get("code")
        if code == "INTERAC_REQUIRES_IN_PERSON":
            return ProcessorResult(status="pending", failure_reason="Interac refunds must be processed in person on "
                                                                    "the card reader (bbpos app → Transactions)")
        if r.status_code >= 400:
            raise ProcessorError((body.get("error") or {}).get("message") or f"bbpos refund failed (HTTP {r.status_code})")
        return ProcessorResult(status="captured", reference=str(body.get("id", "")))


def processor_for(method: str, store):
    if method == "cash":
        return CashProcessor()
    if method == "card":
        provider = (store.payment_config or {}).get("card_processor", "stripe")
        if provider == "stripe":
            return StripeTerminalProcessor(store)
        if provider == "bbpos":
            return BBPOSProcessor(store)
        if provider == "none":
            raise ProcessorNotConfigured("Card payments are not enabled for this store")
        raise ProcessorNotConfigured(f"Unsupported card processor '{provider}'")
    if method == "online":
        return ExternalReferenceProcessor()
    raise ProcessorError(f"Unsupported payment method '{method}'")
