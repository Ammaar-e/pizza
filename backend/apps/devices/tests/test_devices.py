from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.devices.models import DeviceAlert, DeviceConfiguration, DeviceCredential
from apps.organization.models import Device, Headquarters
from conftest import login, register_device

pytestmark = pytest.mark.django_db


def device_client(token):
    from rest_framework.test import APIClient

    c = APIClient()
    c.credentials(HTTP_X_DEVICE_TOKEN=token)
    return c


def hb(token, **data):
    return device_client(token).post("/api/v1/device-auth/heartbeat/", {"software_version": "1.1.0",
                                                                        "os_info": "iPadOS 18", **data}, format="json")


def test_staff_registration_requires_approval_then_works(seeded, api):
    token, dev_id = register_device(api, approver=None)
    d = Device.objects.get(pk=dev_id)
    assert d.status == "pending" and d.uid and token.startswith("dvc_")
    cred = DeviceCredential.objects.get(device=d)
    assert token.split(".", 1)[1] not in cred.secret_hash  # only a hash is stored
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1001", "username": "cashier.1001",
                                             "password": "Test!Pass2026"}, format="json", HTTP_X_DEVICE_TOKEN=token)
    assert r.status_code in (401, 403) and "awaiting approval" in r.json()["error"]["message"]
    assert hb(token).status_code == 202
    # cashier cannot approve; franchise owner can (HQ policy permits franchise approval)
    login(api, "manager.1001")
    assert api.post(f"/api/v1/devices/{dev_id}/approve/", {}, format="json").status_code == 403
    api.credentials()
    login(api, "owner.a")
    assert api.post(f"/api/v1/devices/{dev_id}/approve/", {}, format="json").json()["status"] == "active"
    api.credentials()
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1001", "username": "cashier.1001",
                                             "password": "Test!Pass2026"}, format="json", HTTP_X_DEVICE_TOKEN=token)
    assert r.status_code == 200 and r.json()["device"]["id"] == dev_id
    assert Device.objects.get(pk=dev_id).current_user.username == "cashier.1001"


def test_enrollment_code_flow_single_use(seeded, as_user, api):
    hq = as_user("hq.admin")
    r = hq.post("/api/v1/device-registrations/", {"store": seeded["2001"].id, "device_type": "register",
                                                   "name": "Register 2"}, format="json")
    assert r.status_code == 201
    code = r.json()["enrollment_code"]
    anon = device_client("")
    anon.credentials()
    r = anon.post("/api/v1/device-auth/enroll/", {"store_number": "1001", "enrollment_code": code}, format="json")
    assert r.status_code == 403  # wrong store
    r = anon.post("/api/v1/device-auth/enroll/", {"store_number": "2001", "enrollment_code": code.lower(),
                                                  "software_version": "1.1.0"}, format="json")
    assert r.status_code == 201 and r.json()["device"]["status"] == "active"
    token = r.json()["device_token"]
    assert anon.post("/api/v1/device-auth/enroll/", {"store_number": "2001", "enrollment_code": code},
                     format="json").status_code == 403  # single use
    assert hb(token).json()["status"] == "active"
    cfg = device_client(token).get("/api/v1/device-auth/config/").json()
    assert cfg["profile"]["name"] == "Standard POS" and cfg["device"]["store_number"] == "2001"


