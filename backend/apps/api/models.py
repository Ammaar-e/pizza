from __future__ import annotations

import hashlib
import secrets

from django.db import models

from apps.core.encryption import EncryptedJSONField
from apps.core.models import TimeStampedModel

SCOPES = {
    "stores:read": "Read stores, hours and availability",
    "menu:read": "Read menus, products and customization options",
    "orders:write": "Submit orders",
    "orders:read": "Read order details and status",
    "orders:cancel": "Cancel orders the client submitted",
}


class ApiClient(TimeStampedModel):
    """An external application (call centre, website back-end, mobile app back-end)."""

    KIND_CALL_CENTRE = "call_centre"
    KIND_EXTERNAL = "external_api"
    KIND_ANDROID = "android"
    KIND_IOS = "ios"
    KIND_OTHER = "other"
    KINDS = [(KIND_CALL_CENTRE, "Call centre"), (KIND_EXTERNAL, "Website / external API"),
             (KIND_ANDROID, "Android app"), (KIND_IOS, "iOS app"), (KIND_OTHER, "Other integration")]

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KINDS)
    franchisee = models.ForeignKey("organization.Franchisee", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="api_clients", help_text="Null = network-wide (HQ issued)")
    stores = models.ManyToManyField("organization.Store", blank=True, related_name="api_clients",
                                    help_text="Restrict to these stores (empty = all stores in scope)")
    scopes = models.JSONField(default=list)
    key_prefix = models.CharField(max_length=16, unique=True)
    key_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    rate_limit_per_minute = models.PositiveIntegerField(null=True, blank=True)
    webhook_url = models.URLField(blank=True)
    webhook_secret = EncryptedJSONField(blank=True, default=dict)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")

    @property
    def is_authenticated(self):  # used by throttles
        return True

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    @classmethod
    def issue_key(cls) -> tuple[str, str, str]:
        prefix = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32)
        return f"{prefix}.{secret}", prefix, cls.hash_secret(secret)

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def accessible_store_ids(self) -> set[int]:
        from apps.organization.models import Store

        qs = Store.objects.filter(is_active=True)
        if self.franchisee_id:
            qs = qs.filter(franchisee_id=self.franchisee_id)
        restricted = set(self.stores.values_list("id", flat=True))
        ids = set(qs.values_list("id", flat=True))
        return ids & restricted if restricted else ids

    def can_access_store(self, store) -> bool:
        return store.id in self.accessible_store_ids()

    @property
    def order_source(self) -> str:
        return {self.KIND_CALL_CENTRE: "call_centre", self.KIND_EXTERNAL: "external_api", self.KIND_ANDROID: "android",
                self.KIND_IOS: "ios"}.get(self.kind, "other")


class OutboundWebhook(TimeStampedModel):
    """Order status notifications sent to API clients (e.g. the call-centre system)."""

    client = models.ForeignKey(ApiClient, on_delete=models.CASCADE, related_name="deliveries")
    order = models.ForeignKey("orders.Order", null=True, on_delete=models.SET_NULL, related_name="+")
    event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=50)
    payload = models.JSONField()
    status = models.CharField(max_length=20, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_status_code = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
