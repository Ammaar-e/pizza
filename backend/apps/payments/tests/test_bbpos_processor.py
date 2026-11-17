"""BBPOS card processor: the restaurant server verifies bbpos transactions server-to-server."""
import pytest

from apps.payments.models import Payment
from conftest import FakeResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def bbpos_store(seeded):
    s = seeded["1001"]
    s.payment_config = {"card_processor": "bbpos", "bbpos_api_url": "https://bbpos.test"}
    s.payment_secrets = {"bbpos_email": "svc@example.com", "bbpos_password": "Secret-123456"}
    s.save()
    return s


@pytest.fixture
def bbpos_http(monkeypatch):
    calls, txs = [], {}

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append(("POST", url, json))
        return FakeResponse(200, {"accessToken": "tok", "refreshToken": "r" * 30, "expiresIn": 900})

    def fake_request(method, url, json=None, headers=None, timeout=None):
        calls.append((method, url, json))
        assert headers["Authorization"] == "Bearer tok"
        if url.endswith("/confirm"):
            tid = url.split("/")[-2]
            return FakeResponse(200, txs[tid]) if tid in txs else FakeResponse(404, {"error": {"code": "NOT_FOUND"}})
        if "/refunds" in url:
            tid = url.split("/")[-2]
            if txs[tid].get("interac"):
                return FakeResponse(409, {"error": {"code": "INTERAC_REQUIRES_IN_PERSON", "message": "in person"}})
            return FakeResponse(201, {"id": "re_1", "amountCents": json["amountCents"]})
        raise AssertionError(url)

    import apps.payments.processors as p

    monkeypatch.setattr(p.requests, "post", fake_post)
    monkeypatch.setattr(p.requests, "request", fake_request)
    return {"calls": calls, "txs": txs}


def order(as_user, store):
    c = as_user("manager.1001", store_number="1001")
    o = c.post("/api/v1/orders/", {"store_id": store.id, "order_type": "pickup", "items": [{"product": "DR-COLA"}]},
               format="json").json()
    return c, o


def tx(amount, status="SUCCEEDED", **kw):
    return {"transactionId": "t", "status": status, "amountCents": amount, "currency": "CAD", "cardBrand": "visa",
            "last4": "4242", "mock": False, **kw}


def test_verified_card_payment_is_recorded(bbpos_store, bbpos_http, as_user):
    c, o = order(as_user, bbpos_store)
    bbpos_http["txs"]["tx_1"] = tx(o["total_cents"] + 100)
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"],
                                                       "tip_cents": 100, "reference": "tx_1"}, format="json",
               HTTP_IDEMPOTENCY_KEY="bbpos:tx_1")
    assert r.status_code == 201, r.content
    assert r.json()["payment"]["status"] == "captured" and r.json()["payment_status"] == "paid"
    p = Payment.objects.get(order_id=o["id"])
    assert p.processor == "bbpos" and p.processor_reference == "tx_1" and p.card_last4 == "4242"
    assert any(u.endswith("/api/payments/tx_1/confirm") for _, u, _ in bbpos_http["calls"])
    # Retrying with the same idempotency key returns the same payment, no second record.
    r2 = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"],
                                                        "tip_cents": 100, "reference": "tx_1"}, format="json",
                HTTP_IDEMPOTENCY_KEY="bbpos:tx_1")
    assert r2.status_code == 201 and Payment.objects.filter(order_id=o["id"]).count() == 1


def test_unverified_or_mismatched_transactions_are_refused(bbpos_store, bbpos_http, as_user):
    c, o = order(as_user, bbpos_store)
    bbpos_http["txs"].update({"declined": tx(o["total_cents"], "DECLINED"), "short": tx(o["total_cents"] - 1),
                              "mock": tx(o["total_cents"], mock=True)})
    for ref, reason in (("declined", "declined"), ("short", "expected"), ("mock", "Mock"), ("missing", "Unknown")):
        r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"],
                                                           "reference": ref}, format="json")
        assert r.status_code == 201
        assert r.json()["payment"]["status"] == "failed" and reason in r.json()["payment"]["failure_reason"]
    assert c.get(f"/api/v1/orders/{o['id']}/").json()["payment_status"] == "unpaid"
    assert c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"]},
                  format="json").status_code == 400  # reference required


def test_one_card_transaction_cannot_pay_two_orders(bbpos_store, bbpos_http, as_user):
    c, o1 = order(as_user, bbpos_store)
    o2 = c.post("/api/v1/orders/", {"store_id": bbpos_store.id, "order_type": "pickup",
                                    "items": [{"product": "DR-COLA"}]}, format="json").json()
    bbpos_http["txs"]["tx_9"] = tx(o1["total_cents"])
    assert c.post(f"/api/v1/orders/{o1['id']}/payments/", {"method": "card", "amount_cents": o1["total_cents"],
                                                           "reference": "tx_9"}, format="json").status_code == 201
    r = c.post(f"/api/v1/orders/{o2['id']}/payments/", {"method": "card", "amount_cents": o2["total_cents"],
                                                        "reference": "tx_9"}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "reference_reused"


def test_refunds_go_through_bbpos_and_interac_stays_pending(bbpos_store, bbpos_http, as_user):
    c, o = order(as_user, bbpos_store)
    bbpos_http["txs"]["tx_r"] = tx(o["total_cents"])
    pid = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"],
                                                         "reference": "tx_r"}, format="json").json()["payment"]["id"]
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": 100, "reason": "cold"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "succeeded" and r.json()["payment_status"] == "partially_refunded"
    refund_call = [x for x in bbpos_http["calls"] if "/refunds" in x[1]][0]
    assert refund_call[2]["amountCents"] == 100 and len(refund_call[2]["idempotencyKey"]) == 36
    bbpos_http["txs"]["tx_r"]["interac"] = True
    r = c.post(f"/api/v1/payments/{pid}/refund/", {"amount_cents": 50, "reason": "cold"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "pending"


def test_not_configured(seeded, as_user):
    s = seeded["1001"]
    s.payment_config = {"card_processor": "bbpos"}
    s.save()
    c, o = order(as_user, s)
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "card", "amount_cents": o["total_cents"],
                                                       "reference": "x"}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "processor_not_configured"


def test_failed_verification_can_be_retried_with_same_key(bbpos_store, bbpos_http, as_user):
    c, o = order(as_user, bbpos_store)
    body = {"method": "card", "amount_cents": o["total_cents"], "reference": "tx_late"}
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY="bbpos:tx_late")
    assert r.json()["payment"]["status"] == "failed"  # bbpos does not know it yet
    bbpos_http["txs"]["tx_late"] = tx(o["total_cents"])
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY="bbpos:tx_late")
    assert r.status_code == 201 and r.json()["payment"]["status"] == "captured" and r.json()["payment_status"] == "paid"
