import pytest

from apps.orders.models import Order, OrderStatus

pytestmark = pytest.mark.django_db


def pos_order(store, items, **extra):
    return {"store_id": store.id, "order_type": "pickup", "items": items, **extra}


def test_pos_order_priced_by_engine_with_half_and_extra_toppings(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = as_user("cashier.1001", store_number="1001")
    items = [{"product": "PZ-PEP", "size": "L", "crust": "THIN", "quantity": 2,
              "modifiers": [{"code": "TOP-PEP", "quantity": 2},             # extra pepperoni (default 1)
                            {"code": "TOP-MUSH", "placement": "left"},      # half mushrooms
                            {"code": "TOP-CHEESE", "quantity": 2}],         # extra cheese
              "removed": []}]
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post("/api/v1/orders/", pos_order(s, items), format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    # Large = 1749; multiplier 125%: extra pep 175*1.25=219, half mush 150*1.25*50%=94, extra cheese 219
    unit = 1749 + 219 + 94 + 219
    assert body["items"][0]["unit_price_cents"] == unit
    assert body["subtotal_cents"] == unit * 2
    assert body["tax_cents"] == round(unit * 2 * 0.13)
    assert body["status"] == OrderStatus.ACCEPTED
    assert body["intake"]["decision"] == "accepted"
    placements = {m["code"]: m["placement"] for m in body["items"][0]["modifiers"]}
    assert placements["TOP-MUSH"] == "left"
    # Kitchen tickets routed by prep category (oven + expo)
    order = Order.objects.get(pk=body["id"])
    assert set(order.kitchen_tickets.values_list("station__code", flat=True)) == {"OVEN", "EXPO"}


def test_invalid_customization_is_rejected_with_line_issues(seeded, as_user):
    s = seeded["1001"]
    c = as_user("cashier.1001", store_number="1001")
    r = c.post("/api/v1/orders/", pos_order(s, [
        {"product": "PZ-PEP", "size": "XXL"},
        {"product": "SD-WINGS", "modifiers": [{"code": "TOP-PEP"}]},  # toppings not allowed on wings
        {"product": "SD-GBREAD", "modifiers": [{"code": "DIP-GAR", "placement": "left"}]},
    ]), format="json")
    assert r.status_code == 422
    codes = {e["code"] for e in r.json()["error"]["errors"]}
    assert {"invalid_size", "modifier_not_allowed"} <= codes
    assert Order.objects.count() == 0


def test_unavailable_item_rejected_for_internal_orders(seeded, as_user):
    from apps.catalog.models import StoreProduct

    s = seeded["1001"]
    StoreProduct.objects.filter(store=s, product__sku="SD-WINGS").update(is_available=False)
    c = as_user("cashier.1001", store_number="1001")
    r = c.post("/api/v1/orders/", pos_order(s, [{"product": "SD-WINGS"}]), format="json")
    assert r.status_code == 422
    assert r.json()["error"]["errors"][0]["code"] == "product_unavailable"


def test_franchise_price_override_respects_hq_range(seeded, as_user):
    s = seeded["1001"]
    c = as_user("owner.a")
    from apps.catalog.models import Product

    wings = Product.objects.get(sku="SD-WINGS")
    r = c.patch(f"/api/v1/stores/{s.id}/menu-config/", {"products": [{"product": wings.id, "price_cents": 5000}]},
                format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_out_of_range"
    r = c.patch(f"/api/v1/stores/{s.id}/menu-config/", {"products": [{"product": wings.id, "price_cents": 1499}]},
                format="json")
    assert r.status_code == 200
    q = c.post(f"/api/v1/stores/{s.id}/quote/", {"items": [{"product": "SD-WINGS"}]}, format="json").json()
    assert q["subtotal_cents"] == 1499


def test_order_state_machine_rejects_invalid_transition(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = as_user("manager.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = c.post("/api/v1/orders/", pos_order(s, [{"product": "DR-COLA"}]), format="json").json()
    r = c.post(f"/api/v1/orders/{o['id']}/status/", {"status": "received"}, format="json")
    assert r.status_code == 409
    r = c.post(f"/api/v1/orders/{o['id']}/status/", {"status": "ready"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "ready"
    r = c.post(f"/api/v1/orders/{o['id']}/cancel/", {"reason": "customer left"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    events = c.get(f"/api/v1/orders/{o['id']}/events/").json()
    assert [e["to_status"] for e in events if e["event_type"] == "status_changed"] == [
        "accepted", "ready", "cancelled"]


def test_cashier_cancel_requires_manager_code(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = as_user("cashier.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = c.post("/api/v1/orders/", pos_order(s, [{"product": "DR-COLA"}]), format="json").json()
    r = c.post(f"/api/v1/orders/{o['id']}/cancel/", {"reason": "mistake"}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "manager_authorization_required"
    r = c.post(f"/api/v1/orders/{o['id']}/cancel/", {"reason": "mistake", "manager_code": "0000"}, format="json")
    assert r.status_code == 403
    r = c.post(f"/api/v1/orders/{o['id']}/cancel/", {"reason": "mistake", "manager_code": "1234"}, format="json")
    assert r.status_code == 200
    from apps.core.models import AuditLog

    log = AuditLog.objects.filter(action="order.cancel").latest("created_at")
    assert log.authorized_by.username == "manager.1001"


def test_kitchen_bump_drives_order_status(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = as_user("manager.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = c.post("/api/v1/orders/", pos_order(s, [{"product": "PZ-CHEESE", "size": "M"},
                                                    {"product": "SD-WINGS"}]), format="json").json()
    tickets = c.get(f"/api/v1/kitchen/tickets/?store={s.id}").json()
    assert {t["station"] for t in tickets} == {"OVEN", "FRY", "EXPO"}
    k = as_user("kitchen.1001", store_number="1001")
    oven = next(t for t in tickets if t["station"] == "OVEN")
    with django_capture_on_commit_callbacks(execute=True):
        assert k.post(f"/api/v1/kitchen/tickets/{oven['id']}/bump/", {"status": "preparing"},
                      format="json").status_code == 200
    assert Order.objects.get(pk=o["id"]).status == "preparing"
    with django_capture_on_commit_callbacks(execute=True):
        for t in tickets:
            k.post(f"/api/v1/kitchen/tickets/{t['id']}/bump/", {"status": "ready"}, format="json")
    assert Order.objects.get(pk=o["id"]).status == "ready"


def test_receipt_renders(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = as_user("cashier.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = c.post("/api/v1/orders/", pos_order(s, [{"product": "PZ-DLX", "size": "S", "removed": ["TOP-MUSH"]}]),
                   format="json").json()
    rec = c.get(f"/api/v1/orders/{o['id']}/receipt/").json()
    assert "NO Mushrooms" in rec["text"] and "TOTAL" in rec["text"]
