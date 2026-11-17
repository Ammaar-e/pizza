from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class OrderSource(models.TextChoices):
    POS = "pos", "POS"
    UBER_EATS = "ubereats", "Uber Eats"
    DOORDASH = "doordash", "DoorDash"
    SKIP = "skip", "SkipTheDishes"
    CALL_CENTRE = "call_centre", "Call centre"
    EXTERNAL_API = "external_api", "External API / website"
    ANDROID = "android", "Android app"
    IOS = "ios", "iOS app"
    OTHER = "other", "Other integration"


THIRD_PARTY_SOURCES = {OrderSource.UBER_EATS, OrderSource.DOORDASH, OrderSource.SKIP}


class OrderType(models.TextChoices):
    PICKUP = "pickup", "Pickup"
    DELIVERY = "delivery", "Store delivery"
    DINE_IN = "dine_in", "Dine in"
    PROVIDER_DELIVERY = "provider_delivery", "Delivered by provider courier"


class OrderStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PENDING_REVIEW = "pending_review", "Needs staff review"
    ACCEPTED = "accepted", "Accepted"
    PREPARING = "preparing", "Preparing"
    READY = "ready", "Ready"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class PaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PARTIALLY_PAID = "partially_paid", "Partially paid"
    PAID = "paid", "Paid"
    PROVIDER_PAID = "provider_paid", "Paid via provider"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"


class ProviderSyncStatus(models.TextChoices):
    NOT_APPLICABLE = "n/a", "Not applicable"
    PENDING = "pending", "Pending"
    SYNCED = "synced", "Synced"
    FAILED = "failed", "Failed"


class Customer(TimeStampedModel):
    """Customers are scoped to a franchisee (never shared across franchisees)."""

    franchisee = models.ForeignKey("organization.Franchisee", on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=40, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    addresses = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["franchisee", "phone"])]


class Order(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.PositiveIntegerField(help_text="Per-store daily sequence shown to staff/customers")
    business_date = models.DateField(db_index=True)
    franchisee = models.ForeignKey("organization.Franchisee", on_delete=models.PROTECT, related_name="orders")
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="orders")
    source = models.CharField(max_length=20, choices=OrderSource.choices, db_index=True)
    api_client = models.ForeignKey("api.ApiClient", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="orders")
    external_order_id = models.CharField(max_length=100, null=True, blank=True)
    external_display_id = models.CharField(max_length=50, blank=True)
    store_integration = models.ForeignKey("integrations.StoreIntegration", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="orders")
    idempotency_key = models.CharField(max_length=100, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True)
    order_type = models.CharField(max_length=20, choices=OrderType.choices)
    entry_channel = models.CharField(max_length=20, blank=True,
                                     help_text="How a POS order was taken: walk_in, phone, manual_provider")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.RECEIVED, db_index=True)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    provider_sync_status = models.CharField(max_length=10, choices=ProviderSyncStatus.choices,
                                            default=ProviderSyncStatus.NOT_APPLICABLE)
    provider_sync_error = models.TextField(blank=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    customer_snapshot = models.JSONField(default=dict, blank=True)
    delivery_address = models.JSONField(default=dict, blank=True)
    special_instructions = models.TextField(blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="CAD")
    subtotal_cents = models.IntegerField(default=0)
    discount_cents = models.IntegerField(default=0)
    charges_cents = models.IntegerField(default=0)
    tax_cents = models.IntegerField(default=0)
    tip_cents = models.IntegerField(default=0)
    total_cents = models.IntegerField(default=0)
    tax_breakdown = models.JSONField(default=dict, blank=True)
    charges = models.JSONField(default=list, blank=True)
    provider_totals = models.JSONField(default=dict, blank=True,
                                       help_text="Totals as reported by the external provider")
    validation_issues = models.JSONField(default=list, blank=True)
    requires_attention = models.BooleanField(default=False, db_index=True)
    attention_reason = models.CharField(max_length=300, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    cancellation_reason = models.CharField(max_length=300, blank=True)
    is_offline_origin = models.BooleanField(default=False)
    offline_client_ref = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    device = models.ForeignKey("organization.Device", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    placed_at = models.DateTimeField(db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    provider_ack_deadline = models.DateTimeField(null=True, blank=True)
    estimated_ready_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True, help_text="Original provider/client payload")

    class Meta:
        ordering = ["-placed_at"]
        constraints = [
            # Duplicate prevention: one order per provider order id per source.
            models.UniqueConstraint(fields=["source", "external_order_id"],
                                    condition=Q(external_order_id__isnull=False), name="uniq_order_source_external_id"),
            models.UniqueConstraint(fields=["store", "source", "idempotency_key"],
                                    condition=Q(idempotency_key__isnull=False), name="uniq_order_idempotency_key"),
            models.UniqueConstraint(fields=["store", "business_date", "order_number"], name="uniq_order_number_per_day"),
        ]
        indexes = [
            models.Index(fields=["store", "status", "placed_at"]),
            models.Index(fields=["store", "business_date"]),
            models.Index(fields=["franchisee", "placed_at"]),
            models.Index(fields=["external_order_id"]),
        ]

    def __str__(self):
        return f"#{self.order_number} {self.store.number} ({self.source})"

    @property
    def is_third_party(self) -> bool:
        return self.source in THIRD_PARTY_SOURCES

    @property
    def amount_paid_cents(self) -> int:
        from apps.payments.models import Payment

        return sum(p.net_amount_cents for p in self.payments.filter(status__in=Payment.SETTLED_STATUSES))


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField(default=0)
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.SET_NULL)
    sku = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=200)
    size_code = models.CharField(max_length=20, blank=True)
    size_name = models.CharField(max_length=50, blank=True)
    crust_code = models.CharField(max_length=30, blank=True)
    crust_name = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_cents = models.IntegerField(default=0)
    line_total_cents = models.IntegerField(default=0)
    tax_cents = models.IntegerField(default=0)
    removed = models.JSONField(default=list, blank=True)
    special_instructions = models.TextField(blank=True)
    prep_category = models.CharField(max_length=30, blank=True)
    external_item_id = models.CharField(max_length=100, blank=True)
    is_mapped = models.BooleanField(default=True)

    class Meta:
        ordering = ["position"]


class OrderItemModifier(models.Model):
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="modifiers")
    modifier = models.ForeignKey("catalog.Modifier", null=True, blank=True, on_delete=models.SET_NULL)
    code = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=100)
    group_code = models.CharField(max_length=40, blank=True)
    placement = models.CharField(max_length=10, default="whole")
    quantity = models.PositiveIntegerField(default=1)
    price_cents = models.IntegerField(default=0)
    is_default = models.BooleanField(default=False)
    external_id = models.CharField(max_length=100, blank=True)


class OrderDiscount(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="discounts")
    discount = models.ForeignKey("catalog.Discount", null=True, blank=True, on_delete=models.SET_NULL)
    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=100)
    amount_cents = models.IntegerField()
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class OrderEvent(models.Model):
    """Append-only event log for every order (state changes, sync results, errors)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=60, db_index=True)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    actor_label = models.CharField(max_length=200, blank=True)
    message = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