def test_revoked_and_deactivated_devices_denied_with_alert(seeded, api, as_user):
    token, dev_id = register_device(api)
    owner = as_user("owner.a")
    r = owner.post(f"/api/v1/devices/{dev_id}/revoke-credentials/", {"reason": "Tablet stolen"}, format="json")
    assert r.status_code == 200 and r.json()["revoked"] == 1 and r.json()["enrollment_code"]
    api.credentials()
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1001", "username": "cashier.1001",
                                             "password": "Test!Pass2026"}, format="json", HTTP_X_DEVICE_TOKEN=token)
    assert r.status_code in (401, 403) and "revoked" in r.json()["error"]["message"]
    login(api, "cashier.1001", store_number="1001")
    api.credentials(HTTP_AUTHORIZATION=api._credentials["HTTP_AUTHORIZATION"], HTTP_X_DEVICE_TOKEN=token)
    assert api.get("/api/v1/orders/").status_code in (401, 403)  # employee token alone does not rescue the device
    assert hb(token).status_code in (401, 403)
    assert DeviceAlert.objects.filter(device_id=dev_id, kind="unauthorized_access", resolved_at__isnull=True).exists()
    assert AuditLog.objects.filter(action="device.auth_failed", entity_id=str(dev_id)).exists()
    # Deactivation
    token2, dev2 = register_device(api, name="Front 2")
    owner = as_user("owner.a")
    assert owner.post(f"/api/v1/devices/{dev2}/deactivate/", {}, format="json").status_code == 400  # reason required
    assert owner.post(f"/api/v1/devices/{dev2}/deactivate/", {"reason": "Retired"},
                      format="json").json()["status"] == "deactivated"
    assert hb(token2).status_code in (401, 403)


def test_repeated_failures_raise_alert(seeded, api):
    token, dev_id = register_device(api)
    bad = token[:-4] + "XXXX"
    for _ in range(5):
        assert hb(bad).status_code in (401, 403)
    kinds = set(DeviceAlert.objects.filter(device_id=dev_id).values_list("kind", flat=True))
    assert {"auth_failures", "unauthorized_access"} <= kinds


def test_credential_rotation(seeded, api):
    token, dev_id = register_device(api)
    r = device_client(token).post("/api/v1/device-auth/rotate/")
    new = r.json()["device_token"]
    assert new != token
    assert hb(token).status_code in (401, 403)
    assert hb(new).status_code == 200


def test_heartbeat_timeout_detection_is_server_side(seeded, api):
    token, dev_id = register_device(api)
    r = hb(token, app_status="ok", sync_status="ok", pending_offline_count=0)
    assert r.status_code == 200 and r.json()["version_status"] == "ok"
    d = Device.objects.get(pk=dev_id)
    assert d.is_online and d.software_version == "1.1.0"
    from apps.devices.tasks import monitor_device_heartbeats

    assert monitor_device_heartbeats() == 0
    Device.objects.filter(pk=dev_id).update(last_heartbeat_at=timezone.now() - timedelta(minutes=10))
    assert monitor_device_heartbeats() == 1
    d.refresh_from_db()
    assert not d.is_online
    assert DeviceAlert.objects.get(device=d, kind="offline").resolved_at is None
    hb(token)
    assert DeviceAlert.objects.get(device=d, kind="offline").resolved_at is not None


def test_outdated_version_and_sync_failure_alerts(seeded, api):
    token, dev_id = register_device(api)
    assert hb(token, software_version="0.9.0").json()["version_status"] == "outdated"
    assert DeviceAlert.objects.filter(device_id=dev_id, kind="outdated_version", resolved_at__isnull=True).exists()
    hb(token, sync_status="error", pending_offline_count=4)
    assert DeviceAlert.objects.filter(device_id=dev_id, kind="sync_failure", resolved_at__isnull=True).exists()
    hb(token, software_version="1.1.0", sync_status="ok", pending_offline_count=0)
    assert not DeviceAlert.objects.filter(device_id=dev_id, resolved_at__isnull=True,
                                          kind__in=["outdated_version", "sync_failure"]).exists()


