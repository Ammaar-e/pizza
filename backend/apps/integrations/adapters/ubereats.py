"""Uber Eats Marketplace adapter.

Verified against Uber's official developer documentation (developer.uber.com/docs/eats) via
search of the official domain on 2026-11-17 (direct page fetches were not possible from the
build environment — see docs/integrations/ubereats.md):

* OAuth 2.0 client credentials: POST https://auth.uber.com/oauth/v2/token
  (sandbox: https://sandbox-login.uber.com/oauth/v2/token), scopes ``eats.store``, ``eats.order``,
  ``eats.store.orders.read``; tokens last 30 days and the token endpoint is limited to 100
  requests/hour, so tokens are cached.
* Webhooks: all events go to the app's primary webhook URL; ``X-Uber-Signature`` is the
  lowercase hex HMAC-SHA256 of the raw body keyed with the client secret. Respond 200 with an
  empty body or Uber retries. Payload: event_id, event_type, event_time, meta.user_id (store id),
  meta.resource_id (order id), resource_href (GET URL for the order).
* ``orders.notification`` — new order; ``orders.cancel`` (or ``orders.failure`` on API v1.0.0
  stores) — cancellation.
* Accept: POST https://api.uber.com/v1/eats/orders/{order_id}/accept_pos_order
  Deny:   POST https://api.uber.com/v1/eats/orders/{order_id}/deny_pos_order (reason codes such as
  STORE_CLOSED, POS_OFFLINE, ITEM_AVAILABILITY). Must be called within 11.5 minutes of the
  webhook or the order auto-cancels.
* Store details: GET https://api.uber.com/v1/eats/stores/{store_id}

Not enabled until verified in the Uber sandbox (see docs): merchant cancellation request body,
menu upload body, and the complete Get Order Details response schema. The order parser below
is defensive: anything it cannot map is flagged for staff review / rejection — it never guesses.
Access: production scopes require the app to be approved and whitelisted by Uber Eats.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal

from apps.orders.intake import NormalizedCustomer, NormalizedOrder, NormalizedPayment
from apps.orders.models import OrderSource, OrderType

from ..idscheme import RawOption, build_item
from .base import ParsedEvent, ProviderAdapter, ProviderError, WebhookAuthError, safe_json

HOSTS = {
    "production": {"api": "https://api.uber.com", "auth": "https://auth.uber.com/oauth/v2/token"},
    "sandbox": {"api": "https://test-api.uber.com", "auth": "https://sandbox-login.uber.com/oauth/v2/token"},
}
DEFAULT_SCOPES = "eats.store eats.order eats.store.orders.read"
DENY_CODES = {"STORE_CLOSED": "STORE_CLOSED", "ITEM_UNAVAILABLE": "ITEM_AVAILABILITY",
              "ITEM_MAPPING_FAILED": "ITEM_AVAILABILITY", "POS_OFFLINE": "POS_OFFLINE"}


def _money_to_cents(v) -> int | None:
    """Accepts {amount: <major units>} / {amount_e5: int} / bare numbers."""
    if v is None:
        return None
    if isinstance(v, dict):
        if v.get("amount_e5") is not None:
            return int(Decimal(int(v["amount_e5"])) / Decimal(1000))
        if v.get("amount") is not None:
            return int((Decimal(str(v["amount"])) * 100).quantize(Decimal("1")))
        for key in ("unit_price", "total_price", "price"):
            if key in v:
                return _money_to_cents(v[key])
        return None
    if isinstance(v, (int, float, str)):
        return int((Decimal(str(v)) * 100).quantize(Decimal("1")))
    return None


def _qty(v) -> int:
    if isinstance(v, dict):
        v = v.get("amount") or v.get("quantity") or 1
    try:
        return max(1, int(Decimal(str(v))))
    except Exception:
        return 1


class UberEatsAdapter(ProviderAdapter):
    code = "ubereats"
    name = "Uber Eats"
    account_secret_fields = ("client_secret",)
    account_fields = ("client_id",)
    ack_deadline_seconds = 690  # 11.5 minutes
    webhook_success_status = 200
    supports_ready_signal = False
    supports_merchant_cancel = False
    supports_menu_push = False

    def hosts(self, si=None, account=None) -> dict:
        acc = account or (si.account if si else None)
        return HOSTS["production" if acc and acc.environment == "production" else "sandbox"]

    # ---- auth -----------------------------------------------------------------
    def access_token(self, si) -> str:
        creds = self.account_credentials(si)
        account = si.account
        cache = account.token_cache or {}
        if cache.get("access_token") and cache.get("expires_at", 0) > time.time() + 300:
            return cache["access_token"]
        resp = self.http(si).request(
            "POST", self.hosts(si)["auth"], operation="oauth_token",
            data={"client_id": creds["client_id"], "client_secret": creds["client_secret"],
                  "grant_type": "client_credentials", "scope": creds.get("scopes") or DEFAULT_SCOPES},
            expected=(200,))
        body = safe_json(resp)
        token = body.get("access_token")
        if not token:
            raise ProviderError("Uber token response did not include access_token", retryable=False)
        account.token_cache = {"access_token": token, "expires_at": time.time() + int(body.get("expires_in", 3600))}
        account.save(update_fields=["token_cache", "updated_at"])
        return token

    def headers(self, si) -> dict:
        return {"Authorization": f"Bearer {self.access_token(si)}", "Content-Type": "application/json"}

    def verify_webhook(self, request, raw_body: bytes, account) -> None:
        secret = (account.credentials or {}).get("client_secret") if account else None
        if not secret:
            raise WebhookAuthError("Uber Eats client secret not configured for this account")
        sig = request.headers.get("X-Uber-Signature", "")
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(sig.strip().lower(), expected):
            raise WebhookAuthError("Invalid X-Uber-Signature")

    # ---- events ---------------------------------------------------------------
    def parse_events(self, payload: dict) -> list[ParsedEvent]:
        etype = payload.get("event_type") or ""
        event_id = str(payload.get("event_id") or "")
        meta = payload.get("meta") or {}
        if not event_id:
            raise ValueError("Uber webhook without event_id")
        order_id = str(meta.get("resource_id") or "")
        store_id = str(meta.get("user_id") or "")
        if etype == "orders.notification":
            kind = "order_new"
        elif etype in ("orders.cancel", "orders.failure"):
            kind = "order_cancelled"
        else:
            kind = "ignored"
        return [ParsedEvent(event_id, etype, kind, order_id, store_id, payload)]

    def fetch_order(self, si, event) -> dict:
        href = (event.payload or {}).get("resource_href") or ""
        api = self.hosts(si)["api"]
        allowed = tuple(h["api"] + "/" for h in HOSTS.values())
        if not href.startswith(allowed):
            raise ProviderError(f"Refusing to fetch order from untrusted resource_href '{href[:100]}'",
                                retryable=False)
        resp = self.http(si).request("GET", href, operation="get_order", headers=self.headers(si), expected=(200,))
        body = safe_json(resp)
        if not body:
            raise ProviderError("Empty order body from Uber Eats", retryable=True)
        body.setdefault("_api_base", api)
        return body

    # ---- normalization ---------------------------------------------------------
    def _options(self, groups: list) -> list[RawOption]:
        out = []
        for g in groups or []:
            for sel in g.get("selected_items") or []:
                out.append(RawOption(
                    external_id=str(sel.get("external_data") or sel.get("external_id") or sel.get("id") or ""),
                    name=sel.get("title") or sel.get("name") or "",
                    quantity=_qty(sel.get("quantity")),
                    price_cents=_money_to_cents(sel.get("price")),
                    children=self._options(sel.get("selected_modifier_groups") or []),
                ))
        return out

    def normalize(self, si, raw: dict) -> NormalizedOrder:
        carts = raw.get("carts") or ([raw["cart"]] if raw.get("cart") else [])
        raw_items = [it for c in carts for it in (c.get("items") or [])]
        if not raw_items:
            raise ProviderError("Uber Eats order payload contained no recognizable cart items; the order "
                                "schema must be verified against the Uber sandbox", retryable=False)
        mappings, skus = self.mappings(si), self.known_skus()
        items = [build_item(
            external_id=str(it.get("external_data") or it.get("external_id") or it.get("id") or ""),
            name=it.get("title") or it.get("name") or "", quantity=_qty(it.get("quantity")),
            unit_price_cents=_money_to_cents(it.get("price")),
            options=self._options(it.get("selected_modifier_groups") or []),
            special_instructions=it.get("special_instructions") or "", mappings=mappings, known_skus=skus,
        ) for it in raw_items]
        ftype = str(raw.get("type") or raw.get("fulfillment_type") or "").upper()
        if "PICK" in ftype:
            order_type = OrderType.PICKUP
        elif "DINE" in ftype:
            order_type = OrderType.DINE_IN
        elif "RESTAURANT" in ftype or "MERCHANT" in ftype:
            order_type = OrderType.DELIVERY
        else:
            order_type = OrderType.PROVIDER_DELIVERY
        eater = raw.get("eater") or (raw.get("customers") or [{}])[0] or {}
        charges = (raw.get("payment") or {}).get("charges") or {}
        totals = {k: _money_to_cents(charges.get(src)) for k, src in
                  (("subtotal_cents", "sub_total"), ("tax_cents", "tax"), ("total_cents", "total"))
                  if charges.get(src) is not None}
        return NormalizedOrder(
            source=OrderSource.UBER_EATS,
            store_id=si.store_id,
            order_type=order_type,
            items=items,
            external_order_id=str(raw.get("id") or ""),
            external_display_id=str(raw.get("display_id") or "")[:50],
            customer=NormalizedCustomer(
                name=" ".join(x for x in [eater.get("first_name"), eater.get("last_name")] if x)
                or eater.get("name", ""),
                phone=str(eater.get("phone") or ""), external_id=str(eater.get("id") or "")),
            special_instructions=(raw.get("cart") or {}).get("special_instructions") or "",
            payment=NormalizedPayment(status="provider_paid", method="ubereats",
                                      amount_cents=totals.get("total_cents") or 0, reference=str(raw.get("id") or "")),
            provider_totals=totals,
            provider_ack_deadline=self.deadline(),
            raw=raw,
        )

    # ---- order actions ------------------------------------------------------------
    def _api(self, si) -> str:
        return self.hosts(si)["api"]

    def accept(self, si, order) -> str:
        self.http(si, order).request(
            "POST", f"{self._api(si)}/v1/eats/orders/{order.external_order_id}/accept_pos_order",
            operation="accept_pos_order", headers=self.headers(si),
            json_body={"reason": "accepted", "external_reference_id": str(order.pk)}, expected=(200, 204))
        return "accepted"

    def reject(self, si, order, reason_code: str, message: str) -> str:
        code = DENY_CODES.get(reason_code, "ITEM_AVAILABILITY" if "ITEM" in reason_code else "STORE_CLOSED"
                              if "CLOSED" in reason_code else "POS_OFFLINE")
        self.http(si, order).request(
            "POST", f"{self._api(si)}/v1/eats/orders/{order.external_order_id}/deny_pos_order",
            operation="deny_pos_order", headers=self.headers(si),
            json_body={"reason": {"explanation": message[:300], "code": code}}, expected=(200, 204))
        return f"denied:{code}"

    def test_connection(self, si) -> str:
        self.access_token(si)
        if not si.external_store_id:
            raise ProviderError("Uber Eats store id not set", retryable=False)
        resp = self.http(si).request("GET", f"{self._api(si)}/v1/eats/stores/{si.external_store_id}",
                                     operation="test_connection", headers=self.headers(si), expected=(200,))
        name = safe_json(resp).get("name") or "store"
        return f"Authenticated; Uber Eats store '{name}' reachable"
