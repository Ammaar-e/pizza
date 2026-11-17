import pytest

from apps.core.models import AuditLog
from apps.payments.models import CashSession, CashTransaction, ShiftClosing
from conftest import login, register_device

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_registers(seeded, api):
    t1, d1 = register_device(api, name="Front 1")
    t2, d2 = register_device(api, name="Front 2")
    return (t1, d1), (t2, d2)


def client_for(username, token):
    from rest_framework.test import APIClient

    c = APIClient()
    login(c, username, store_number="1001", device_token=token)
    return c


def cash_order(c, store, sku="DR-COLA"):
    o = c.post("/api/v1/orders/", {"store_id": store.id, "order_type": "pickup", "items": [{"product": sku}]},
               format="json").json()
    r = c.post(f"/api/v1/orders/{o['id']}/payments/", {"method": "cash", "amount_cents": o["total_cents"],
                                                       "tendered_cents": o["total_cents"]}, format="json")
    assert r.status_code == 201, r.content
    return o


def test_separate_registers_for_separate_employees(seeded, two_registers):
    from apps.accounts.models import StoreAssignment, User

    (t1, _), (t2, _) = two_registers
    u2 = User.objects.create_user("cashier2.1001", password="Test!Pass2026", role=User.objects.get(
        username="cashier.1001").role)
    StoreAssignment.objects.create(user=u2, store=seeded["1001"])
    a = client_for("cashier.1001", t1)
    b = client_for("cashier2.1001", t2)
    sa = a.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 10000}, format="json")
    sb = b.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 5000}, format="json")
    assert sa.status_code == 201 and sb.status_code == 201
    assert sa.json()["register"] != sb.json()["register"]
    assert sa.json()["expected_now_cents"] is None  # blind count for cashiers
    # A second session on the same register is refused
    assert a.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 1}, format="json").status_code == 409
    oa = cash_order(a, seeded["1001"])
    ob = cash_order(b, seeded["1001"], "SD-GBREAD")
    s1 = CashSession.objects.get(pk=sa.json()["id"])
    s2 = CashSession.objects.get(pk=sb.json()["id"])
    from apps.payments.cash import expected_cash

    assert expected_cash(s1) == 10000 + oa["total_cents"]
    assert expected_cash(s2) == 5000 + ob["total_cents"]
    # Cashiers only see their own drawers
    assert [s["id"] for s in a.get("/api/v1/cash-sessions/").json()["results"]] == [str(s1.pk)]
    assert b.get(f"/api/v1/cash-sessions/{s1.pk}/").status_code == 404


def test_full_reconciliation_shortage_requires_explanation_and_approval(seeded, two_registers, as_user, api):
    (t1, _), _ = two_registers
    c = client_for("cashier.1001", t1)
    s = c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 20000}, format="json").json()
    o = cash_order(c, seeded["1001"], "SD-WINGS")
    sid = s["id"]
    assert c.post(f"/api/v1/cash-sessions/{sid}/transactions/", {"kind": "drop", "amount_cents": 10000},
                  format="json").status_code == 201
    # paid-out needs a manager (cashier lacks cash.paid_in_out)
    r = c.post(f"/api/v1/cash-sessions/{sid}/transactions/", {"kind": "paid_out", "amount_cents": 1500,
                                                              "reason": "Napkins"}, format="json")
    assert r.status_code == 403
    r = c.post(f"/api/v1/cash-sessions/{sid}/transactions/", {"kind": "petty_expense", "amount_cents": 1500,
                                                              "reason": "Napkins", "expense_category": "supplies",
                                                              "manager_code": "1234"}, format="json")
    assert r.status_code == 201 and r.json()["amount_cents"] == -1500
    r = c.post(f"/api/v1/cash-sessions/{sid}/transactions/", {"kind": "addition", "amount_cents": 2000,
                                                              "reason": "Change from safe", "manager_code": "1234"},
               format="json")
    assert r.status_code == 201
    expected = 20000 + o["total_cents"] - 10000 - 1500 + 2000
    counted = expected - 900  # $9 short, threshold is $5
    r = c.post(f"/api/v1/cash-sessions/{sid}/close/", {"counted_cash_cents": counted}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "explanation_required"
    assert CashSession.objects.get(pk=sid).status == "open"
    r = c.post(f"/api/v1/cash-sessions/{sid}/close/", {"counted_cash_cents": counted,
                                                       "explanation": "Gave wrong change to a customer"}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["status"] == "pending_approval" and body["variance_cents"] == -900
    assert body["expected_cash_cents"] == expected
    rec = c.get(f"/api/v1/cash-sessions/{sid}/reconciliation/").json()["reconciliation"]
    assert rec["cash_sales_cents"] == o["total_cents"] and rec["drops_cents"] == 10000
    assert rec["petty_expenses_cents"] == 1500 and rec["additions_cents"] == 2000
    assert rec["discrepancies"][0]["kind"] == "shortage" and rec["discrepancies"][0]["status"] == "open"
    # Cashier cannot approve; the manager can.
    assert c.post(f"/api/v1/cash-sessions/{sid}/approve/", {}, format="json").status_code == 403
    m = as_user("manager.1001")
    r = m.post(f"/api/v1/cash-sessions/{sid}/approve/", {"notes": "Reviewed with employee"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["approved_by_name"] == "manager.1001"
    sc = ShiftClosing.objects.get(session_id=sid)
    assert sc.status == "approved" and sc.summary["cash"]["variance_cents"] == -900
    assert sc.summary["sales"]["order_count"] == 1
    assert AuditLog.objects.filter(action="cash_session.approve", entity_id=sid).exists()
    text = m.get(f"/api/v1/shift-closings/{sc.pk}/print/").json()["text"]
    assert "SHIFT CLOSING REPORT" in text and "Over / (short)" in text
    csv = m.get(f"/api/v1/shift-closings/{sc.pk}/export/")
    assert csv["Content-Type"] == "text/csv" and b"variance_cents" in csv.content


def test_overage_within_threshold_closes_without_approval(seeded, two_registers):
    (t1, _), _ = two_registers
    c = client_for("cashier.1001", t1)
    s = c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 5000}, format="json").json()
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/close/", {
        "counted_cash_cents": 5200, "denominations": {"2000": 2, "1000": 1, "100": 2}}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["variance_cents"] == 200
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/close/", {"counted_cash_cents": 5200}, format="json")
    assert r.status_code == 409


def test_finalized_session_immutable_and_corrections_are_separate(seeded, two_registers, as_user):
    (t1, _), _ = two_registers
    c = client_for("cashier.1001", t1)
    s = c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 5000}, format="json").json()
    c.post(f"/api/v1/cash-sessions/{s['id']}/close/", {"counted_cash_cents": 5000}, format="json")
    session = CashSession.objects.get(pk=s["id"])
    session.counted_cash_cents = 9999
    with pytest.raises(ValueError):
        session.save()
    txn = CashTransaction.objects.create(session=session, kind="correction", amount_cents=1, user=session.employee)
    txn.amount_cents = 2
    with pytest.raises(ValueError):
        txn.save()
    # Non-correction transactions are refused after close; corrections need a manager code.
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/transactions/", {"kind": "drop", "amount_cents": 100}, format="json")
    assert r.status_code == 409
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/transactions/", {"kind": "correction", "amount_cents": -300,
                                                                  "reason": "Uncounted refund slip"}, format="json")
    assert r.status_code == 403
    r = c.post(f"/api/v1/cash-sessions/{s['id']}/transactions/", {
        "kind": "correction", "amount_cents": -300, "reason": "Uncounted refund slip", "manager_code": "1234"},
        format="json")
    assert r.status_code == 201 and r.json()["post_close"] is True
    m = as_user("manager.1001")
    adj = m.get(f"/api/v1/cash-sessions/{s['id']}/").json()["adjustments"]
    assert adj == {"post_close_corrections_cents": -300, "adjusted_expected_cents": 4700, "adjusted_variance_cents": 300}
    log = AuditLog.objects.get(action="cash.correction")
    assert log.authorized_by.username == "manager.1001" and log.data["post_close"] is True


