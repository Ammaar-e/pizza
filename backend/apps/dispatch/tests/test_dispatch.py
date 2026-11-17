import pytest

from apps.dispatch.models import DeliveryAssignment, DriverProfile
from apps.kitchen.models import KitchenTicket
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

ADDR = {"line1": "1 Front St", "city": "Toronto", "postal_code": "M5V 1A1"}


def new_client(username, store="1001"):
    from rest_framework.test import APIClient

    from conftest import login

    c = APIClient()
    login(c, username, store_number=store)
    return c


def delivery_order(c, store, address=ADDR, items=None):
    return c.post("/api/v1/orders/", {"store_id": store.id, "order_type": "delivery", "delivery_address": address,
                                      "customer": {"name": "Dee", "phone": "+15550001"},
                                      "items": items or [{"product": "PZ-PEP", "size": "L"}]}, format="json")


def make_ready(order_id, user):
    from apps.kitchen.services import bump_ticket

    for t in KitchenTicket.objects.filter(order_id=order_id):
        bump_ticket(t, "ready", user)


@pytest.fixture
def drivers(seeded):
    from apps.accounts.models import StoreAssignment, User

    d1 = DriverProfile.objects.get(user__username="driver.1001")
    u2 = User.objects.create_user("driver2.1001", password="Test!Pass2026", role=d1.user.role)
    StoreAssignment.objects.create(user=u2, store=seeded["1001"])
    d2 = DriverProfile.objects.create(user=u2)
    return d1, d2


def test_zone_validation_and_fees(seeded, as_user):
    s2 = seeded["1002"]
    c = as_user("manager.1002", store_number="1002")
    r = delivery_order(c, s2, {"line1": "x", "city": "Toronto", "postal_code": "M1M 1M1"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "address_location_required"
    r = delivery_order(c, s2, {"line1": "x", "city": "Toronto", "postal_code": "M4S 2B2"})
    assert r.status_code == 201 and r.json()["charges"][0]["amount_cents"] == 299  # Core zone by postal prefix
    far = {"line1": "x", "city": "Toronto", "postal_code": "M1M1M1", "latitude": 43.7444, "longitude": -79.3986}
    r = delivery_order(c, s2, far)  # ~4.5 km -> Extended zone, $25 minimum
    assert r.status_code == 422 and r.json()["error"]["code"] == "below_delivery_minimum"
    r = delivery_order(c, s2, far, items=[{"product": "PZ-DLX", "size": "XL"}, {"product": "SD-WINGS"}])
    assert r.status_code == 201 and r.json()["charges"][0]["amount_cents"] == 499
    very_far = {**far, "latitude": 44.5}
    assert delivery_order(c, s2, very_far).json()["error"]["code"] == "outside_delivery_area"
    q = c.post(f"/api/v1/stores/{s2.id}/quote/", {"order_type": "delivery", "delivery_address": far,
                                                  "items": [{"product": "DR-COLA"}]}, format="json").json()
    assert q["valid"] is False and q["delivery"]["code"] == "below_delivery_minimum"


def test_lifecycle_assignment_and_driver_flow(seeded, as_user, drivers, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    s.delivery_config = {**s.delivery_config, "auto_assign": False}
    s.save()
    d1, d2 = drivers
    m = as_user("manager.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = delivery_order(m, s).json()
    a = DeliveryAssignment.objects.get(order_id=o["id"])
    assert a.status == "awaiting_preparation" and a.fee_cents == s.delivery_config["delivery_fee_cents"]
    # Unavailable drivers cannot be assigned
    r = m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d1.id}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "driver_unavailable"
    for d in (d1, d2):
        assert m.post(f"/api/v1/drivers/{d.id}/availability/", {"status": "available", "store": s.id},
                      format="json").status_code == 200
    r = m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d1.id, "version": a.version}, format="json")
    assert r.status_code == 200 and r.json()["driver"]["username"] == "driver.1001"
    stale = m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d2.id, "version": a.version}, format="json")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    r = m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d2.id}, format="json")  # reassign
    assert r.json()["driver"]["username"] == "driver2.1001"
    # Drivers only see their own deliveries
    drv1 = new_client("driver.1001")
    drv2 = new_client("driver2.1001")
    assert drv1.get("/api/v1/driver/me/").json()["active"] == []
    assert drv1.get(f"/api/v1/driver/deliveries/{a.id}/").status_code == 404
    assert drv1.get(f"/api/v1/stores/{s.id}/dispatch/").status_code == 403
    mine = drv2.get("/api/v1/driver/me/").json()["active"]
    assert [x["id"] for x in mine] == [a.id] and mine[0]["maps_url"].startswith("https://www.google.com/maps/dir/")
    # Pickup is refused until the kitchen marks the order ready
    r = drv2.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "picked_up"}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "order_not_ready"
    from apps.accounts.models import User

    with django_capture_on_commit_callbacks(execute=True):
        make_ready(o["id"], User.objects.get(username="kitchen.1001"))
    a.refresh_from_db()
    assert a.status == "assigned"  # already assigned: stays assigned when ready
    with django_capture_on_commit_callbacks(execute=True):
        assert drv2.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "picked_up"},
                         format="json").status_code == 200
    assert Order.objects.get(pk=o["id"]).status == "out_for_delivery"
    assert drv2.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "cancelled"},
                     format="json").status_code == 403
    with django_capture_on_commit_callbacks(execute=True):
        r = drv2.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "delivered", "note": "Left with concierge"},
                      format="json")
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    assert Order.objects.get(pk=o["id"]).status == "completed"
    hist = m.get(f"/api/v1/deliveries/{a.id}/").json()["history"]
    assert [h["to"] for h in hist] == ["awaiting_preparation", "assigned", "assigned", "picked_up", "delivered"]
    assert all(h["at"] for h in hist) and hist[3]["actor"] == "user:driver2.1001"
    assert any(h["previous_driver"] == "driver.1001" for h in hist)
    perf = m.get("/api/v1/reports/drivers/").json()["drivers"]
    assert perf[0]["username"] == "driver2.1001" and perf[0]["delivered"] == 1