def test_configuration_versioning_and_local_settings(seeded, api, as_user):
    token, dev_id = register_device(api)
    first = hb(token, config_version=1).json()
    assert first["config_stale"] is False
    hq = as_user("hq.admin")
    prof = DeviceConfiguration.objects.get(name="Standard POS")
    r = hq.patch(f"/api/v1/device-profiles/{prof.id}/", {"settings": {**prof.settings, "receipt_copies": 2}},
                 format="json")
    assert r.status_code == 200 and r.json()["version"] == 2
    beat = hb(token, config_version=first["config_version"]).json()
    assert beat["config_stale"] is True and beat["config_version"] == first["config_version"] + 1
    cfg = device_client(token).get("/api/v1/device-auth/config/").json()
    assert cfg["settings"]["receipt_copies"] == 2
    device_client(token).post("/api/v1/device-auth/config/", {"config_version": cfg["config_version"]}, format="json")
    assert hb(token, config_version=cfg["config_version"]).json()["config_stale"] is False
    api.credentials()
    m = as_user("manager.1001")
    assert m.patch(f"/api/v1/devices/{dev_id}/", {"local_settings": {"receipt_copies": 3}},
                   format="json").status_code == 200
    r = m.patch(f"/api/v1/devices/{dev_id}/", {"local_settings": {"features": {"cash_drawer": False}}}, format="json")
    assert r.status_code == 400  # not locally overridable
    assert m.patch(f"/api/v1/devices/{dev_id}/", {"config_profile": prof.id}, format="json").status_code == 403
    assert m.patch(f"/api/v1/device-profiles/{prof.id}/", {"minimum_version": "9"}, format="json").status_code == 403
    assert device_client(token).get("/api/v1/device-auth/config/").json()["settings"]["receipt_copies"] == 3


def test_device_dashboard_isolation_and_reassign(seeded, api, as_user):
    t1, d1 = register_device(api)
    t2, d2 = register_device(api, username="manager.2001", store_number="2001", approver="owner.b")
    a = as_user("owner.a")
    ids = [d["id"] for d in a.get("/api/v1/devices/").json()["results"]]
    assert d1 in ids and d2 not in ids
    assert a.get(f"/api/v1/devices/{d2}/").status_code == 404
    assert a.post(f"/api/v1/devices/{d1}/reassign/", {"store": seeded["2001"].id}, format="json").status_code == 403
    r = a.post(f"/api/v1/devices/{d1}/reassign/", {"store": seeded["1002"].id}, format="json")
    assert r.status_code == 200 and r.json()["store_number"] == "1002"
    api.credentials()
    hq = as_user("hq.admin")
    summary = hq.get("/api/v1/devices/summary/").json()
    assert summary["total"] >= 2 and summary["active"] >= 2
    act = hq.get(f"/api/v1/devices/{d1}/activity/").json()
    assert {"device.register", "device.approve", "device.reassign"} <= {x["action"] for x in act["audit"]}
    filtered = hq.get(f"/api/v1/devices/?franchisee={seeded['2001'].franchisee_id}").json()["results"]
    assert [d["id"] for d in filtered] == [d2]


def test_reassign_blocked_with_open_register(seeded, api, as_user):
    token, dev_id = register_device(api)
    c = as_user("cashier.1001", store_number="1001", device_token=token)
    assert c.post("/api/v1/cash-sessions/open/", {"opening_float_cents": 0}, format="json").status_code == 201
    api.credentials()
    hq = as_user("hq.admin")
    r = hq.post(f"/api/v1/devices/{dev_id}/reassign/", {"store": seeded["2001"].id}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "register_session_open"
    d = hq.get(f"/api/v1/devices/{dev_id}/").json()
    assert d["active_register_session"]["employee"] == "cashier.1001"


def test_policy_requires_registered_device_for_pos(seeded, api):
    hq = Headquarters.objects.first()
    hq.policies = {**hq.policies, "require_registered_device_for_pos": True}
    hq.save()
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1001", "username": "cashier.1001",
                                             "password": "Test!Pass2026"}, format="json")
    assert r.status_code in (401, 403) and r.json()["error"]["code"] == "device_required"
    hq.policies["require_registered_device_for_pos"] = False
    hq.save()
    token, _ = register_device(api)
    hq.policies["require_registered_device_for_pos"] = True
    hq.save()
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1001", "username": "cashier.1001",
                                             "password": "Test!Pass2026"}, format="json", HTTP_X_DEVICE_TOKEN=token)
    assert r.status_code == 200
    # device registered to 1001 cannot be used to sign into another store
    r = api.post("/api/v1/auth/pos/login/", {"store_number": "1002", "username": "manager.1001",
                                             "password": "Test!Pass2026"}, format="json", HTTP_X_DEVICE_TOKEN=token)
    assert r.status_code in (401, 403)
