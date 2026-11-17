"""Centralized device management: credentials, registration/approval, heartbeats, alerts,
configuration profiles and their version history.

The ``Device`` itself lives in ``organization`` (it is referenced across the platform). Device
administration history is recorded in the platform-wide immutable ``core.AuditLog`` with
``entity_type="Device"`` (no separate audit table), while heartbeats and alerts live here.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings as dj_settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class DeviceCredential(TimeStampedModel):
    """A device's own authentication secret. Every device has distinct credentials; they can be
    rotated (new credential, old one revoked) or revoked outright. Only a SHA-256 hash is stored."""

    TOKEN_PREFIX = "dvc_"

    device = models.ForeignKey("organization.Device", on_delete=models.CASCADE, related_name="credentials")
    key_id = models.CharField(max_length=16, unique=True)
    secret_hash = models.CharField(max_length=64)
    issued_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=200, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["device", "revoked_at"])]

    @classmethod
    def issue(cls, device, issued_by=None, lifetime_days: int | None = None) -> tuple["DeviceCredential", str]:
        key_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        cred = cls.objects.create(
            device=device, key_id=key_id, secret_hash=_hash(secret), issued_by=issued_by,
            expires_at=timezone.now() + timedelta(days=lifetime_days) if lifetime_days else None)
        return cred, f"{cls.TOKEN_PREFIX}{key_id}.{secret}"

    @staticmethod
    def parse(token: str) -> tuple[str, str] | None:
        if not token.startswith(DeviceCredential.TOKEN_PREFIX):
            return None
        key_id, _, secret = token[len(DeviceCredential.TOKEN_PREFIX):].partition(".")
        return (key_id, secret) if key_id and secret else None

    def matches(self, secret: str) -> bool:
        return hmac.compare_digest(self.secret_hash, _hash(secret))

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > timezone.now())


class DeviceRegistration(TimeStampedModel):
    """A registration request or a pre-issued one-time enrollment code."""

    KIND_STAFF = "staff"            # registered by a signed-in employee on the device
    KIND_ENROLLMENT = "enrollment"  # HQ/franchise issued a one-time code; the device claims it
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CONSUMED = "consumed"
    STATUS_EXPIRED = "expired"

    kind = models.CharField(max_length=20, choices=[(KIND_STAFF, "Staff registration"),
                                                    (KIND_ENROLLMENT, "Enrollment code")])
    status = models.CharField(max_length=20, default=STATUS_PENDING, db_index=True)
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="device_registrations")
    device = models.ForeignKey("organization.Device", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="registrations")
    device_type = models.CharField(max_length=30)
    device_name = models.CharField(max_length=100)
    code_hash = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    decided_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def new_code() -> str:
        # 10 characters from an unambiguous alphabet; single use; short-lived.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(10))

    @staticmethod
    def hash_code(code: str) -> str:
        return _hash(code.strip().upper().replace("-", ""))


class DeviceHeartbeat(models.Model):
    """Raw heartbeat log (pruned by a scheduled task). Online status is derived server-side from
    ``Device.last_heartbeat_at`` — the device's own claims are recorded, not trusted."""

    device = models.ForeignKey("organization.Device", on_delete=models.CASCADE, related_name="heartbeats")
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    software_version = models.CharField(max_length=40, blank=True)
    os_info = models.CharField(max_length=200, blank=True)
    app_status = models.CharField(max_length=40, blank=True)
    sync_status = models.CharField(max_length=40, blank=True)
    pending_offline_count = models.PositiveIntegerField(default=0)
    config_version = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["device", "received_at"])]


class DeviceAlert(TimeStampedModel):
    KIND_OFFLINE = "offline"
    KIND_SYNC = "sync_failure"
    KIND_OUTDATED = "outdated_version"
    KIND_UNAUTHORIZED = "unauthorized_access"
    KIND_AUTH_FAILURES = "auth_failures"
    KINDS = [(KIND_OFFLINE, "Device offline"), (KIND_SYNC, "Synchronization failing"),
             (KIND_OUTDATED, "Outdated / unapproved software"), (KIND_UNAUTHORIZED, "Unauthorized access attempt"),
             (KIND_AUTH_FAILURES, "Repeated authentication failures")]
    SEVERITY = {KIND_OFFLINE: "warning", KIND_SYNC: "warning", KIND_OUTDATED: "info", KIND_UNAUTHORIZED: "critical",
                KIND_AUTH_FAILURES: "critical"}

    device = models.ForeignKey("organization.Device", on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=30, choices=KINDS, db_index=True)
    severity = models.CharField(max_length=10, default="warning")
    message = models.CharField(max_length=500)
    details = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    acknowledged_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["device", "kind"], condition=models.Q(resolved_at__isnull=True),
                                               name="one_open_alert_per_kind")]


def default_profile_settings():
    return {
        "features": {"cash_drawer": True, "refunds": True, "discounts": True, "coupon_entry": True,
                     "offline_queue": True, "dispatch": True, "price_override": False},
        "heartbeat_interval_seconds": 60,
        "receipt_copies": 1,
        "idle_lock_minutes": 10,
    }


class DeviceConfiguration(TimeStampedModel):
    """HQ-controlled configuration profile assigned to devices."""

    UPDATE_AUTO = "auto"
    UPDATE_MANUAL = "manual"
    UPDATE_PINNED = "pinned"

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=300, blank=True)
    device_types = models.JSONField(default=list, blank=True, help_text="Applicable device types; empty = all")
    settings = models.JSONField(default=default_profile_settings)
    local_overridable = models.JSONField(default=list, blank=True,
                                         help_text="Setting keys store managers may override locally")
    approved_versions = models.JSONField(default=list, blank=True, help_text="Approved application versions")
    minimum_version = models.CharField(max_length=40, blank=True)
    update_policy = models.CharField(max_length=10, default=UPDATE_AUTO,
                                     choices=[(UPDATE_AUTO, "Auto-update"), (UPDATE_MANUAL, "Manual"),
                                              (UPDATE_PINNED, "Pinned to approved versions")])
    is_default = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    def __str__(self):
        return self.name


class DeviceConfigurationVersion(models.Model):
    profile = models.ForeignKey(DeviceConfiguration, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("profile", "version")]
        ordering = ["-version"]