def test_failed_delivery_redispatch_and_return(seeded, as_user, drivers, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    s.delivery_config = {**s.delivery_config, "auto_assign": False}
    s.save()
    d1, _ = drivers
    from apps.accounts.models import User
    from apps.dispatch.services import set_driver_status

    set_driver_status(d1, "available", None, s)
    m = as_user("manager.1001", store_number="1001")
    with django_capture_on_commit_callbacks(execute=True):
        o = delivery_order(m, s).json()
    with django_capture_on_commit_callbacks(execute=True):
        make_ready(o["id"], User.objects.get(username="kitchen.1001"))
    a = DeliveryAssignment.objects.get(order_id=o["id"])
    assert a.status == "ready_for_dispatch"
    m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d1.id}, format="json")
    drv = new_client("driver.1001")
    with django_capture_on_commit_callbacks(execute=True):
        drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "picked_up"}, format="json")
        assert drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "failed"},
                        format="json").status_code == 400  # reason required
        assert drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "failed",
                                                                       "failure_reason": "No answer at door"},
                        format="json").status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        r = m.post(f"/api/v1/deliveries/{a.id}/status/", {"status": "ready_for_dispatch"}, format="json")
    assert r.status_code == 200 and r.json()["driver"] is None
    assert Order.objects.get(pk=o["id"]).status == "ready"
    m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d1.id}, format="json")
    with django_capture_on_commit_callbacks(execute=True):
        drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "picked_up"}, format="json")
        drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "failed", "failure_reason": "Address closed"},
                 format="json")
        r = m.post(f"/api/v1/deliveries/{a.id}/status/", {"status": "returned"}, format="json")
    assert r.json()["status"] == "returned"
    o2 = Order.objects.get(pk=o["id"])
    assert o2.status == "cancelled" and "Address closed" in o2.cancellation_reason


