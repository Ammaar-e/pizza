"""HQ menu setup on a fresh installation: reference data can be created from the portal."""
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.models import Category, Modifier, PrepCategory, TaxCategory, TaxRate

pytestmark = pytest.mark.django_db


@pytest.fixture
def hq():
    c = APIClient()
    c.force_authenticate(User.objects.create_superuser("admin", "a@example.com", "Fresh-Install-2026"))
    return c


def test_starter_setup_is_idempotent_and_enables_products(hq):
    r = hq.post("/api/v1/catalog/starter-setup/", {}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["created"]["categories"] == 3 and r.json()["created"]["tax_rates"] == 4
    assert hq.post("/api/v1/catalog/starter-setup/", {}, format="json").json()["created"]["categories"] == 0
    assert Modifier.objects.filter(group__code="TOPPINGS").count() == 12
    p = hq.post("/api/v1/products/", {
        "sku": "PZ-MARG", "name": "Margherita", "status": "approved", "category": Category.objects.get(code="PIZZA").id,
        "tax_category": TaxCategory.objects.get(code="FOOD").id, "prep_category": PrepCategory.objects.get(code="OVEN").id,
        "base_price_cents": 1299, "sizes": [], "crusts": [], "modifier_group_links": [], "default_modifier_links": []},
        format="json")
    assert p.status_code == 201, p.content


def test_create_reference_data_by_hand(hq):
    cat = hq.post("/api/v1/categories/", {"code": "DESSERT", "name": "Desserts", "sort_order": 4, "is_active": True}, format="json")
    tax = hq.post("/api/v1/tax-categories/", {"code": "FOOD", "name": "Prepared food"}, format="json")
    rate = hq.post("/api/v1/tax-rates/", {"tax_category": tax.json()["id"], "region": "AB", "name": "GST", "rate_bps": 500}, format="json")
    prep = hq.post("/api/v1/prep-categories/", {"code": "OVEN", "name": "Pizza oven"}, format="json")
    assert [x.status_code for x in (cat, tax, rate, prep)] == [201, 201, 201, 201]
    assert TaxRate.objects.get().rate_bps == 500


def test_deleting_a_category_in_use_is_a_clear_conflict(hq):
    hq.post("/api/v1/catalog/starter-setup/", {}, format="json")
    cat = Category.objects.get(code="PIZZA")
    hq.post("/api/v1/products/", {
        "sku": "PZ-X", "name": "X", "status": "draft", "category": cat.id,
        "tax_category": TaxCategory.objects.get(code="FOOD").id, "prep_category": PrepCategory.objects.get(code="OVEN").id,
        "base_price_cents": 999, "sizes": [], "crusts": [], "modifier_group_links": [], "default_modifier_links": []}, format="json")
    r = hq.delete(f"/api/v1/categories/{cat.id}/")
    assert r.status_code == 409 and r.json()["error"]["code"] == "in_use"
