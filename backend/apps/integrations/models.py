from __future__ import annotations

import secrets

from django.db import models

from apps.core.encryption import EncryptedJSONField
from apps.core.models import TimeStampedModel


class IntegrationProvider(TimeStampedModel):
    """A third-party ordering provider. HQ controls whether it is permitted network-wide."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    network_enabled = models.BooleanField(default=False, help_text="HQ permits franchisees to use this provider")
    api_available = models.BooleanField(default=True, help_text="A documented order-ingestion API is implemented")
    access_requirements = models.TextField(blank=True)
    documentation_url = models.URLField(blank=True)
    ack_deadline_seconds = models.PositiveIntegerField(default=180)
    capabilities = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class ProviderAccount(TimeStampedModel):
    """Developer/app credentials issued by a provider (owned by HQ or one franchisee).

    Example: an Uber Eats app (client_id/client_secret) or a DoorDash developer
    credential (developer_id/key_id/signing_secret). Webhooks for the account are
    received on /api/v1/integrations/<provider>/webhooks/<webhook_key>/.
    """

    ENV_SANDBOX = "sandbox"
    ENV_PRODUCTION = "production"

    provider = models.ForeignKey(IntegrationProvider, on_delete=models.PROTECT, related_name="accounts")
    franchisee = models.ForeignKey("organization.Franchisee", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="provider_accounts", help_text="Null = HQ-owned account")
    name = models.CharField(max_length=100)
    environment = models.CharField(max_length=20, default=ENV_SANDBOX,
                                   choices=[(ENV_SANDBOX, "Sandbox"), (ENV_PRODUCTION, "Production")])
    credentials = EncryptedJSONField(blank=True, default=dict)
    webhook_key = models.CharField(max_length=40, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    token_cache = EncryptedJSONField(blank=True, default=dict, help_text="Cached OAuth access token")

    def save(self, *args, **kwargs):
        if not self.webhook_key:
            self.webhook_key = secrets.token_urlsafe(18)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.provider.code}:{self.name}"


class ConnectionStatus(models.TextChoices):
    NOT_CONFIGURED = "not_configured", "Not configured"
    PENDING_ACCESS = "pending_access", "Awaiting provider access"
    CONNECTED = "connected", "Connected"
    ERROR = "error", "Error"
    DISABLED = "disabled", "Disabled"


def default_integration_settings():
    return {
        "acceptance_policy": {
            "auto_accept": True,
            "reject_unavailable_items": True,
            "reject_when_closed": True,
            "reject_unmapped_items": False,
            "price_tolerance_cents": 50,
        },
        "on_review_timeout": "reject",
        "default_prep_minutes": 20,
        "send_ready_signal": True,
    }


class StoreIntegration(TimeStampedModel):
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="integrations")
    provider = models.ForeignKey(IntegrationProvider, on_delete=models.PROTECT, related_name="store_integrations")
    account = models.ForeignKey(ProviderAccount, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="store_integrations")
    enabled = models.BooleanField(default=False)
    external_store_id = models.CharField(max_length=100, blank=True,
                                         help_text="Provider's store / merchant identifier")
    credentials = EncryptedJSONField(blank=True, default=dict, help_text="Store-level secrets if required")
    settings = models.JSONField(default=default_integration_settings, blank=True)
    connection_status = models.CharField(max_length=20, choices=ConnectionStatus.choices,
                                         default=ConnectionStatus.NOT_CONFIGURED)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    menu_sync_status = models.CharField(max_length=20, default="never")
    menu_synced_at = models.DateTimeField(null=True, blank=True)
    external_menu_id = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = [("store", "provider")]
        indexes = [models.Index(fields=["provider", "external_store_id"])]

    def __str__(self):
        return f"{self.store.number}:{self.provider.code}"


class WebhookEvent(models.Model):
    STATUS_RECEIVED = "received"
    STATUS_PROCESSING = "processing"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_IGNORED = "ignored"
    STATUSES = [(s, s) for s in (STATUS_RECEIVED, STATUS_PROCESSING, STATUS_PROCESSED, STATUS_FAILED, STATUS_IGNORED)]

    provider = models.ForeignKey(IntegrationProvider, on_delete=models.PROTECT, related_name="webhook_events")
    account = models.ForeignKey(ProviderAccount, null=True, blank=True, on_delete=models.SET_NULL)
    event_id = models.CharField(max_length=200)
    event_type = models.CharField(max_length=100, db_index=True)
    external_order_id = models.CharField(max_length=100, blank=True, db_index=True)
    store_integration = models.ForeignKey(StoreIntegration, null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="webhook_events")
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="webhook_events")
    headers = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_RECEIVED, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    delivery_count = models.PositiveIntegerField(default=1, help_text="Times the provider delivered this event")
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="uniq_provider_event")]
        ordering = ["-received_at"]


class IntegrationLog(models.Model):
    LEVELS = [("info", "info"), ("warning", "warning"), ("error", "error")]
    provider = models.ForeignKey(IntegrationProvider, on_delete=models.CASCADE, related_name="logs")
    store_integration = models.ForeignKey(StoreIntegration, null=True, blank=True, on_delete=models.CASCADE,
                                          related_name="logs")
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    level = models.CharField(max_length=10, choices=LEVELS, default="info", db_index=True)
    direction = models.CharField(max_length=10, choices=[("inbound", "inbound"), ("outbound", "outbound")])
    operation = models.CharField(max_length=60)
    message = models.TextField()
    http_method = models.CharField(max_length=10, blank=True)
    url = models.CharField(max_length=500, blank=True)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    response_excerpt = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class SyncRecord(TimeStampedModel):
    KIND_MENU = "menu"
    KIND_ORDER_ACK = "order_ack"
    KIND_ORDER_STATUS = "order_status"
    KIND_AVAILABILITY = "availability"

    store_integration = models.ForeignKey(StoreIntegration, on_delete=models.CASCADE, related_name="sync_records")
    kind = models.CharField(max_length=20, db_index=True)
    status = models.CharField(max_length=20, default="pending",
                              help_text="pending | succeeded | failed | unsupported | awaiting_callback")
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reference = models.CharField(max_length=200, blank=True)
    detail = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ProductMapping(TimeStampedModel):
    """Optional explicit mapping of provider ids to catalogue entities.

    Menus pushed by this platform use our own codes as provider merchant ids, so
    mapping is usually implicit. This table handles menus created outside the
    platform (e.g. provider-side menu edits)."""

    provider = models.ForeignKey(IntegrationProvider, on_delete=models.CASCADE)
    store = models.ForeignKey("organization.Store", null=True, blank=True, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=150)
    target = models.CharField(max_length=60, help_text="Internal code in our id scheme, e.g. 'PZ-PEP' or 'MOD:TOP-MUSH'")

    class Meta:
        unique_together = [("provider", "store", "external_id")]
