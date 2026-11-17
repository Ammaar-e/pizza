from datetime import timedelta

import pytest
from django.utils import timezone

from apps.orders.models import Order
from apps.promotions.models import Promotion, PromotionCode, PromotionProduct, PromotionRedemption

pytestmark = pytest.mark.django_db


def pos(as_user, user="manager.1001", store="1001"):
    return as_user(user, store_number=store)


def place(c, store, items, **extra):
    return c.post("/api/v1/orders/", {"store_id": store.id, "order_type": "pickup", "items": items, **extra},
                  format="json")


def quote(c, store, items, **extra):
    return c.post(f"/api/v1/stores/{store.id}/quote/", {"order_type": "pickup", "items": items, **extra},
                  format="json").json()


LARGE_PEP = {"product": "PZ-PEP", "size": "L"}
BREAD = {"product": "SD-GBREAD"}


def test_auto_free_product_applies_and_tax_recalculated(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = pos(as_user)
    q = quote(c, s, [LARGE_PEP, BREAD])
    assert q["discount_cents"] == 599
    assert q["promotions"]["applied"][0]["name"].startswith("Free garlic bread")
    bread_line = q["lines"][1]
    assert bread_line["discount_cents"] == 599 and bread_line["discounted_total_cents"] == 0 and bread_line["tax_cents"] == 0
    with django_capture_on_commit_callbacks(execute=True):
        r = place(c, s, [LARGE_PEP, BREAD])
    o = r.json()
    assert o["discount_cents"] == 599 and o["subtotal_cents"] == 1749 + 599
    assert o["tax_cents"] == round(1749 * 0.13)  # tax on discounted amounts only
    assert o["total_cents"] == 1749 + o["tax_cents"]
    assert o["discounts"][0]["promotion"] is not None
    receipt = c.get(f"/api/v1/orders/{o['id']}/receipt/").json()["text"]
    assert "Free garlic bread" in receipt
    # medium pizza does not qualify
    assert quote(c, s, [{"product": "PZ-PEP", "size": "M"}, BREAD])["discount_cents"] == 0


def test_bundle_and_overlapping_promotions_stack(seeded, as_user):
    s = seeded["1001"]
    c = pos(as_user)
    q = quote(c, s, [{"product": "SD-WINGS"}, {"product": "DR-COLA"}, LARGE_PEP, BREAD], promo_codes=["PROMO5"])
    names = {a["name"] for a in q["promotions"]["applied"]}
    assert names == {"Wings & Cola deal $14.99", "Free garlic bread with any large pizza", "$5 off $20+"}
    assert q["discount_cents"] == (1299 + 299 - 1499) + 599 + 500
    # every line's discount is bounded by its price
    assert all(0 <= l["discounted_total_cents"] <= l["line_total_cents"] for l in q["lines"])


def test_non_combinable_priority_blocks_others_and_reports_reason(seeded, as_user):
    s = seeded["1001"]
    bogo = Promotion.objects.get(name="Weekend BOGO pizza")
    bogo.starts_at = timezone.now() - timedelta(days=1)
    bogo.days_of_week = []
    bogo.save()
    c = pos(as_user)
    q = quote(c, s, [LARGE_PEP, {"product": "PZ-CHEESE", "size": "L"}, BREAD], promo_codes=["PROMO5"])
    applied = [a["name"] for a in q["promotions"]["applied"]]
    assert applied == ["Weekend BOGO pizza"]
    assert q["discount_cents"] == 1599  # cheaper large pizza free
    assert any("Cannot be combined" in r["reason"] for r in q["promotions"]["rejected"])
    r = place(c, s, [LARGE_PEP, {"product": "PZ-CHEESE", "size": "L"}], promo_codes=["PROMO5"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "promotion_invalid"
    assert Order.objects.count() == 0


def test_expired_scheduled_and_daypart_rules(seeded, as_user):
    s = seeded["1001"]
    c = pos(as_user)
    p = Promotion.objects.get(name="$5 off $20+")
    p.ends_at = timezone.now() - timedelta(minutes=1)
    p.save()
    q = quote(c, s, [LARGE_PEP, BREAD, BREAD], promo_codes=["PROMO5"])
    assert q["valid"] is False and q["promotions"]["rejected"][0]["reason"] == "Promotion has expired"
    p.ends_at, p.starts_at = None, timezone.now() + timedelta(days=1)
    p.save()
    assert "not started" in quote(c, s, [LARGE_PEP], promo_codes=["PROMO5"])["promotions"]["rejected"][0]["reason"]
    p.starts_at = None
    p.days_of_week = [(s.local_now().weekday() + 1) % 7]
    p.save()
    assert quote(c, s, [LARGE_PEP], promo_codes=["PROMO5"])["promotions"]["rejected"][0]["reason"] == "Not valid today"
    p.days_of_week = []
    p.min_subtotal_cents = 99999
    p.save()
    assert "minimum order" in quote(c, s, [LARGE_PEP], promo_codes=["PROMO5"])["promotions"]["rejected"][0]["reason"]
    assert quote(c, s, [LARGE_PEP], promo_codes=["NOPE"])["promotions"]["rejected"][0]["reason"] == \
        "Unknown or invalid code"


def test_free_toppings_code_and_customer_limits(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = pos(as_user)
    item = {"product": "PZ-CHEESE", "size": "M", "modifiers": [{"code": "TOP-BACON"}, {"code": "TOP-MUSH"},
                                                                 {"code": "TOP-OLIVE"}]}
    q = quote(c, s, [item], promo_codes=["TOPPINGS2"])
    assert "Customer phone or email is required" in q["promotions"]["rejected"][0]["reason"]
    cust = {"name": "Ana", "phone": "+1 555 222 3333"}
    q = quote(c, s, [item], promo_codes=["TOPPINGS2"], customer=cust)
    assert q["discount_cents"] == 200 + 150  # two most expensive toppings (M size = 100%)
    with django_capture_on_commit_callbacks(execute=True):
        first = place(c, s, [item], promo_codes=["TOPPINGS2"], customer=cust)
    assert first.status_code == 201 and first.json()["discount_cents"] == 350
    second = place(c, s, [item], promo_codes=["TOPPINGS2"], customer={"phone": "+15552223333"})
    assert second.status_code == 422 and "maximum number of times" in second.json()["error"]["errors"][0]["message"]
    # Cancelling the order releases the redemption
    with django_capture_on_commit_callbacks(execute=True):
        c.post(f"/api/v1/orders/{first.json()['id']}/cancel/", {"reason": "test"}, format="json")
    assert PromotionRedemption.objects.get(order_id=first.json()["id"]).status == "voided"
    assert place(c, s, [item], promo_codes=["TOPPINGS2"], customer=cust).status_code == 201


def test_max_redemptions_and_single_use_code(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    p = Promotion.objects.create(name="One-off", promo_type="fixed", value=100, requires_code=True, status="active")
    PromotionCode.objects.create(promotion=p, code="ONCE", max_uses=1)
    c = pos(as_user)
    with django_capture_on_commit_callbacks(execute=True):
        assert place(c, s, [LARGE_PEP], promo_codes=["ONCE"]).status_code == 201
    r = place(c, s, [LARGE_PEP], promo_codes=["ONCE"])
    assert r.status_code == 422 and "already been used" in r.json()["error"]["errors"][0]["message"]


def test_code_entered_after_order_creation_revalidates(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    c = as_user("cashier.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = place(c, s, [LARGE_PEP, BREAD]).json()
    assert o["discount_cents"] == 599
    r = c.post(f"/api/v1/orders/{o['id']}/discounts/", {"discount_code": "PROMO5"}, format="json")
    assert r.status_code == 200 and r.json()["discount_cents"] == 599 + 500
    # STAFF10 needs a manager; cashier without code is refused, with code accepted
    r = c.post(f"/api/v1/orders/{o['id']}/discounts/", {"discount_code": "STAFF10"}, format="json")
    assert r.status_code == 403
    r = c.post(f"/api/v1/orders/{o['id']}/discounts/", {"discount_code": "STAFF10", "manager_code": "1234"},
               format="json")
    assert r.status_code == 200
    body = r.json()
    assert {d["name"] for d in body["discounts"]} == {"Free garlic bread with any large pizza", "$5 off $20+",
                                                      "Staff 10%"}
    assert body["total_cents"] == body["subtotal_cents"] - body["discount_cents"] + body["tax_cents"]
    assert PromotionRedemption.objects.filter(order_id=o["id"], status="applied").count() == 3


def test_expected_total_includes_promotions(seeded, as_user):
    s = seeded["1001"]
    c = pos(as_user)
    q = quote(c, s, [LARGE_PEP, BREAD])
    assert place(c, s, [LARGE_PEP, BREAD], expected_total_cents=q["total_cents"] + 599).status_code == 422
    assert place(c, s, [LARGE_PEP, BREAD], expected_total_cents=q["total_cents"]).status_code == 201


def test_franchise_policy_and_isolation(seeded, as_user, api):
    a = as_user("owner.a")
    body = {"name": "A 15% off", "promo_type": "percent", "value": 1500, "auto_apply": True,
            "franchisee": seeded["1001"].franchisee_id}
    r = a.post("/api/v1/promotions/", body, format="json")
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    assert a.post("/api/v1/promotions/", {**body, "value": 9000}, format="json").status_code == 403  # > HQ max
    assert a.post("/api/v1/promotions/", {**body, "promo_type": "free_modifier"}, format="json").status_code == 403
    assert a.post("/api/v1/promotions/", {**body, "franchisee": None}, format="json").status_code == 403  # network
    r = a.post("/api/v1/promotions/", {**body, "franchisee": None, "targets": [{"store": seeded["2001"].id}],
                                       "store": seeded["2001"].id}, format="json")
    assert r.status_code == 403
    api.credentials()
    b = as_user("owner.b")
    assert pid not in [p["id"] for p in b.get("/api/v1/promotions/").json()["results"]]
    api.credentials()
    c = pos(as_user, "manager.2001", "2001")
    assert quote(c, seeded["2001"], [LARGE_PEP])["discount_cents"] == 0  # A's promo never applies in B's store
    api.credentials()
    c = pos(as_user)
    assert quote(c, seeded["1001"], [{"product": "DR-COLA"}])["discount_cents"] == round(299 * 0.15)
    # Store managers in franchise A are authorized by the franchisee; B's are not.
    store_body = {"name": "Store deal", "promo_type": "fixed", "value": 200, "store": seeded["1001"].id,
                  "requires_code": True, "codes": [{"code": "LOCAL2"}]}
    assert c.post("/api/v1/promotions/", store_body, format="json").status_code == 201
    api.credentials()
    m2 = as_user("manager.2001")
    assert m2.post("/api/v1/promotions/", {**store_body, "store": seeded["2001"].id, "codes": [{"code": "LOCAL3"}]},
                   format="json").status_code == 403


def test_hq_publishes_to_selected_stores_and_reports(seeded, as_user, api, django_capture_on_commit_callbacks):
    hq = as_user("hq.admin")
    r = hq.post("/api/v1/promotions/", {"name": "HQ cola", "promo_type": "percent", "value": 5000, "auto_apply": True,
                                        "campaign": "Summer", "targets": [{"store": seeded["1002"].id}],
                                        "items": [{"role": "eligible", "product": "DR-COLA"}]}, format="json")
    assert r.status_code == 201, r.content
    api.credentials()
    c = pos(as_user)
    assert all(a["name"] != "HQ cola" for a in quote(c, seeded["1001"], [{"product": "DR-COLA"}])
               ["promotions"]["applied"])
    api.credentials()
    c2 = pos(as_user, "manager.1002", "1002")
    with django_capture_on_commit_callbacks(execute=True):
        o = place(c2, seeded["1002"], [{"product": "DR-COLA"}]).json()
    assert o["discount_cents"] == 150
    api.credentials()
    hq = as_user("hq.admin")
    rep = hq.get("/api/v1/reports/promotions/").json()
    row = next(p for p in rep["promotions"] if p["name"] == "HQ cola")
    assert row["redemptions"] == 1 and row["discount_cents"] == 150 and row["by_store"][0]["store__number"] == "1002"
    assert rep["by_campaign"][0]["campaign"] == "Summer"
    assert rep["by_product"][0]["sku"] == "DR-COLA"
    r = hq.post(f"/api/v1/promotions/{row['promotion_id']}/disable/", {}, format="json")
    assert r.json()["status"] == "disabled"


def test_marketplace_orders_never_get_promotions(seeded, as_user):
    c = pos(as_user)
    r = c.post("/api/v1/orders/", {"store_id": seeded["1001"].id, "order_type": "provider_delivery", "source": "skip",
                                   "external_order_id": "S-1", "items": [LARGE_PEP, BREAD]}, format="json")
    assert r.status_code == 201 and r.json()["discount_cents"] == 0


def test_scheduled_task_updates_status(seeded):
    from apps.promotions.tasks import refresh_promotion_statuses

    p = Promotion.objects.get(name="Weekend BOGO pizza")
    assert p.status == "scheduled"
    p.starts_at = timezone.now() - timedelta(seconds=1)
    p.save()
    assert refresh_promotion_statuses() >= 1
    p.refresh_from_db()
    assert p.status == "active"


def test_bogo_cheapest_free_in_each_pair(seeded, as_user):
    p = Promotion.objects.create(name="Pizza BOGO", promo_type="bogo", auto_apply=True, status="active")
    from apps.catalog.models import Category

    PromotionProduct.objects.create(promotion=p, role="eligible", category=Category.objects.get(code="PIZZA"))
    c = pos(as_user)
    q = quote(c, seeded["1001"], [{"product": "PZ-DLX", "size": "XL"}, {"product": "PZ-CHEESE", "size": "S"},
                                  {"product": "PZ-PEP", "size": "L"}])
    bogo = next(a for a in q["promotions"]["applied"] if a["name"] == "Pizza BOGO")
    assert bogo["amount_cents"] == 1749  # pairs by price: (XL deluxe, L pep) -> pep free; small cheese unpaired
