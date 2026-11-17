import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.integrations.models import IntegrationProvider, ProviderAccount, StoreIntegration, SyncRecord, WebhookEvent
from apps.orders.models import Order, OrderStatus
from conftest import FakeResponse

pytestmark = pytest.mark.django_db

DD_TOKEN = "dd-webhook-token-123456"
DD_SECRET = base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("=")
UBER_SECRET = "uber-client-secret-abcdef"


@pytest.fixture
def doordash(seeded):
    prov = IntegrationProvider.objects.get(code="doordash")
    prov.network_enabled = True
    prov.save()
    acc = ProviderAccount.objects.create(provider=prov, name="HQ DoorDash", credentials={
        "developer_id": "dev-1", "key_id": "key-1", "signing_secret": DD_SECRET, "webhook_auth_token": DD_TOKEN,
        "provider_type": "pizza_network"})
    si = StoreIntegration.objects.create(store=seeded["1001"], provider=prov, account=acc, enabled=True,
                                         external_store_id="1001")
    return si


@pytest.fixture
def uber(seeded):
    prov = IntegrationProvider.objects.get(code="ubereats")
    prov.network_enabled = True
    prov.save()
    acc = ProviderAccount.objects.create(provider=prov, name="HQ Uber", credentials={
        "client_id": "cid", "client_secret": UBER_SECRET})
    return StoreIntegration.objects.create(store=seeded["1002"], provider=prov, account=acc, enabled=True,
                                           external_store_id="uber-store-uuid-1002")


def dd_order(order_id="dd-100", store="1001", items=None):
    items = items or [{
        "merchant_supplied_id": "PZ-PEP", "name": "Pepperoni Pizza", "price": 1449, "quantity": 1,
        "extras": [
            {"merchant_supplied_id": "GRP:SIZE", "name": "Size",
             "options": [{"merchant_supplied_id": "SIZE:M", "name": "Medium", "price": 0, "quantity": 1}]},
            {"merchant_supplied_id": "GRP:TOPPINGS", "name": "Toppings",
             "options": [{"merchant_supplied_id": "MOD:TOP-MUSH", "name": "Mushrooms", "price": 75, "quantity": 1,
                          "extra": [{"merchant_supplied_id": "GRP:PLACE", "name": "Placement",
                                     "options": [{"merchant_supplied_id": "PLACE:right", "name": "Right half",
                                                  "price": 0, "quantity": 1}]}]}]},
        ]}]
    return {"event": {"type": "OrderCreate", "status": "NEW"},
            "order": {"id": order_id, "experience": "DOORDASH", "is_pickup": False, "subtotal": 1524, "tax": 198,
                      "merchant_tip_amount": 0, "fulfillment_type": "dx_delivery", "delivery_short_code": "AB12",
                      "categories": [{"merchant_supplied_id": "PIZZA", "name": "Pizzas", "items": items}],
                      "consumer": {"id": 1, "first_name": "Kelley", "last_name": "W.", "phone": "+18559731040"},
                      "store": {"merchant_supplied_id": store, "provider_type": "pizza_network"}}}


