import pytest

from apps.integrations.models import IntegrationProvider, ProviderAccount, StoreIntegration
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def make_order(client, store, sku="DR-COLA"):
    r = client.post("/api/v1/orders/", {"store_id": store.id, "order_type": "pickup",
                                        "items": [{"product": sku}]}, format="json")
    assert r.status_code == 201, r.content
    return r.json()


def test_franchisee_sees_only_own_stores_and_franchisee(seeded, as_user):
    c = as_user("owner.a")
    stores = {s["number"] for s in c.get("/api/v1/stores/").json()["results"]}
    assert stores == {"1001", "1002"}
    fr = c.get("/api/v1/franchisees/").json()["results"]
    assert [f["code"] for f in fr] == ["A"]
    assert c.get(f"/api/v1/stores/{seeded['2001'].id}/").status_code == 404
    assert c.patch(f"/api/v1/stores/{seeded['2001'].id}/", {"name": "x"}, format="json").status_code == 404
    assert c.get(f"/api/v1/stores/{seeded['2001'].id}/integrations/").status_code == 404
    assert c.get(f"/api/v1/stores/{seeded['2001'].id}/menu-config/").status_code == 403


def test_cross_franchise_orders_invisible(seeded, as_user, api):
    b = as_user("manager.2001", store_number="2001")
    ob = make_order(b, seeded["2001"])
    api.credentials()
    a = as_user("owner.a")
    assert a.get(f"/api/v1/orders/{ob['id']}/").status_code == 404
    assert a.post(f"/api/v1/orders/{ob['id']}/cancel/", {"reason": "x"}, format="json").status_code == 404
    assert all(o["store_number"] in ("1001", "1002") for o in a.get("/api/v1/orders/").json()["results"])
    assert a.get(f"/api/v1/stores/{seeded['2001'].id}/orders/").json()["results"] == []


def test_store_employee_limited_to_assigned_store(seeded, as_user, api):
    c = as_user("cashier.1001", store_number="1001")
    r = c.post("/api/v1/orders/", {"store_id": seeded["1002"].id, "order_type": "pickup",
                                   "items": [{"product": "DR-COLA"}]}, format="json")
    assert r.status_code == 403
    api.credentials()
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1002", "username": "cashier.1001",
                                             "password": "Test!Pass2026"}, format="json")
    assert r.status_code in (401, 403)
    assert "not assigned" in r.json()["error"]["message"]


def test_reports_scoped(seeded, as_user, api):
    a = as_user("manager.1001", store_number="1001")
    make_order(a, seeded["1001"])
    api.credentials()
    b = as_user("manager.2001", store_number="2001")
    make_order(b, seeded["2001"])
    api.credentials()
    owner_b = as_user("owner.b")
    rep = owner_b.get("/api/v1/reports/summary/").json()
    assert {s["store__number"] for s in rep["by_store"]} == {"2001"}
    api.credentials()
    hq = as_user("hq.admin")
    rep = hq.get("/api/v1/reports/summary/").json()
    assert {s["store__number"] for s in rep["by_store"]} == {"1001", "2001"}


def test_integration_secrets_encrypted_and_never_returned(seeded, as_user):
    from django.db import connection

    prov = IntegrationProvider.objects.get(code="doordash")
    prov.network_enabled = True
    prov.save()
    a = as_user("owner.a")
    fa = seeded["1001"].franchisee
    r = a.post("/api/v1/provider-accounts/", {"provider": "doordash", "franchisee": fa.id, "name": "A DoorDash",
                                              "credentials": {"developer_id": "d", "key_id": "k",
                                                              "signing_secret": "SUPERSECRETVALUE-123456"}},
               format="json")
    assert r.status_code == 201, r.content
    assert "SUPERSECRETVALUE" not in r.content.decode()
    acc_id = r.json()["id"]
    r = a.put(f"/api/v1/stores/{seeded['1001'].id}/integrations/doordash/",
              {"enabled": True, "account": acc_id, "external_store_id": "1001",
               "credentials": {"extra_token": "STORE-SECRET-987654321"}}, format="json")
    assert r.status_code == 200, r.content
    body = a.get(f"/api/v1/stores/{seeded['1001'].id}/integrations/").content.decode()
    assert "SUPERSECRETVALUE" not in body and "STORE-SECRET" not in body
    with connection.cursor() as cur:
        cur.execute("SELECT credentials FROM integrations_provideraccount WHERE id=%s", [acc_id])
        raw = cur.fetchone()[0]
    assert "SUPERSECRETVALUE" not in raw and raw.startswith("gAAAA")  # Fernet token
    assert ProviderAccount.objects.get(pk=acc_id).credentials["signing_secret"] == "SUPERSECRETVALUE-123456"


def test_franchisee_cannot_use_other_franchisee_provider_account(seeded, as_user):
    prov = IntegrationProvider.objects.get(code="doordash")
    prov.network_enabled = True
    prov.save()
    acc_b = ProviderAccount.objects.create(provider=prov, name="B", franchisee=seeded["2001"].franchisee,
                                           credentials={"signing_secret": "x"})
    a = as_user("owner.a")
    r = a.put(f"/api/v1/stores/{seeded['1001'].id}/integrations/doordash/", {"account": acc_b.id}, format="json")
    assert r.status_code == 403
    assert acc_b.id not in [x["id"] for x in a.get("/api/v1/provider-accounts/").json()["results"]]


def test_hq_controls_permitted_providers(seeded, as_user, api):
    a = as_user("owner.a")
    r = a.put(f"/api/v1/stores/{seeded['1001'].id}/integrations/ubereats/", {"enabled": True}, format="json")
    assert r.status_code == 403  # not network-enabled by HQ
    assert a.patch("/api/v1/integration-providers/ubereats/", {"network_enabled": True},
                   format="json").status_code == 403
    api.credentials()
    hq = as_user("hq.admin")
    assert hq.patch("/api/v1/integration-providers/ubereats/", {"network_enabled": True},
                    format="json").status_code == 200
    api.credentials()
    a = as_user("owner.a")
    r = a.put(f"/api/v1/stores/{seeded['1001'].id}/integrations/ubereats/", {"enabled": True}, format="json")
    assert r.status_code == 200
    assert StoreIntegration.objects.get(store=seeded["1001"], provider__code="ubereats").enabled


def test_store_manager_cannot_escalate_roles(seeded, as_user):
    c = as_user("manager.1001")
    r = c.post("/api/v1/users/", {"username": "new.owner", "role": "franchisee_owner", "password": "Xy!12345678",
                                  "store_ids": [seeded["1001"].id]}, format="json")
    assert r.status_code == 403
    r = c.post("/api/v1/users/", {"username": "new.cashier", "role": "cashier", "password": "Xy!12345678",
                                  "store_ids": [seeded["2001"].id]}, format="json")
    assert r.status_code in (400, 403)
    r = c.post("/api/v1/users/", {"username": "new.cashier", "role": "cashier", "password": "Xy!12345678",
                                  "store_ids": [seeded["1001"].id], "pin": "5555"}, format="json")
    assert r.status_code == 201, r.content
    assert "pin" not in r.json() and r.json()["has_pin"] is True
