"""Device registration, authentication, heartbeat monitoring and configuration services."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.audit import audit
from apps.core.exceptions import ConflictError, DomainError
from apps.organization.models import Device, Headquarters

from .models import (
    DeviceAlert,
    DeviceConfiguration,
    DeviceConfigurationVersion,
    DeviceCredential,
    DeviceHeartbeat,
    DeviceRegistration,
    default_profile_settings,
)

logger = logging.getLogger("devices")

AUTH_FAILURE_WINDOW = 600
AUTH_FAILURE_ALERT_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Policies (Headquarters.policies)
# ---------------------------------------------------------------------------
DEFAULT_POLICIES = {
    "devices_require_approval": True,          # devices registered by store staff wait for approval
    "franchisee_can_approve_devices": True,    # franchise users may approve their own stores' devices
    "require_registered_device_for_pos": False,  # POS/KDS login only from an approved device
    "device_offline_after_seconds": 180,
    "device_credential_lifetime_days": 365,
}


def policy(key: str):
    hq = Headquarters.objects.first()
    return (hq.policies if hq else {}).get(key, DEFAULT_POLICIES.get(key))


def can_approve(user, device_or_store) -> bool:
    if not user.has_permission("devices.approve"):
        return False
    if user.is_superuser or user.is_hq:
        return True
    return bool(policy("franchisee_can_approve_devices")) and user.is_franchise_level


# ---------------------------------------------------------------------------
# Real-time
# ---------------------------------------------------------------------------
def device_groups(device: Device) -> list[str]:
    return ["devices_all", f"devices_franchisee_{device.store.franchisee_id}", f"devices_store_{device.store_id}"]


def broadcast_device(device: Device, event: str, extra: dict | None = None):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return
    payload = {"event": event, "device": {
        "id": device.id, "uid": str(device.uid), "name": device.name, "store_id": device.store_id,
        "status": device.status, "is_online": device.is_online,
        "last_heartbeat_at": device.last_heartbeat_at.isoformat() if device.last_heartbeat_at else None,
        "software_version": device.software_version}, **(extra or {})}
    for g in device_groups(device):
        async_to_sync(layer.group_send)(g, {"type": "push", "payload": payload})


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
def raise_alert(device: Device, kind: str, message: str, details: dict | None = None) -> DeviceAlert:
    alert = DeviceAlert.objects.filter(device=device, kind=kind, resolved_at__isnull=True).first()
    if alert:
        return alert
    alert = DeviceAlert.objects.create(device=device, kind=kind, message=message[:500], details=details or {},
                                       severity=DeviceAlert.SEVERITY.get(kind, "warning"))
    audit(f"device.alert.{kind}", device, entity_type="Device", actor_label="system", data={"message": message})
    transaction.on_commit(lambda: broadcast_device(device, "device.alert", {"alert": {
        "id": alert.id, "kind": kind, "severity": alert.severity, "message": alert.message}}))
    return alert


def resolve_alert(device: Device, kind: str):
    DeviceAlert.objects.filter(device=device, kind=kind, resolved_at__isnull=True).update(resolved_at=timezone.now())


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class DeviceAuthError(Exception):
    def __init__(self, message: str, code: str = "invalid_device", device: Device | None = None):
        super().__init__(message)
        self.code = code
        self.device = device


def _record_failure(device: Device | None, ip: str | None, reason: str):
    audit("device.auth_failed", device, entity_type="Device", entity_id=str(device.pk) if device else "unknown",
          actor_label=f"ip:{ip}", store=device.store if device else None, data={"reason": reason, "ip": ip})
    key = f"devauth-fail:{device.pk if device else ip}"
    n = cache.get_or_set(key, 0, AUTH_FAILURE_WINDOW)
    try:
        n = cache.incr(key)
    except ValueError:
        cache.set(key, 1, AUTH_FAILURE_WINDOW)
        n = 1
    if device is not None:
        if reason in ("revoked_credential", "deactivated", "bad_secret", "expired_credential"):
            raise_alert(device, DeviceAlert.KIND_UNAUTHORIZED, f"Rejected device credential ({reason}) from {ip}",
                        {"ip": ip, "reason": reason})
        if n >= AUTH_FAILURE_ALERT_THRESHOLD:
            raise_alert(device, DeviceAlert.KIND_AUTH_FAILURES, f"{n} failed device authentications in 10 minutes",
                        {"ip": ip, "count": n})


def authenticate_device(raw: str, ip: str | None = None, *, allow_pending: bool = False) -> tuple[Device, DeviceCredential]:
    """Resolve a device credential. Raises DeviceAuthError for unknown, revoked, expired,
    pending (unless allowed) or deactivated devices, recording the attempt."""
    parsed = DeviceCredential.parse(raw or "")
    if parsed is None and (raw or "").startswith("dev_"):
        # Pre-upgrade token format: the whole token was hashed (see devices migration 0002).
        from .models import _hash

        legacy = DeviceCredential.objects.filter(key_id__startswith="legacy", secret_hash=_hash(raw)).first()
        parsed = (legacy.key_id, None) if legacy else None
    if parsed is None:
        _record_failure(None, ip, "malformed")
        raise DeviceAuthError("Unknown device credential")
    key_id, secret = parsed
    cred = DeviceCredential.objects.select_related("device__store__franchisee").filter(key_id=key_id).first()
    if cred is None:
        _record_failure(None, ip, "unknown_key")
        raise DeviceAuthError("Unknown device credential")
    device = cred.device
    if secret is not None and not cred.matches(secret):
        _record_failure(device, ip, "bad_secret")
        raise DeviceAuthError("Invalid device credential", device=device)
    if cred.revoked_at is not None:
        _record_failure(device, ip, "revoked_credential")
        raise DeviceAuthError("Device credential has been revoked", code="device_revoked", device=device)
    if cred.expires_at and cred.expires_at <= timezone.now():
        _record_failure(device, ip, "expired_credential")
        raise DeviceAuthError("Device credential has expired; rotate or re-enroll", code="device_credential_expired",
                              device=device)
    if device.status == Device.STATUS_DEACTIVATED:
        _record_failure(device, ip, "deactivated")
        raise DeviceAuthError("Device has been deactivated", code="device_deactivated", device=device)
    if device.status == Device.STATUS_PENDING and not allow_pending:
        raise DeviceAuthError("Device is awaiting approval", code="device_pending_approval", device=device)
    if not device.store.is_active:
        raise DeviceAuthError("Store is inactive", code="store_inactive", device=device)
    now = timezone.now()
    DeviceCredential.objects.filter(pk=cred.pk).update(last_used_at=now)
    Device.objects.filter(pk=device.pk).update(last_seen_at=now, last_ip=ip)
    return device, cred


# ---------------------------------------------------------------------------
# Registration & administration
# ---------------------------------------------------------------------------
def _issue(device: Device, user=None) -> str:
    _, token = DeviceCredential.issue(device, issued_by=user, lifetime_days=policy("device_credential_lifetime_days"))
    return token


def default_profile_for(device_type: str) -> DeviceConfiguration | None:
    for p in DeviceConfiguration.objects.filter(is_default=True).order_by("id"):
        if not p.device_types or device_type in p.device_types:
            return p
    return None


@transaction.atomic
def register_by_staff(*, store, device_type: str, name: str, user, ip=None, user_agent="", parent=None,
                      kitchen_station=None) -> tuple[Device, str]:
    approved = can_approve(user, store) or not policy("devices_require_approval")
    device = Device.objects.create(
        store=store, device_type=device_type, name=name, registered_by=user, last_ip=ip, user_agent=user_agent[:300],
        status=Device.STATUS_ACTIVE if approved else Device.STATUS_PENDING, parent_device=parent,
        kitchen_station=kitchen_station, config_profile=default_profile_for(device_type),
        approved_by=user if approved else None, approved_at=timezone.now() if approved else None)
    DeviceRegistration.objects.create(
        kind=DeviceRegistration.KIND_STAFF, store=store, device=device, device_type=device_type, device_name=name,
        requested_by=user, request_ip=ip,
        status=DeviceRegistration.STATUS_APPROVED if approved else DeviceRegistration.STATUS_PENDING,
        decided_by=user if approved else None, decided_at=timezone.now() if approved else None)
    token = _issue(device, user)
    audit("device.register", device, entity_type="Device", actor=user,
          data={"type": device_type, "status": device.status, "method": "staff"})
    transaction.on_commit(lambda: broadcast_device(device, "device.registered"))
    return device, token


def create_enrollment(*, store, device_type: str, name: str, user, hours: int = 24) -> tuple[DeviceRegistration, str]:
    code = DeviceRegistration.new_code()
    reg = DeviceRegistration.objects.create(
        kind=DeviceRegistration.KIND_ENROLLMENT, store=store, device_type=device_type, device_name=name,
        code_hash=DeviceRegistration.hash_code(code), expires_at=timezone.now() + timedelta(hours=hours),
        requested_by=user, status=DeviceRegistration.STATUS_APPROVED, decided_by=user, decided_at=timezone.now())
    audit("device.enrollment_code_issued", reg, entity_type="DeviceRegistration", actor=user, store=store,
          data={"device_type": device_type, "name": name, "expires_at": reg.expires_at.isoformat()})
    return reg, f"{code[:5]}-{code[5:]}"


@transaction.atomic
def enroll(*, store_number: str, code: str, ip=None, user_agent="", software_version="") -> tuple[Device, str]:
    reg = DeviceRegistration.objects.select_for_update(of=("self",)).select_related("store").filter(
        kind=DeviceRegistration.KIND_ENROLLMENT, code_hash=DeviceRegistration.hash_code(code),
        store__number=store_number).first()
    if reg is None or reg.status != DeviceRegistration.STATUS_APPROVED:
        audit("device.enrollment_failed", entity_type="DeviceRegistration", entity_id="unknown",
              actor_label=f"ip:{ip}", data={"store_number": store_number})
        raise DomainError("Invalid or already used enrollment code", code="invalid_enrollment_code", status_code=403)
    if reg.expires_at and reg.expires_at <= timezone.now():
        reg.status = DeviceRegistration.STATUS_EXPIRED
        reg.save(update_fields=["status", "updated_at"])
        raise DomainError("Enrollment code has expired", code="enrollment_code_expired", status_code=403)
    if reg.device_id:  # re-enrollment of an existing device after credential revocation
        device = reg.device
        device.status = Device.STATUS_ACTIVE
        device.save(update_fields=["status", "updated_at"])
    else:
        device = Device.objects.create(
            store=reg.store, device_type=reg.device_type, name=reg.device_name, registered_by=reg.requested_by,
            status=Device.STATUS_ACTIVE, approved_by=reg.decided_by, approved_at=timezone.now(), last_ip=ip,
            user_agent=user_agent[:300], software_version=software_version[:40],
            config_profile=default_profile_for(reg.device_type))
        reg.device = device
    reg.status = DeviceRegistration.STATUS_CONSUMED
    reg.request_ip = ip
    reg.save()
    token = _issue(device, reg.requested_by)
    audit("device.enrolled", device, entity_type="Device", actor_label=f"device:{device.uid}",
          data={"registration": reg.pk, "ip": ip})
    transaction.on_commit(lambda: broadcast_device(device, "device.registered"))
    return device, token


@transaction.atomic
def decide(device: Device, user, approve: bool, note: str = "") -> Device:
    device = Device.objects.select_for_update().get(pk=device.pk)
    if device.status != Device.STATUS_PENDING:
        raise ConflictError("Device is not awaiting approval", code="not_pending")
    if approve:
        device.status, device.approved_by, device.approved_at = Device.STATUS_ACTIVE, user, timezone.now()
    else:
        device.status, device.deactivated_at, device.deactivation_reason = (
            Device.STATUS_DEACTIVATED, timezone.now(), note or "Registration rejected")
        device.credentials.filter(revoked_at__isnull=True).update(revoked_at=timezone.now(),
                                                                  revoked_reason="registration rejected")
    device.save()
    device.registrations.filter(status=DeviceRegistration.STATUS_PENDING).update(
        status=DeviceRegistration.STATUS_APPROVED if approve else DeviceRegistration.STATUS_REJECTED,
        decided_by=user, decided_at=timezone.now(), note=note[:300])
    audit("device.approve" if approve else "device.reject", device, entity_type="Device", actor=user,
          data={"note": note})
    transaction.on_commit(lambda: broadcast_device(device, "device.updated"))
    return device


def revoke_credentials(device: Device, user, reason: str) -> int:
    n = device.credentials.filter(revoked_at__isnull=True).update(revoked_at=timezone.now(), revoked_reason=reason[:200])
    audit("device.credentials_revoked", device, entity_type="Device", actor=user, data={"reason": reason, "count": n})
    return n


@transaction.atomic
def deactivate(device: Device, user, reason: str) -> Device:
    from apps.payments.models import CashSession

    device = Device.objects.select_for_update().get(pk=device.pk)
    device.status = Device.STATUS_DEACTIVATED
    device.deactivated_at = timezone.now()
    device.deactivation_reason = reason[:200]
    device.is_online = False
    device.save()
    revoke_credentials(device, user, f"device deactivated: {reason}")
    open_sessions = CashSession.objects.filter(register__device=device, status=CashSession.STATUS_OPEN).count()
    audit("device.deactivate", device, entity_type="Device", actor=user,
          data={"reason": reason, "open_register_sessions": open_sessions})
    transaction.on_commit(lambda: broadcast_device(device, "device.updated"))
    return device


@transaction.atomic
def reactivate(device: Device, user) -> tuple[DeviceRegistration, str]:
    """A deactivated/revoked device returns only through a fresh one-time enrollment code."""
    reg, code = create_enrollment(store=device.store, device_type=device.device_type, name=device.name, user=user)
    reg.device = device
    reg.save(update_fields=["device"])
    audit("device.reactivation_code_issued", device, entity_type="Device", actor=user)
    return reg, code


def rotate_credential(device: Device, current: DeviceCredential) -> str:
    """Device-initiated rotation: issue a new credential and revoke the one used to request it."""
    with transaction.atomic():
        cred, token = DeviceCredential.issue(device, lifetime_days=policy("device_credential_lifetime_days"))
        DeviceCredential.objects.filter(pk=current.pk).update(revoked_at=timezone.now(), revoked_reason="rotated",
                                                              replaced_by=cred)
    audit("device.credential_rotated", device, entity_type="Device", actor_label=f"device:{device.uid}")
    return token


def bump_config(devices_qs):
    devices_qs.update(config_version=F("config_version") + 1)


@transaction.atomic
def reassign(device: Device, store, user) -> Device:
    from apps.payments.models import CashSession

    if CashSession.objects.filter(register__device=device, status=CashSession.STATUS_OPEN).exists():
        raise ConflictError("Close the open register session on this device before reassigning it",
                            code="register_session_open")
    old = device.store
    device.store = store
    device.kitchen_station = None
    device.config_version += 1
    device.save()
    from apps.payments.models import Register

    Register.objects.filter(device=device).update(store=store)
    audit("device.reassign", device, entity_type="Device", actor=user,
          data={"from_store": old.number, "to_store": store.number})
    transaction.on_commit(lambda: broadcast_device(device, "device.updated"))
    return device


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def effective_config(device: Device) -> dict:
    profile = device.config_profile
    settings = _merge(default_profile_settings(), profile.settings if profile else {})
    overridable = set(profile.local_overridable if profile else [])
    local = {k: v for k, v in (device.local_settings or {}).items() if k in overridable}
    settings = _merge(settings, local)
    return {
        "device": {"id": device.id, "uid": str(device.uid), "name": device.name, "type": device.device_type,
                   "store_id": device.store_id, "store_number": device.store.number,
                   "kitchen_station": device.kitchen_station.code if device.kitchen_station else None},
        "config_version": device.config_version,
        "profile": {"id": profile.id, "name": profile.name, "version": profile.version} if profile else None,
        "settings": settings,
        "local_overridable": sorted(overridable),
        "approved_versions": profile.approved_versions if profile else [],
        "minimum_version": profile.minimum_version if profile else "",
        "update_policy": profile.update_policy if profile else DeviceConfiguration.UPDATE_AUTO,
    }


def feature_enabled(device: Device | None, feature: str) -> bool:
    """Server-side enforcement of device feature permissions (no device → not restricted)."""
    if device is None:
        return True
    return bool(effective_config(device)["settings"].get("features", {}).get(feature, True))


def _vtuple(v: str):
    out = []
    for part in (v or "0").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def version_status(device: Device) -> str:
    p = device.config_profile
    if not p or not device.software_version:
        return "unknown"
    if p.minimum_version and _vtuple(device.software_version) < _vtuple(p.minimum_version):
        return "outdated"
    if p.approved_versions and device.software_version not in p.approved_versions:
        return "unapproved"
    return "ok"


@transaction.atomic
def save_profile(profile: DeviceConfiguration, user, *, is_new: bool) -> DeviceConfiguration:
    if not is_new:
        profile.version += 1
    profile.updated_by = user
    profile.save()
    DeviceConfigurationVersion.objects.create(profile=profile, version=profile.version, created_by=user, snapshot={
        "settings": profile.settings, "local_overridable": profile.local_overridable,
        "approved_versions": profile.approved_versions, "minimum_version": profile.minimum_version,
        "update_policy": profile.update_policy, "device_types": profile.device_types})
    bump_config(Device.objects.filter(config_profile=profile))
    audit("device_profile.save", profile, entity_type="DeviceConfiguration", actor=user,
          data={"version": profile.version})
    for d in Device.objects.filter(config_profile=profile).select_related("store"):
        transaction.on_commit(lambda d=d: broadcast_device(d, "device.config_changed"))
    return profile


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------
@transaction.atomic
def record_heartbeat(device: Device, data: dict, ip: str | None) -> dict:
    now = timezone.now()
    d = Device.objects.select_for_update(of=("self",)).select_related("config_profile", "store").get(pk=device.pk)
    was_online = d.is_online
    d.last_heartbeat_at = now
    d.is_online = True
    d.last_ip = ip
    d.software_version = str(data.get("software_version") or d.software_version)[:40]
    d.os_info = str(data.get("os_info") or d.os_info)[:200]
    d.app_status = str(data.get("app_status") or "")[:40]
    d.sync_status = str(data.get("sync_status") or "")[:40]
    pending = max(0, int(data.get("pending_offline_count") or 0))
    d.pending_offline_count = pending
    if pending and not d.sync_pending_since:
        d.sync_pending_since = now
    elif not pending:
        d.sync_pending_since = None
    reported = int(data.get("config_version") or 0)
    if reported and reported <= d.config_version:
        d.applied_config_version = reported
    if "signed_in_username" in data:
        from apps.accounts.models import User

        uname = data.get("signed_in_username")
        u = User.objects.filter(username=uname).first() if uname else None
        if (u.pk if u else None) != d.current_user_id:
            d.current_user, d.current_user_since = u, now if u else None
    d.save()
    DeviceHeartbeat.objects.create(device=d, ip=ip, software_version=d.software_version, os_info=d.os_info,
                                   app_status=d.app_status, sync_status=d.sync_status, pending_offline_count=pending,
                                   config_version=reported, payload={k: v for k, v in data.items()
                                                                     if k in ("peripherals", "app_status", "notes")})
    # Peripherals (e.g. receipt printers attached to this POS) are reported by their host device.
    for p in data.get("peripherals") or []:
        Device.objects.filter(uid=p.get("uid"), parent_device=d, status=Device.STATUS_ACTIVE).update(
            last_heartbeat_at=now, is_online=p.get("status") == "ok", app_status=str(p.get("status", ""))[:40])
    # Alerts
    if not was_online:
        resolve_alert(d, DeviceAlert.KIND_OFFLINE)
        transaction.on_commit(lambda: broadcast_device(d, "device.online"))
    vs = version_status(d)
    if vs in ("outdated", "unapproved"):
        raise_alert(d, DeviceAlert.KIND_OUTDATED, f"Software {d.software_version} is {vs}",
                    {"version": d.software_version})
    elif vs == "ok":
        resolve_alert(d, DeviceAlert.KIND_OUTDATED)
    sync_failed = d.sync_status == "error" or (d.sync_pending_since and now - d.sync_pending_since > timedelta(minutes=15))
    if sync_failed:
        raise_alert(d, DeviceAlert.KIND_SYNC, f"Synchronization failing ({pending} pending offline transactions)",
                    {"pending": pending, "sync_status": d.sync_status})
    else:
        resolve_alert(d, DeviceAlert.KIND_SYNC)
    return {
        "server_time": now.isoformat(),
        "status": d.status,
        "config_version": d.config_version,
        "config_stale": d.applied_config_version < d.config_version,
        "version_status": vs,
        "heartbeat_interval_seconds": effective_config(d)["settings"].get("heartbeat_interval_seconds", 60),
    }


def detect_offline_devices() -> int:
    """Server-side heartbeat timeout detection (Celery beat)."""
    threshold = timezone.now() - timedelta(seconds=int(policy("device_offline_after_seconds") or 180))
    n = 0
    for d in Device.objects.select_related("store").filter(status=Device.STATUS_ACTIVE, is_online=True).exclude(
            device_type=Device.TYPE_PRINTER).filter(last_heartbeat_at__lt=threshold):
        Device.objects.filter(pk=d.pk).update(is_online=False)
        d.is_online = False
        raise_alert(d, DeviceAlert.KIND_OFFLINE, f"No heartbeat since {d.last_heartbeat_at:%Y-%m-%d %H:%M:%S} UTC",
                    {"last_heartbeat_at": d.last_heartbeat_at.isoformat()})
        broadcast_device(d, "device.offline")
        n += 1
    return n


def prune_heartbeats(days: int = 7) -> int:
    return DeviceHeartbeat.objects.filter(received_at__lt=timezone.now() - timedelta(days=days)).delete()[0]