def post_dd(api, payload, token=DD_TOKEN):
    return api.post("/api/v1/integrations/doordash/webhooks/", data=json.dumps(payload),
                    content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")


def test_doordash_order_flow_and_async_confirmation(doordash, api, provider_http, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        r = post_dd(api, dd_order())
    assert r.status_code == 202
    order = Order.objects.get(source="doordash", external_order_id="dd-100")
    assert order.status == OrderStatus.ACCEPTED
    assert order.payment_status == "provider_paid"
    assert order.store.number == "1001"
    item = order.items.get()
    assert item.size_code == "M"
    assert item.modifiers.get(code="TOP-MUSH").placement == "right"
    assert order.payments.get().method == "provider"
    confirm = [c for c in provider_http["calls"] if c["url"].endswith("/marketplace/api/v1/orders/dd-100")]
    assert len(confirm) == 1 and confirm[0]["method"] == "PATCH"
    assert confirm[0]["json"] == {"merchant_supplied_id": str(order.pk), "order_status": "success"}
    hdrs = confirm[0]["headers"]
    assert hdrs["Authorization"].startswith("Bearer ") and hdrs["auth-version"] == "v2"
    import jwt as pyjwt

    tok = hdrs["Authorization"].split(" ", 1)[1]
    assert pyjwt.get_unverified_header(tok)["dd-ver"] == "DD-JWT-V1"
    claims = pyjwt.decode(tok, base64.urlsafe_b64decode(DD_SECRET + "=="), algorithms=["HS256"], audience="doordash")
    assert claims["iss"] == "dev-1" and claims["kid"] == "key-1" and claims["exp"] - claims["iat"] <= 1800
    assert order.kitchen_tickets.exists()
    assert SyncRecord.objects.get(order=order, kind="order_ack").status == "succeeded"


def test_duplicate_webhook_delivery_creates_one_order(doordash, api, provider_http,
                                                      django_capture_on_commit_callbacks):
    for _ in range(3):
        with django_capture_on_commit_callbacks(execute=True):
            assert post_dd(api, dd_order("dd-dup")).status_code == 202
    assert Order.objects.filter(external_order_id="dd-dup").count() == 1
    ev = WebhookEvent.objects.get(event_id="OrderCreate:dd-dup")
    assert ev.delivery_count == 3
    acks = [c for c in provider_http["calls"] if c["url"].endswith("/orders/dd-dup")]
    assert len(acks) == 1  # acknowledged exactly once


def test_webhook_auth_rejected(doordash, api):
    r = post_dd(api, dd_order(), token="wrong")
    assert r.status_code == 401
    assert not WebhookEvent.objects.exists()


def test_unavailable_item_auto_rejected_with_reason(doordash, api, provider_http, django_capture_on_commit_callbacks):
    from apps.catalog.models import StoreProduct

    StoreProduct.objects.filter(store=doordash.store, product__sku="PZ-PEP").update(is_available=False)
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-86"))
    order = Order.objects.get(external_order_id="dd-86")
    assert order.status == OrderStatus.REJECTED
    body = next(c for c in provider_http["calls"] if c["url"].endswith("/orders/dd-86"))["json"]
    assert body["order_status"] == "fail" and body["failure_reason"].startswith("ITEM_UNAVAILABLE")
    assert body["errors"][0]["code"] == "ITEM_OUT_OF_STOCK"
    assert not order.kitchen_tickets.exists()


def test_store_closed_auto_rejected(doordash, api, provider_http, django_capture_on_commit_callbacks):
    s = doordash.store
    s.is_temporarily_closed = True
    s.save()
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-closed"))
    assert Order.objects.get(external_order_id="dd-closed").rejection_reason == "STORE_CLOSED"


def test_unmapped_item_routed_to_staff_then_timeout_rejects(doordash, api, provider_http,
                                                            django_capture_on_commit_callbacks):
    items = [{"merchant_supplied_id": "UNKNOWN-ITEM", "name": "Mystery", "price": 500, "quantity": 1}]
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-unmapped", items=items))
    order = Order.objects.get(external_order_id="dd-unmapped")
    assert order.status == OrderStatus.PENDING_REVIEW and order.requires_attention
    assert not [c for c in provider_http["calls"] if c["url"].endswith("/orders/dd-unmapped")]
    # Provider deadline approaching: sweeper resolves per policy (reject) instead of letting it time out.
    Order.objects.filter(pk=order.pk).update(provider_ack_deadline=timezone.now() + timedelta(seconds=30))
    from apps.integrations.tasks import sweep_pending_provider_acknowledgements

    with django_capture_on_commit_callbacks(execute=True):
        sweep_pending_provider_acknowledgements()
    order.refresh_from_db()
    assert order.status == OrderStatus.REJECTED
    assert [c for c in provider_http["calls"] if c["url"].endswith("/orders/dd-unmapped")][0]["json"][
        "order_status"] == "fail"


def test_staff_can_accept_pending_order_and_ready_signal_sent(doordash, api, provider_http, as_user,
                                                              django_capture_on_commit_callbacks):
    doordash.settings["acceptance_policy"]["auto_accept"] = False
    doordash.save()
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-manual"))
    order = Order.objects.get(external_order_id="dd-manual")
    assert order.status == OrderStatus.PENDING_REVIEW
    c = as_user("manager.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post(f"/api/v1/orders/{order.pk}/accept/", {}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    with django_capture_on_commit_callbacks(execute=True):
        c.post(f"/api/v1/orders/{order.pk}/status/", {"status": "ready"}, format="json")
    ready = [x for x in provider_http["calls"] if x["url"].endswith("/orders/dd-manual/events/order_ready_for_pickup")]
    assert len(ready) == 1 and ready[0]["json"] == {"merchant_supplied_id": str(order.pk)}


def test_provider_cancellation_webhook(doordash, api, provider_http, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-cx"))
    order = Order.objects.get(external_order_id="dd-cx")
    with django_capture_on_commit_callbacks(execute=True):
        r = post_dd(api, {"external_order_id": "dd-cx", "client_order_id": str(order.pk),
                          "store": {"merchant_supplied_id": "1001", "provider_type": "pizza_network"}})
    assert r.status_code == 202
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED
    # Provider-initiated cancel must not be echoed back as a merchant cancellation.
    assert not [c for c in provider_http["calls"] if "/cancellation" in c["url"]]
    assert order.kitchen_tickets.filter(status="void").count() == order.kitchen_tickets.count()


def test_failed_ack_marks_sync_failed_and_flags_order(doordash, api, provider_http,
                                                      django_capture_on_commit_callbacks):
    provider_http["routes"].append(("PATCH", "/orders/dd-err", FakeResponse(400, {"message": "bad"})))
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-err"))
    order = Order.objects.get(external_order_id="dd-err")
    assert order.provider_sync_status == "failed" and order.requires_attention
    assert "HTTP 400" in order.provider_sync_error
    doordash.refresh_from_db()
    assert doordash.connection_status == "error" and doordash.last_error


def uber_post(api, payload, secret=UBER_SECRET):
    raw = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return api.post("/api/v1/integrations/ubereats/webhooks/", data=raw, content_type="application/json",
                    HTTP_X_UBER_SIGNATURE=sig)


UBER_ORDER = {
    "id": "uber-order-1", "display_id": "7F3A2", "type": "DELIVERY_BY_UBER",
    "eater": {"first_name": "Sam", "last_name": "R", "phone": "+15550000"},
    "cart": {"items": [{"id": "x", "external_data": "PZ-CHEESE", "title": "Cheese Pizza", "quantity": 1,
                        "price": {"unit_price": {"amount": 15.99}},
                        "selected_modifier_groups": [
                            {"id": "g1", "external_data": "GRP:SIZE",
                             "selected_items": [{"id": "s1", "external_data": "SIZE:L", "title": "Large",
                                                 "quantity": 1}]},
                            {"id": "g2", "external_data": "GRP:TOPPINGS",
                             "selected_items": [{"id": "t1", "external_data": "MOD:TOP-OLIVE", "title": "Olives",
                                                 "quantity": 1}]}]}]},
    "payment": {"charges": {"sub_total": {"amount": 17.87}, "tax": {"amount": 2.32}, "total": {"amount": 20.19}}},
}


def test_uber_webhook_signature_fetch_and_accept(uber, api, provider_http, django_capture_on_commit_callbacks):
    provider_http["routes"].append(("POST", "/oauth/v2/token", FakeResponse(200, {"access_token": "tok",
                                                                                   "expires_in": 2592000})))
    provider_http["routes"].append(("GET", "/v2/eats/order/uber-order-1", FakeResponse(200, UBER_ORDER)))
    provider_http["routes"].append(("POST", "accept_pos_order", FakeResponse(204, {})))
    payload = {"event_id": "evt-1", "event_type": "orders.notification", "event_time": 1,
               "meta": {"resource_id": "uber-order-1", "user_id": "uber-store-uuid-1002", "status": "pos"},
               "resource_href": "https://test-api.uber.com/v2/eats/order/uber-order-1"}
    with django_capture_on_commit_callbacks(execute=True):
        r = uber_post(api, payload)
    assert r.status_code == 200 and r.content == b""
    order = Order.objects.get(source="ubereats", external_order_id="uber-order-1")
    assert order.status == OrderStatus.ACCEPTED
    assert order.items.get().size_code == "L"
    assert order.total_cents == 2019 and order.provider_totals["subtotal_cents"] == 1787
    accept = [c for c in provider_http["calls"] if c["url"].endswith("/v1/eats/orders/uber-order-1/accept_pos_order")]
    assert len(accept) == 1 and accept[0]["headers"]["Authorization"] == "Bearer tok"
    # Same event redelivered → no new order, no second accept
    with django_capture_on_commit_callbacks(execute=True):
        uber_post(api, payload)
    assert Order.objects.filter(external_order_id="uber-order-1").count() == 1
    assert len([c for c in provider_http["calls"] if "accept_pos_order" in c["url"]]) == 1
    # Token cached (encrypted) — only one token request
    assert len([c for c in provider_http["calls"] if "oauth" in c["url"]]) == 1


def test_uber_bad_signature_and_untrusted_href(uber, api, provider_http, django_capture_on_commit_callbacks):
    payload = {"event_id": "evt-2", "event_type": "orders.notification",
               "meta": {"resource_id": "o2", "user_id": "uber-store-uuid-1002"},
               "resource_href": "https://evil.example.com/steal"}
    assert uber_post(api, payload, secret="wrong").status_code == 401
    with django_capture_on_commit_callbacks(execute=True):
        assert uber_post(api, payload).status_code == 200
    ev = WebhookEvent.objects.get(event_id="evt-2")
    assert ev.status == "failed" and "untrusted resource_href" in ev.last_error
    assert not any("evil.example.com" in c["url"] for c in provider_http["calls"])


def test_skip_is_not_faked(seeded, api):
    r = api.post("/api/v1/integrations/skip/webhooks/", data="{}", content_type="application/json")
    assert r.status_code == 501
    assert not Order.objects.filter(source="skip").exists()


def test_disabled_integration_does_not_create_order(doordash, api, provider_http, django_capture_on_commit_callbacks):
    doordash.enabled = False
    doordash.save()
    with django_capture_on_commit_callbacks(execute=True):
        post_dd(api, dd_order("dd-off"))
    assert not Order.objects.filter(external_order_id="dd-off").exists()
    ev = WebhookEvent.objects.get(event_id="OrderCreate:dd-off")
    assert ev.status == "failed" and "disabled" in ev.last_error