def test_auto_assign_prefers_least_loaded_available_driver(seeded, as_user, drivers,
                                                           django_capture_on_commit_callbacks):
    s = seeded["1001"]
    d1, d2 = drivers
    from apps.accounts.models import User
    from apps.dispatch.services import set_driver_status

    set_driver_status(d1, "available", None, s)
    m = as_user("manager.1001", store_number="1001")
    kitchen = User.objects.get(username="kitchen.1001")
    with django_capture_on_commit_callbacks(execute=True):
        o1 = delivery_order(m, s).json()
    with django_capture_on_commit_callbacks(execute=True):
        make_ready(o1["id"], kitchen)
    a1 = DeliveryAssignment.objects.get(order_id=o1["id"])
    assert a1.driver_id == d1.id and a1.auto_assigned  # store 1001 has auto_assign enabled
    set_driver_status(d2, "available", None, s)
    with django_capture_on_commit_callbacks(execute=True):
        o2 = delivery_order(m, s).json()
    with django_capture_on_commit_callbacks(execute=True):
        make_ready(o2["id"], kitchen)
    assert DeliveryAssignment.objects.get(order_id=o2["id"]).driver_id == d2.id  # least loaded
    set_driver_status(d1, "unavailable", None, s)
    set_driver_status(d2, "off_shift", None, s)
    with django_capture_on_commit_callbacks(execute=True):
        o3 = delivery_order(m, s).json()
    with django_capture_on_commit_callbacks(execute=True):
        make_ready(o3["id"], kitchen)
    a3 = DeliveryAssignment.objects.get(order_id=o3["id"])
    assert a3.driver_id is None and a3.status == "ready_for_dispatch"
    r = m.post(f"/api/v1/deliveries/{a3.id}/auto-assign/", {}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_driver_available"


def test_capacity_multi_assign_and_tenant_isolation(seeded, as_user, drivers, api, django_capture_on_commit_callbacks):
    s = seeded["1001"]
    s.delivery_config = {**s.delivery_config, "auto_assign": False}
    s.save()
    d1, _ = drivers
    d1.max_active_deliveries = 2
    d1.save()
    from apps.dispatch.services import set_driver_status

    set_driver_status(d1, "available", None, s)
    m = as_user("manager.1001", store_number="1001")
    ids = []
    for _ in range(3):
        with django_capture_on_commit_callbacks(execute=True):
            ids.append(DeliveryAssignment.objects.get(order_id=delivery_order(m, s).json()["id"]).id)
    r = m.post(f"/api/v1/stores/{s.id}/dispatch/assign-many/", {"driver": d1.id, "deliveries": ids}, format="json")
    res = r.json()["results"]
    assert [x["ok"] for x in res] == [True, True, False] and "max 2" in res[2]["error"]
    board = m.get(f"/api/v1/stores/{s.id}/dispatch/?driver={d1.id}").json()
    assert len(board["deliveries"]) == 2 and board["drivers"][0]["active_deliveries"] in (0, 2)
    other = new_client("manager.2001", "2001")
    assert other.get(f"/api/v1/stores/{s.id}/dispatch/").status_code == 404
    assert other.post(f"/api/v1/deliveries/{ids[0]}/assign/", {"driver": d1.id}, format="json").status_code == 404
    # a driver from another store cannot be assigned
    d_other = DriverProfile.objects.get(user__username="driver.2001")
    set_driver_status(d_other, "available", None, seeded["2001"])
    r = m.post(f"/api/v1/deliveries/{ids[2]}/assign/", {"driver": d_other.id}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "driver_not_assigned_to_store"


def test_proof_of_delivery_required_when_configured(seeded, as_user, django_capture_on_commit_callbacks):
    s = seeded["1002"]  # demo config: photo proof required
    d = DriverProfile.objects.get(user__username="driver.1002")
    from apps.accounts.models import User
    from apps.dispatch.services import set_driver_status

    set_driver_status(d, "available", None, s)
    m = as_user("manager.1002", store_number="1002")
    with django_capture_on_commit_callbacks(execute=True):
        o = delivery_order(m, s, {"line1": "x", "city": "Toronto", "postal_code": "M4S 1A1"}).json()
    with django_capture_on_commit_callbacks(execute=True):
        make_ready(o["id"], User.objects.get(username="kitchen.1002"))
    a = DeliveryAssignment.objects.get(order_id=o["id"])
    m.post(f"/api/v1/deliveries/{a.id}/assign/", {"driver": d.id}, format="json")
    drv = new_client("driver.1002", "1002")
    with django_capture_on_commit_callbacks(execute=True):
        drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "picked_up"}, format="json")
    r = drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "delivered"}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "proof_required"
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    r = drv.post(f"/api/v1/driver/deliveries/{a.id}/proof/", {"kind": "photo", "image_data": png,
                                                              "recipient_name": "Pat"}, format="json")
    assert r.status_code == 201
    with django_capture_on_commit_callbacks(execute=True):
        assert drv.post(f"/api/v1/driver/deliveries/{a.id}/status/", {"status": "delivered"},
                        format="json").status_code == 200


def test_marketplace_orders_are_not_dispatched(seeded, as_user):
    m = as_user("manager.1001", store_number="1001")
    r = m.post("/api/v1/orders/", {"store_id": seeded["1001"].id, "order_type": "provider_delivery", "source": "doordash",
                                   "external_order_id": "DD-MAN-1", "items": [{"product": "DR-COLA"}]}, format="json")
    assert r.status_code == 201
    assert not DeliveryAssignment.objects.filter(order_id=r.json()["id"]).exists()
