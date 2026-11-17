"""DoorDash Marketplace adapter.

Implemented from DoorDash's official developer documentation
(developer.doordash.com/en-US/docs/marketplace/...), read 2026-11-17:

* Auth to DoorDash: JWT (HS256) with header {"alg":"HS256","typ":"JWT","dd-ver":"DD-JWT-V1"},
  claims {aud:"doordash", iss:<developer_id>, kid:<key_id>, iat, exp<=iat+1800}, signed with the
  base64url-decoded signing secret; sent as ``Authorization: Bearer <jwt>`` plus ``auth-version: v2``
  and a CamelCase ``User-Agent``.            (overview/getting_started/jwts_getting_started)
* Orders webhook: DoorDash POSTs ``{"event":{"type":"OrderCreate","status":"NEW"},"order":{...}}`` to
  the endpoint configured in the Developer Portal, authenticated by the auth token configured
  with the webhook subscription.              (how_to/order_integration, create_webhook_subscription)
* Async confirmation: respond 202, then ``PATCH /marketplace/api/v1/orders/{id}`` with
  ``{merchant_supplied_id, order_status: success|fail, failure_reason, prep_time?}`` within the
  3–8 minute SLA.                              (how_to/order_integration)
* Cancellation webhook: ``{external_order_id, client_order_id, store:{merchant_supplied_id,...}}``
                                               (how_to/order_cancellation_webhook)
* Merchant cancel: ``PATCH /marketplace/api/v1/orders/{id}/cancellation`` with ``cancel_reason`` in
  ITEM_OUT_OF_STOCK | STORE_CLOSED | KITCHEN_BUSY | OTHER (requires allow-listing by DoorDash).
                                               (how_to/merchant_order_cancellation)
* Order Ready Signal: ``PATCH /marketplace/api/v1/orders/{id}/events/order_ready_for_pickup`` with
  ``{merchant_supplied_id}``.                  (how_to/order_ready_signal)
* Menu push: ``POST /marketplace/api/v1/menus`` / ``PATCH /marketplace/api/v1/menus/{id}``; result is
  delivered asynchronously by the Menu Status webhook.  (how_to/menu_push, menu_status_callback)

Access: Marketplace APIs are "Limited access" — partners apply in the Developer Portal, are
reviewed quarterly, build in sandbox, then pass certification before production access.
"""
from __future__ import annotations

import base64
import hmac
import time

import jwt

from apps.orders.intake import NormalizedCustomer, NormalizedOrder, NormalizedPayment
from apps.orders.models import OrderSource, OrderType

from ..idscheme import RawOption, build_item
from .base import ParsedEvent, ProviderAdapter, ProviderError, WebhookAuthError, safe_json

BASE = "https://openapi.doordash.com/marketplace"
USER_AGENT = "PizzaFranchisePlatform/1.0"

CANCEL_REASONS = {"ITEM_OUT_OF_STOCK", "STORE_CLOSED", "KITCHEN_BUSY", "OTHER"}
REJECT_TO_ERROR_CODE = {"ITEM_UNAVAILABLE": "ITEM_OUT_OF_STOCK", "STORE_CLOSED": "STORE_CLOSED"}

