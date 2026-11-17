import pytest

from apps.payments.models import Payment, PaymentState
from apps.payments.services import set_payment_state
from apps.core.exceptions import ConflictError

pytestmark = pytest.mark.django_db


@pytest.fixture
def pos(seeded, api, as_user):
    from apps.organization.models import Device

    mgr = as_user("manager.1001", store_number="1001")
    r = mgr.post("/api/v1/devices/", {"store": seeded["1001"].id, "device_type": "pos", "name": "Front 1"},
                 format="json")
    assert r.status_code == 201
    token = r.json()["device_token"]
    assert Device.objects.get(pk=r.json()["id"]).token_hash != token  # only hash stored
    api.credentials()
    c = as_user("cashier.1001", store_number="1001", device_token=token)
    return c, token


def order(c, store, sku="PZ-CHEESE", size="M"):
    r = c.post("/api/v1/orders/", {"store_id": store.id, "order_type": "pickup",
                                   "items": [{"product": sku, "size": size}]}, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def test_cash_payment_requires_session_and_computes_change(seeded, pos):
    c, _ = pos
    o = order(c, seeded["1001"])
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": o["total_cents"],
                                                       "tendered_cents": 5000}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_cash_session"
    assert c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 10000}, format="json").status_code == 201
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": o["total_cents"],
                                                       "tendered_cents": 5000}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["payment"]["change_cents"] == 5000 - o["total_cents"]
    assert r.json()["payment_status"] == "paid"
    # Order state is independent of payment state
    assert r.json()["status"] == "accepted"


def test_overpayment_and_split_payment(seeded, pos):
    c, _ = pos
    c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 0}, format="json")
    o = order(c, seeded["1001"])
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": o["total_cents"] + 1,
                                                       "tendered_cents": 99999}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "overpayment"
    half = o["total_cents"] // 2
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": half,
                                                       "tendered_cents": half}, format="json")
    assert r.json()["payment_status"] == "partially_paid"
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "online", "amount_cents": o["total_cents"] - half,
                                                       "reference": "pi_external_123"}, format="json")
    assert r.json()["payment_status"] == "paid"


def test_refunds_partial_then_full_with_manager_code(seeded, pos):
    c, _ = pos
    c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 0}, format="json")
    o = order(c, seeded["1001"])
    pay = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": o["total_cents"],
                                                         "tendered_cents": o["total_cents"]}, format="json").json()
    pid = pay["payment"]["id"]
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": 100, "reason": "cold"}, format="json")
    assert r.status_code == 403  # cashier needs manager code
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": 100, "reason": "cold", "manager_code": "1234"},
               format="json")
    assert r.status_code == 200, r.content
    assert r.json()["payment_status"] == "partially_refunded"
    assert r.json()["order_payment_status"] == "partially_refunded"
    rest = o["total_cents"] - 100
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": rest + 1, "reason": "x", "manager_code": "1234"},
               format="json")
    assert r.status_code == 422
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": rest, "reason": "x", "manager_code": "1234"},
               format="json")
    assert r.json()["payment_status"] == "refunded" and r.json()["order_payment_status"] == "refunded"
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": 1, "reason": "x", "manager_code": "1234"},
               format="json")
    assert r.status_code == 409


def test_cash_session_close_variance(seeded, pos):
    c, _ = pos
    s = c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 10000}, format="json").json()
    o = order(c, seeded["1001"], "DR-COLA", None)
    c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": o["total_cents"],
                                                   "tendered_cents": o["total_cents"]}, format="json")
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/movements/", {"kind": "paid_out", "amount_cents": 500,
                                                                 "reason": "supplies", "manager_code": "1234"},
               format="json")
    assert r.status_code == 201
    expected = 10000 + o["total_cents"] - 500
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/close/", {"counted_cash_cents": expected - 25}, format="json")
    assert r.json()["expected_cash_cents"] == expected and r.json()["variance_cents"] == -25


def test_card_without_processor_config_fails_cleanly(seeded, pos):
    c, _ = pos
    o = order(c, seeded["1001"])
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"]},
               format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "processor_not_configured"
    p = Payment.objects.get(order_id=o["id"])
    assert p.status == "failed" and p.card_last4 == ""


def test_provider_paid_orders_cannot_be_charged_again(seeded, pos):
    c, _ = pos
    r = c.post("/api/v1/orders/", {"store_id": seeded["1001"].id, "order_type": "provider_delivery",
                                   "source": "skip", "external_order_id": "SKIP-TABLET-1",
                                   "items": [{"product": "DR-COLA"}]}, format="json")
    assert r.status_code == 201, r.content
    o = r.json()
    assert o["payment_status"] == "provider_paid" and o["entry_channel"] == "manual_provider"
    c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 0}, format="json")
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": 100,
                                                       "tendered_cents": 100}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "provider_paid_order"


def test_payment_state_machine():
    p = Payment(status=PaymentState.PENDING)
    set_payment_state(p, PaymentState.CAPTURED)
    set_payment_state(p, PaymentState.PARTIALLY_REFUNDED)
    set_payment_state(p, PaymentState.PARTIALLY_REFUNDED)
    set_payment_state(p, PaymentState.REFUNDED)
    for target in (PaymentState.CAPTURED, PaymentState.PENDING, PaymentState.VOIDED):
        with pytest.raises(ConflictError):
            set_payment_state(p, target)
    f = Payment(status=PaymentState.FAILED)
    with pytest.raises(ConflictError):
        set_payment_state(f, PaymentState.CAPTURED)


def test_offline_sync_is_idempotent(seeded, pos):
    c, _ = pos
    c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 0}, format="json")
    batch = {"orders": [{"offline_client_ref": "dev1-0001",
                         "order": {"order_type": "pickup", "items": [{"product": "DR-COLA"}]},
                         "payments": [{"method": "cash", "amount_cents": 338, "tendered_cents": 500}]},
                        {"offline_client_ref": "dev1-0002",
                         "order": {"order_type": "pickup", "items": [{"product": "NOPE"}]}}]}
    r1 = c.post("/api/v1/pos/sync/", batch, format="json").json()["results"]
    assert r1[0]["status"] == "created" and r1[1]["status"] == "error"
    r2 = c.post("/api/v1/pos/sync/", batch, format="json").json()["results"]
    assert r2[0]["status"] == "duplicate" and r2[0]["order_id"] == r1[0]["order_id"]
    from apps.orders.models import Order

    o = Order.objects.get(pk=r1[0]["order_id"])
    assert o.is_offline_origin and o.payments.count() == 1 and o.payment_status == "paid"
