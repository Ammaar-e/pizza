import pytest

from apps.orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def call_centre(seeded, as_user, api):
    hq = as_user("hq.admin")
    r = hq.post("/api/v1/api-clients/", {"name": "Call Centre", "kind": "call_centre",
                                         "scopes": ["stores:read", "menu:read", "orders:write", "orders:read",
                                                    "orders:cancel"],
                                         "webhook_url": "https://callcentre.example.com/hooks"}, format="json")
    assert r.status_code == 201
    key = r.json()["api_key"]
    api.credentials(HTTP_AUTHORIZATION=f"Api-Key {key}")
    return api


@pytest.fixture
def franchise_client(seeded, as_user, api):
    a = as_user("owner.a")
    r = a.post("/api/v1/api-clients/", {"name": "A website", "kind": "external_api",
                                        "franchisee": seeded["1001"].franchisee_id,
                                        "stores": [seeded["1001"].id], "scopes": ["stores:read", "menu:read",
                                                                                  "orders:write", "orders:read"]},
               format="json")
    assert r.status_code == 201, r.content
    api.credentials(HTTP_AUTHORIZATION=f"Api-Key {r.json()['api_key']}")
    return api


def payload(store, **kw):
    return {"store_number": store.number, "order_type": "delivery",
            "items": [{"product": "PZ-HAW", "size": "L", "crust": "REG"}],
            "customer": {"name": "Pat", "phone": "+15551234567"},
            "delivery_address": {"line1": "1 Main", "city": "Toronto", "postal_code": "M1M1M1"}, **kw}


def test_call_centre_full_flow(seeded, call_centre, monkeypatch, django_capture_on_commit_callbacks):
    sent = []
    import apps.api.tasks as t

    monkeypatch.setattr(t.requests, "post", lambda url, data, headers, timeout: sent.append((url, headers, data))
                        or type("R", (), {"status_code": 200, "text": ""})())
    c = call_centre
    stores = c.get("/api/v1/stores/").json()["results"]
    assert {"number", "hours", "is_open_now"} <= set(stores[0]) and "payment_config" not in stores[0]
    s = seeded["1001"]
    menu = c.get(f"/api/v1/stores/{s.id}/menu/").json()
    assert any(p["sku"] == "PZ-HAW" for cat in menu["categories"] for p in cat["products"])
    quote = c.post(f"/api/v1/stores/{s.id}/quote/", {"order_type": "delivery",
                                                     "items": payload(s)["items"]}, format="json").json()
    assert quote["valid"]
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post("/api/v1/orders/", payload(s, expected_total_cents=quote["total_cents"]), format="json",
                   HTTP_IDEMPOTENCY_KEY="cc-1")
    assert r.status_code == 201, r.content
    oid = r.json()["id"]
    assert r.json()["status"] == "accepted" and "validation_issues" not in r.json()
    assert Order.objects.get(pk=oid).source == "call_centre"
    st = c.get(f"/api/v1/orders/{oid}/status/").json()
    assert st["status"] == "accepted"
    assert sent and sent[0][0] == "https://callcentre.example.com/hooks"
    assert sent[0][1]["X-Pizza-Signature"].startswith("sha256=")


def test_idempotency_key(seeded, call_centre, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    with django_capture_on_commit_callbacks(execute=True):
        r1 = call_centre.post("/api/v1/orders/", payload(s), format="json", HTTP_IDEMPOTENCY_KEY="k-1")
        r2 = call_centre.post("/api/v1/orders/", payload(s), format="json", HTTP_IDEMPOTENCY_KEY="k-1")
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert r2.json()["intake"]["decision"] == "duplicate"
    r3 = call_centre.post("/api/v1/orders/", payload(s, special_instructions="different"), format="json",
                          HTTP_IDEMPOTENCY_KEY="k-1")
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "idempotency_key_reused"
    r4 = call_centre.post("/api/v1/orders/", payload(s), format="json")
    assert r4.status_code == 400
    assert Order.objects.count() == 1


def test_price_mismatch_rejected(seeded, call_centre):
    r = call_centre.post("/api/v1/orders/", payload(seeded["1001"], expected_total_cents=1), format="json",
                         HTTP_IDEMPOTENCY_KEY="pm")
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_mismatch"
    assert Order.objects.count() == 0


def test_client_restricted_to_its_stores(seeded, franchise_client):
    stores = {s["number"] for s in franchise_client.get("/api/v1/stores/").json()["results"]}
    assert stores == {"1001"}
    r = franchise_client.post("/api/v1/orders/", payload(seeded["2001"]), format="json", HTTP_IDEMPOTENCY_KEY="x")
    assert r.status_code == 403
    r = franchise_client.post("/api/v1/orders/", payload(seeded["1002"]), format="json", HTTP_IDEMPOTENCY_KEY="y")
    assert r.status_code == 403
    assert franchise_client.get(f"/api/v1/stores/{seeded['2001'].id}/menu/").status_code == 404


def test_client_cannot_use_staff_endpoints_or_see_others_orders(seeded, franchise_client, as_user, api):
    assert franchise_client.get("/api/v1/users/").status_code == 403
    assert franchise_client.patch(f"/api/v1/stores/{seeded['1001'].id}/", {"name": "x"},
                                  format="json").status_code == 403
    assert franchise_client.post(f"/api/v1/orders/{'0' * 8}-0000-0000-0000-{'0' * 12}/cancel/",
                                 {"reason": "x"}, format="json").status_code == 403  # lacks orders:cancel scope
    key = api._credentials["HTTP_AUTHORIZATION"]
    api.credentials()
    pos = as_user("cashier.1001", store_number="1001")
    o = pos.post("/api/v1/orders/", {"store_id": seeded["1001"].id, "order_type": "pickup",
                                     "items": [{"product": "DR-COLA"}]}, format="json").json()
    api.credentials(HTTP_AUTHORIZATION=key)
    assert api.get(f"/api/v1/orders/{o['id']}/").status_code == 404


def test_invalid_key_rejected(seeded, api):
    api.credentials(HTTP_AUTHORIZATION="Api-Key abc.def")
    assert api.get("/api/v1/stores/").status_code == 401


def test_openapi_schema_generates(seeded, as_user):
    c = as_user("hq.admin")
    r = c.get("/api/v1/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in ("/api/v1/orders/", "/api/v1/stores/{id}/menu/", "/api/v1/integrations/{provider}/webhooks/",
              "/api/v1/orders/{id}/status/", "/api/v1/stores/{store_id}/integrations/{provider}/{op}/"):
        assert p in paths, p