def test_register_transfer_and_idempotent_transactions(seeded, two_registers, as_user):
    (t1, _), (t2, _) = two_registers
    m1 = client_for("manager.1001", t1)
    s1 = m1.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 10000}, format="json").json()
    m2 = client_for("cashier.1001", t2)
    s2 = m2.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 1000}, format="json").json()
    r = m1.post(f"/api/v1/cash-sessions/{s1['id']}/transfer/", {"to_session": s2["id"], "amount_cents": 3000,
                                                                "reason": "Change"}, format="json")
    assert r.status_code == 201
    from apps.payments.cash import expected_cash

    assert expected_cash(CashSession.objects.get(pk=s1["id"])) == 7000
    assert expected_cash(CashSession.objects.get(pk=s2["id"])) == 4000
    r = m1.post(f"/api/v1/cash-sessions/{s1['id']}/transfer/", {"to_session": s2["id"], "amount_cents": 999999},
                format="json")
    assert r.status_code == 422
    # Duplicate request (network retry) with the same Idempotency-Key records one drop only.
    for _ in range(3):
        r = m1.post(f"/api/v1/cash-sessions/{s1['id']}/transactions/", {"kind": "drop", "amount_cents": 500},
                    format="json", HTTP_IDEMPOTENCY_KEY="drop-abc")
        assert r.status_code == 201
    assert CashTransaction.objects.filter(kind="drop").count() == 1
    r = m1.post(f"/api/v1/cash-sessions/{s1['id']}/transactions/", {"kind": "drop", "amount_cents": 700},
                format="json", HTTP_IDEMPOTENCY_KEY="drop-abc")
    assert r.status_code == 409


def test_cash_reports_scoped(seeded, two_registers, as_user, api):
    (t1, _), _ = two_registers
    c = client_for("cashier.1001", t1)
    s = c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 1000}, format="json").json()
    c.post(f"/api/v1/cash-sessions/{s['id']}/close/", {"counted_cash_cents": 700, "explanation": "short"},
           format="json")
    hq = as_user("hq.admin").get("/api/v1/reports/cash/").json()
    assert hq["totals"]["shortage_sessions"] == 1 and hq["totals"]["pending_approval"] == 0
    assert hq["by_store"][0]["store__number"] == "1001"
    api.credentials()
    b = as_user("owner.b").get("/api/v1/reports/cash/").json()
    assert b["totals"]["closed_sessions"] == 0 and b["by_store"] == []
    api.credentials()
    assert as_user("cashier.1002").get("/api/v1/reports/cash/").status_code == 403


def test_kds_profile_disables_cash_on_device(seeded, api):
    token, _ = register_device(api, name="Kitchen", device_type="kds")
    c = client_for("manager.1001", token)
    r = c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 100}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