DAY_INDEX = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class DoorDashAdapter(ProviderAdapter):
    code = "doordash"
    name = "DoorDash"
    account_secret_fields = ("signing_secret",)
    account_fields = ("developer_id", "key_id")
    ack_deadline_seconds = 180          # lower bound of DoorDash's 3–8 minute confirmation SLA
    webhook_success_status = 202        # async confirmation
    supports_ready_signal = True
    supports_merchant_cancel = True
    supports_menu_push = True

    # ---- auth -----------------------------------------------------------------
    def make_jwt(self, creds: dict) -> str:
        now = int(time.time())
        payload = {"aud": "doordash", "iss": creds["developer_id"], "kid": creds["key_id"], "iat": now,
                   "exp": now + 1800}
        return jwt.encode(payload, _b64url_decode(creds["signing_secret"]), algorithm="HS256",
                          headers={"dd-ver": "DD-JWT-V1"})

    def headers(self, si) -> dict:
        creds = self.account_credentials(si)
        return {"Authorization": f"Bearer {self.make_jwt(creds)}", "auth-version": "v2",
                "User-Agent": USER_AGENT, "Content-Type": "application/json"}

    def verify_webhook(self, request, raw_body: bytes, account) -> None:
        expected = (account.credentials or {}).get("webhook_auth_token") if account else None
        if not expected:
            raise WebhookAuthError("DoorDash webhook auth token not configured for this account")
        got = request.headers.get("Authorization", "")
        for prefix in ("Bearer ", "Basic ", "Token "):
            if got.startswith(prefix):
                got = got[len(prefix):]
                break
        if not hmac.compare_digest(got.strip().encode(), expected.encode()):
            raise WebhookAuthError("Invalid DoorDash webhook auth token")

    # ---- events ---------------------------------------------------------------
    def parse_events(self, payload: dict) -> list[ParsedEvent]:
        event = payload.get("event") or {}
        etype = event.get("type") or ""
        if etype == "OrderCreate":
            order = payload.get("order") or {}
            oid = str(order.get("id") or "")
            if not oid:
                raise ValueError("OrderCreate event without order.id")
            return [ParsedEvent(f"OrderCreate:{oid}", "OrderCreate", "order_new", oid,
                                str((order.get("store") or {}).get("merchant_supplied_id") or ""), order)]
        if etype in ("MenuCreate", "MenuUpdate"):
            ref = event.get("reference") or ""
            return [ParsedEvent(f"{etype}:{ref}:{event.get('status')}", etype, "menu_status", "",
                                str((payload.get("store") or {}).get("merchant_supplied_id") or ""), payload)]
        if "external_order_id" in payload and "store" in payload:
            oid = str(payload["external_order_id"])
            return [ParsedEvent(f"OrderCancel:{oid}", "OrderCancel", "order_cancelled", oid,
                                str((payload.get("store") or {}).get("merchant_supplied_id") or ""), payload)]
        return [ParsedEvent(f"unknown:{hash(str(payload))}", etype or "unknown", "ignored", "", "", payload)]

    def fetch_order(self, si, event) -> dict:
        # DoorDash embeds the full order in the webhook body.
        return (event.payload or {}).get("order") or {}

    # ---- normalization ---------------------------------------------------------
    def _options(self, extras: list) -> list[RawOption]:
        out = []
        for extra in extras or []:
            for opt in extra.get("options") or []:
                out.append(RawOption(
                    external_id=str(opt.get("merchant_supplied_id") or ""), name=opt.get("name", ""),
                    quantity=int(opt.get("quantity") or 1),
                    price_cents=int(opt["price"]) if opt.get("price") is not None else None,
                    children=self._options(opt.get("extra") or opt.get("extras") or []),
                ))
        return out

    def normalize(self, si, raw: dict) -> NormalizedOrder:
        mappings, skus = self.mappings(si), self.known_skus()
        items = []
        for cat in raw.get("categories") or []:
            for it in cat.get("items") or []:
                items.append(build_item(
                    external_id=str(it.get("merchant_supplied_id") or ""), name=it.get("name", ""),
                    quantity=int(it.get("quantity") or 1),
                    unit_price_cents=int(it["price"]) if it.get("price") is not None else None,
                    options=self._options(it.get("extras") or []),
                    special_instructions=it.get("special_instructions") or "", mappings=mappings, known_skus=skus))
        consumer = raw.get("consumer") or {}
        ftype = raw.get("fulfillment_type") or ""
        if raw.get("is_pickup") or ftype == "pickup":
            order_type = OrderType.PICKUP
        elif ftype in ("merchant_delivery", "self_delivery"):
            order_type = OrderType.DELIVERY
        else:
            order_type = OrderType.PROVIDER_DELIVERY
        subtotal = int(raw.get("subtotal") or 0)
        tax = int(raw.get("tax") or 0)
        tip = int(raw.get("merchant_tip_amount") or 0)
        return NormalizedOrder(
            source=OrderSource.DOORDASH,
            store_id=si.store_id,
            order_type=order_type,
            items=items,
            external_order_id=str(raw.get("id")),
            external_display_id=str(raw.get("delivery_short_code") or "")[:50],
            customer=NormalizedCustomer(
                name=" ".join(x for x in [consumer.get("first_name"), consumer.get("last_name")] if x),
                phone=consumer.get("phone") or "", email="", external_id=str(consumer.get("id") or "")),
            delivery_address=raw.get("delivery_address") or {},
            special_instructions=raw.get("order_special_instructions") or "",
            tip_cents=tip,
            payment=NormalizedPayment(status="provider_paid", method="doordash", amount_cents=subtotal + tax + tip,
                                      reference=str(raw.get("id"))),
            provider_totals={"subtotal_cents": subtotal, "tax_cents": tax, "tip_cents": tip,
                             "total_cents": subtotal + tax + tip,
                             "is_tax_remitted_by_doordash": raw.get("is_tax_remitted_by_doordash"),
                             "tax_amount_remitted_by_doordash": raw.get("tax_amount_remitted_by_doordash"),
                             "commission_type": raw.get("commission_type"),
                             "experience": raw.get("experience")},
            provider_ack_deadline=self.deadline(),
            raw=raw,
        )

    # ---- order actions ------------------------------------------------------------
    def _confirm(self, si, order, status: str, failure_reason: str = "", errors=None) -> str:
        body = {"merchant_supplied_id": str(order.pk), "order_status": status}
        if failure_reason:
            body["failure_reason"] = failure_reason[:500]
        if errors:
            body["errors"] = errors
        if status == "success" and order.estimated_ready_at:
            body["prep_time"] = order.estimated_ready_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.http(si, order).request("PATCH", f"{BASE}/api/v1/orders/{order.external_order_id}",
                                     operation="order_confirm", headers=self.headers(si), json_body=body,
                                     expected=(200, 202))
        return f"confirmed:{status}"

    def accept(self, si, order) -> str:
        return self._confirm(si, order, "success")

    def reject(self, si, order, reason_code: str, message: str) -> str:
        errors = []
        for issue in order.validation_issues or []:
            if issue.get("code") in ("product_unavailable", "modifier_unavailable"):
                errors.append({"code": "ITEM_OUT_OF_STOCK", "merchant_supplied_id": issue.get("ref") or "",
                               "message": issue.get("message", "")[:200]})
        return self._confirm(si, order, "fail", f"{reason_code}: {message}", errors or None)

    def cancel(self, si, order, reason_code: str, message: str) -> str:
        reason = reason_code if reason_code in CANCEL_REASONS else "OTHER"
        self.http(si, order).request(
            "PATCH", f"{BASE}/api/v1/orders/{order.external_order_id}/cancellation", operation="order_cancel",
            headers=self.headers(si), json_body={"cancel_reason": reason, "cancel_details": message[:300]},
            expected=(200, 202))
        return "cancelled"

    def mark_ready(self, si, order) -> str:
        self.http(si, order).request(
            "PATCH", f"{BASE}/api/v1/orders/{order.external_order_id}/events/order_ready_for_pickup",
            operation="order_ready", headers=self.headers(si), json_body={"merchant_supplied_id": str(order.pk)},
            expected=(200, 202))
        return "ready_signal_sent"

    # ---- menu ---------------------------------------------------------------------
    def build_menu(self, si) -> dict:
        from apps.catalog.engine import build_store_menu

        store = si.store
        menu = build_store_menu(store, channel=self.code)
        categories = []
        for cat in menu["categories"]:
            items = []
            for p in cat["products"]:
                extras = []
                if p["sizes"]:
                    base = min(s["price_cents"] for s in p["sizes"])
                    extras.append({"name": "Size", "merchant_supplied_id": "GRP:SIZE", "min_num_options": 1,
                                   "max_num_options": 1,
                                   "options": [{"name": s["name"], "merchant_supplied_id": f"SIZE:{s['code']}",
                                                "price": s["price_cents"] - base, "active": True}
                                               for s in p["sizes"]]})
                else:
                    base = p["price_cents"] or 0
                if p["crusts"]:
                    extras.append({"name": "Crust", "merchant_supplied_id": "GRP:CRUST", "min_num_options": 1,
                                   "max_num_options": 1,
                                   "options": [{"name": c["name"], "merchant_supplied_id": f"CRUST:{c['code']}",
                                                "price": c["price_cents"], "active": True} for c in p["crusts"]]})
                removable = [d for d in p["default_modifiers"] if d["removable"]]
                if removable:
                    extras.append({"name": "Remove", "merchant_supplied_id": "GRP:NO", "min_num_options": 0,
                                   "max_num_options": len(removable),
                                   "options": [{"name": f"No {d['name']}", "merchant_supplied_id": f"NO:{d['code']}",
                                                "price": 0, "active": True} for d in removable]})
                for g in p["modifier_groups"]:
                    opts = []
                    for m in g["modifiers"]:
                        o = {"name": m["name"], "merchant_supplied_id": f"MOD:{m['code']}", "price": m["price_cents"],
                             "active": m["is_available"]}
                        if p["allow_half_and_half"] and g["allow_half"]:
                            o["extras"] = [{"name": "Placement", "merchant_supplied_id": "GRP:PLACE",
                                            "min_num_options": 0, "max_num_options": 1,
                                            "options": [{"name": n, "merchant_supplied_id": f"PLACE:{c}", "price": 0}
                                                        for c, n in (("whole", "Whole"), ("left", "Left half"),
                                                                     ("right", "Right half"))]}]
                        opts.append(o)
                    extras.append({"name": g["name"], "merchant_supplied_id": f"GRP:{g['code']}",
                                   "min_num_options": g["min_select"], "max_num_options": g["max_select"] or len(opts),
                                   "options": opts})
                items.append({"name": p["name"], "description": p["description"][:500],
                              "merchant_supplied_id": p["sku"], "price": base, "active": p["is_available"],
                              "is_alcohol": p["is_alcohol"], "extras": extras,
                              **({"original_image_url": p["image_url"]} if p["image_url"] else {})})
            categories.append({"name": cat["name"], "merchant_supplied_id": cat["code"], "items": items})
        open_hours = []
        for h in store.hours.all():
            open_hours.append({"day_index": DAY_INDEX[h.day_of_week], "start_time": h.opens_at.strftime("%H:%M"),
                               "end_time": h.closes_at.strftime("%H:%M")})
        special_hours = []
        for sh in store.special_hours.all():
            special_hours.append({"date": sh.date.isoformat(), "closed": sh.is_closed,
                                  **({"start_time": sh.opens_at.strftime("%H:%M"),
                                      "end_time": sh.closes_at.strftime("%H:%M")} if not sh.is_closed else {})})
        creds = self.account_credentials(si)
        return {
            "reference": f"{store.number}-{int(time.time())}",
            "store": {"merchant_supplied_id": si.external_store_id or store.number,
                      "provider_type": creds.get("provider_type", "")},
            "open_hours": open_hours,
            "special_hours": special_hours,
            "menu": {"name": f"{store.name} Menu", "active": True, "categories": categories},
        }

    def push_menu(self, si) -> str:
        body = self.build_menu(si)
        if si.external_menu_id:
            self.http(si).request("PATCH", f"{BASE}/api/v1/menus/{si.external_menu_id}", operation="menu_update",
                                  headers=self.headers(si), json_body=body, expected=(200, 202))
        else:
            self.http(si).request("POST", f"{BASE}/api/v1/menus", operation="menu_create",
                                  headers=self.headers(si), json_body=body, expected=(200, 202))
        return body["reference"]

    def test_connection(self, si) -> str:
        # Validates the credentials produce a well-formed JWT, then performs the documented
        # authenticated read GET /marketplace/api/v1/stores/{merchant_supplied_id}/store_details.
        creds = self.account_credentials(si)
        token = self.make_jwt(creds)
        decoded = jwt.decode(token, _b64url_decode(creds["signing_secret"]), algorithms=["HS256"], audience="doordash")
        if decoded.get("iss") != creds["developer_id"]:
            raise ProviderError("JWT self-check failed", retryable=False)
        if not si.external_store_id:
            raise ProviderError("DoorDash merchant_supplied_id (store id) not set", retryable=False)
        resp = self.http(si).request(
            "GET", f"{BASE}/api/v1/stores/{si.external_store_id}/store_details", operation="test_connection",
            headers=self.headers(si), expected=(200,))
        return f"Authenticated; store details returned ({len(safe_json(resp))} fields)"
