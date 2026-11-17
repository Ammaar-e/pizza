"""Internal delivery dispatch for the restaurant's own drivers.

Marketplace orders (Uber Eats, DoorDash, Skip — order_type ``provider_delivery``) are delivered by
the marketplace and never enter this module.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class DriverStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    UNAVAILABLE = "unavailable", "Unavailable"
    ON_BREAK = "on_break", "On break"
    OFF_SHIFT = "off_shift", "Off shift"


class DriverProfile(TimeStampedModel):
    """Delivery driver. Authorized stores come from the user's store assignments."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="driver_profile")
    phone = models.CharField(max_length=40, blank=True)
    vehicle = models.CharField(max_length=100, blank=True)
    license_plate = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    max_active_deliveries = models.PositiveIntegerField(default=3)
    status = models.CharField(max_length=20, choices=DriverStatus.choices, default=DriverStatus.OFF_SHIFT,
                              db_index=True)
    current_store = models.ForeignKey("organization.Store", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+", help_text="Store the driver is currently working from")
    status_changed_at = models.DateTimeField(null=True, blank=True)
    last_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.user.username

    def store_ids(self) -> set[int]:
        return set(self.user.store_assignments.filter(active=True).values_list("store_id", flat=True))


class DriverAvailability(models.Model):
    """Append-only availability history."""

    driver = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name="availability_log")
    store = models.ForeignKey("organization.Store", null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=20, choices=DriverStatus.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class DeliveryZone(TimeStampedModel):
    """Store delivery zone. Matched by distance band (needs coordinates) and/or postal prefixes.
    Without zones, the store's ``delivery_config`` radius/fee/minimum apply."""

    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="delivery_zones")
    name = models.CharField(max_length=100)
    min_distance_km = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    max_distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    postal_prefixes = models.JSONField(default=list, blank=True, help_text="e.g. ['M5V', 'M5J']")
    fee_cents = models.PositiveIntegerField(default=0)
    min_order_cents = models.PositiveIntegerField(default=0)
    estimated_minutes = models.PositiveIntegerField(default=40)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["store", "sort_order", "min_distance_km"]


class DeliveryStatus(models.TextChoices):
    AWAITING_PREPARATION = "awaiting_preparation", "Awaiting preparation"
    PREPARING = "preparing", "Preparing"
    READY = "ready_for_dispatch", "Ready for dispatch"
    ASSIGNED = "assigned", "Assigned to driver"
    PICKED_UP = "picked_up", "Picked up"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed delivery"
    RETURNED = "returned", "Returned to store"
    CANCELLED = "cancelled", "Cancelled"


class DeliveryAssignment(TimeStampedModel):
    """The dispatch record of one store-delivery order (current driver + lifecycle)."""

    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="delivery")
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="deliveries")
    status = models.CharField(max_length=30, choices=DeliveryStatus.choices,
                              default=DeliveryStatus.AWAITING_PREPARATION, db_index=True)
    driver = models.ForeignKey(DriverProfile, null=True, blank=True, on_delete=models.PROTECT,
                               related_name="deliveries")
    zone = models.ForeignKey(DeliveryZone, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    fee_cents = models.PositiveIntegerField(default=0)
    estimated_minutes = models.PositiveIntegerField(null=True, blank=True)
    estimated_delivery_at = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    auto_assigned = models.BooleanField(default=False)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=300, blank=True)
    driver_notes = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1, help_text="Optimistic concurrency token")

    class Meta:
        indexes = [models.Index(fields=["store", "status"]), models.Index(fields=["driver", "status"])]


ACTIVE_FOR_DRIVER = [DeliveryStatus.ASSIGNED, DeliveryStatus.PICKED_UP, DeliveryStatus.OUT_FOR_DELIVERY]


class DeliveryStatusHistory(models.Model):
    assignment = models.ForeignKey(DeliveryAssignment, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    driver = models.ForeignKey(DriverProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    previous_driver = models.ForeignKey(DriverProfile, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    actor_label = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]


class DeliveryProof(models.Model):
    KIND_PHOTO = "photo"
    KIND_SIGNATURE = "signature"
    KIND_NOTE = "note"
    assignment = models.ForeignKey(DeliveryAssignment, on_delete=models.CASCADE, related_name="proofs")
    kind = models.CharField(max_length=10, choices=[(KIND_PHOTO, "Photo"), (KIND_SIGNATURE, "Signature"),
                                                    (KIND_NOTE, "Note")])
    image = models.ImageField(upload_to="delivery-proof/%Y/%m/", null=True, blank=True)
    recipient_name = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
