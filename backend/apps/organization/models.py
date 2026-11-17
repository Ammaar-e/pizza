from __future__ import annotations

import secrets
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.encryption import EncryptedJSONField
from apps.core.models import TimeStampedModel


class Headquarters(TimeStampedModel):
    """Singleton-like root of the organisation tree."""

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, default="CAD")
    default_timezone = models.CharField(max_length=64, default="America/Toronto")
    policies = models.JSONField(
        default=dict,
        blank=True,
        help_text="Company policies, e.g. max_discount_percent, refund_requires_manager",
    )

    class Meta:
        verbose_name_plural = "headquarters"

    def __str__(self):
        return self.name


class Franchisee(TimeStampedModel):
    headquarters = models.ForeignKey(Headquarters, on_delete=models.PROTECT, related_name="franchisees")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


store_number_validator = RegexValidator(r"^[0-9A-Za-z\-]{2,20}$", "Store number must be 2-20 alphanumeric characters")


def default_delivery_config():
    return {"enabled": True, "radius_km": 8, "minimum_order_cents": 1500, "delivery_fee_cents": 399, "estimated_minutes": 40}


def default_pickup_config():
    return {"enabled": True, "estimated_minutes": 15}


def default_channels():
    return {"pos": True, "phone": True, "call_centre": True, "external_api": True, "android": False, "ios": False}


class Store(TimeStampedModel):
    franchisee = models.ForeignKey(Franchisee, on_delete=models.PROTECT, related_name="stores")
    number = models.CharField(max_length=20, unique=True, validators=[store_number_validator])
    name = models.CharField(max_length=200)
    address_line1 = models.CharField(max_length=200)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, help_text="Province/state")
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=2, default="CA")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    phone = models.CharField(max_length=40)
    timezone = models.CharField(max_length=64, default="America/Toronto")
    is_active = models.BooleanField(default=True)
    is_temporarily_closed = models.BooleanField(default=False)
    temporary_closure_reason = models.CharField(max_length=200, blank=True)
    delivery_config = models.JSONField(default=default_delivery_config, blank=True)
    pickup_config = models.JSONField(default=default_pickup_config, blank=True)
    ordering_channels = models.JSONField(default=default_channels, blank=True)
    payment_config = models.JSONField(
        default=dict, blank=True,
        help_text="Non-secret payment settings: accepted methods, card provider, terminal ids",
    )
    payment_secrets = EncryptedJSONField(blank=True, default=dict, help_text="Encrypted card-processor secrets")
    receipt_header = models.TextField(blank=True)
    receipt_footer = models.TextField(blank=True)

    class Meta:
        ordering = ["number"]
        indexes = [models.Index(fields=["franchisee", "is_active"])]

    def __str__(self):
        return f"{self.number} {self.name}"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def local_now(self, now: datetime | None = None) -> datetime:
        return (now or timezone.now()).astimezone(self.tz)

    def is_open(self, at: datetime | None = None) -> bool:
        if not self.is_active or self.is_temporarily_closed:
            return False
        local = self.local_now(at)
        special = self.special_hours.filter(date=local.date()).first()
        if special:
            if special.is_closed:
                return False
            return _within(local.time(), special.opens_at, special.closes_at)
        today = self.hours.filter(day_of_week=local.weekday())
        for h in today:
            if _within(local.time(), h.opens_at, h.closes_at):
                return True
        # Overnight hours from the previous day (e.g. 11:00-02:00)
        prev = self.hours.filter(day_of_week=(local.weekday() - 1) % 7)
        for h in prev:
            if h.closes_at < h.opens_at and local.time() < h.closes_at:
                return True
        return False


def _within(t: time, opens: time, closes: time) -> bool:
    if opens is None or closes is None:
        return False
    if closes > opens:
        return opens <= t < closes
    return t >= opens  # overnight; remainder handled by next-day check


class StoreHours(models.Model):
    DAYS = [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="hours")
    day_of_week = models.PositiveSmallIntegerField(choices=DAYS)
    opens_at = models.TimeField()
    closes_at = models.TimeField()

    class Meta:
        ordering = ["store", "day_of_week", "opens_at"]


class StoreSpecialHours(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="special_hours")
    date = models.DateField()
    is_closed = models.BooleanField(default=False)
    opens_at = models.TimeField(null=True, blank=True)
    closes_at = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("store", "date")]


class Device(TimeStampedModel):
    """A registered POS terminal or KDS screen."""

    TYPE_POS = "pos"
    TYPE_KDS = "kds"
    TYPE_GATEWAY = "gateway"
    TYPES = [(TYPE_POS, "POS terminal"), (TYPE_KDS, "Kitchen display"), (TYPE_GATEWAY, "Store gateway")]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="devices")
    device_type = models.CharField(max_length=20, choices=TYPES)
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=128, unique=True)
    token_prefix = models.CharField(max_length=12)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    registered_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    kitchen_station = models.ForeignKey(
        "kitchen.KitchenStation", null=True, blank=True, on_delete=models.SET_NULL, related_name="devices"
    )

    class Meta:
        ordering = ["store", "name"]

    @staticmethod
    def generate_token() -> str:
        return "dev_" + secrets.token_urlsafe(32)

    @staticmethod
    def hash_token(token: str) -> str:
        import hashlib

        return hashlib.sha256(token.encode()).hexdigest()


class StoreOrderSequence(models.Model):
    """Per-store, per-business-day order number counter (row-locked)."""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="+")
    business_date = models.DateField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("store", "business_date")]
