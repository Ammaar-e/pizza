"""Promotions and discounts (replaces the earlier flat ``catalog.Discount`` codes).

Ownership: HQ (franchisee=None, store=None), a franchisee, or a single store. HQ promotions are
published to franchisees/stores through ``PromotionStore`` targets.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PromotionType(models.TextChoices):
    PERCENT = "percent", "Percentage discount"
    FIXED = "fixed", "Fixed amount discount"
    BOGO = "bogo", "Buy one get one"
    BUY_X_GET_Y = "buy_x_get_y", "Buy X get Y"
    FREE_PRODUCT = "free_product", "Free product"
    FREE_MODIFIER = "free_modifier", "Free toppings / modifiers"
    BUNDLE = "bundle", "Meal deal / bundle price"


ALL_TYPES = [t.value for t in PromotionType]
CHANNELS = ["pos", "call_centre", "external_api", "android", "ios", "other"]


class Promotion(TimeStampedModel):
    SCOPE_HQ = "hq"
    SCOPE_FRANCHISE = "franchise"
    SCOPE_STORE = "store"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    campaign = models.CharField(max_length=80, blank=True, db_index=True, help_text="Campaign label for reporting")
    promo_type = models.CharField(max_length=20, choices=PromotionType.choices)
    franchisee = models.ForeignKey("organization.Franchisee", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="promotions", help_text="Owner; null = headquarters")
    store = models.ForeignKey("organization.Store", null=True, blank=True, on_delete=models.CASCADE,
                              related_name="local_promotions", help_text="Store-level promotion")
    # Value: basis points for percent / reward_percent; cents for fixed / bundle price.
    value = models.PositiveIntegerField(default=0)
    max_discount_cents = models.PositiveIntegerField(null=True, blank=True)
    min_subtotal_cents = models.PositiveIntegerField(default=0)
    buy_quantity = models.PositiveIntegerField(default=1)
    get_quantity = models.PositiveIntegerField(default=1)
    reward_percent_bps = models.PositiveIntegerField(default=10000, help_text="Discount on reward items; 10000 = free")
    max_applications = models.PositiveIntegerField(default=0, help_text="Per order; 0 = unlimited")
    # Scheduling
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True, help_text="Daily window start (store local time)")
    end_time = models.TimeField(null=True, blank=True)
    days_of_week = models.JSONField(default=list, blank=True, help_text="0=Mon..6=Sun; empty = every day")
    channels = models.JSONField(default=list, blank=True, help_text="Order sources; empty = all internal channels")
    order_types = models.JSONField(default=list, blank=True, help_text="pickup/delivery/dine_in; empty = all")
    # Limits
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    max_per_customer = models.PositiveIntegerField(null=True, blank=True)
    # Application & stacking
    auto_apply = models.BooleanField(default=False)
    requires_code = models.BooleanField(default=False)
    priority = models.IntegerField(default=0, help_text="Higher evaluates first")
    combinable = models.BooleanField(default=True, help_text="May be combined with other promotions")
    requires_manager = models.BooleanField(default=False, help_text="Manual code entry needs manager approval")
    is_enabled = models.BooleanField(default=True)
    status = models.CharField(max_length=20, default="draft", db_index=True,
                              help_text="Derived: draft, scheduled, active, expired, disabled")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-priority", "name"]
        indexes = [models.Index(fields=["status", "is_enabled"])]

    def __str__(self):
        return self.name

    @property
    def scope(self) -> str:
        return self.SCOPE_STORE if self.store_id else self.SCOPE_FRANCHISE if self.franchisee_id else self.SCOPE_HQ

    def derive_status(self, now=None) -> str:
        now = now or timezone.now()
        if not self.is_enabled:
            return "disabled"
        if self.ends_at and self.ends_at <= now:
            return "expired"
        if self.starts_at and self.starts_at > now:
            return "scheduled"
        return "active"


class PromotionCode(TimeStampedModel):
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="codes")
    code = models.CharField(max_length=40, unique=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Per-code limit (single-use coupons = 1)")
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class PromotionProduct(models.Model):
    """Product/category/modifier eligibility for a promotion.

    role: eligible (qualifies & is discounted), excluded, reward (BxGy/free product reward set),
    bundle (a bundle slot: ``slot`` index with ``quantity`` units from product/category),
    modifier (free modifier/topping)."""

    ROLES = [("eligible", "Eligible"), ("excluded", "Excluded"), ("reward", "Reward"), ("bundle", "Bundle slot"),
             ("modifier", "Free modifier")]
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="items")
    role = models.CharField(max_length=10, choices=ROLES)
    product = models.ForeignKey("catalog.Product", null=True, blank=True, on_delete=models.CASCADE)
    category = models.ForeignKey("catalog.Category", null=True, blank=True, on_delete=models.CASCADE)
    modifier = models.ForeignKey("catalog.Modifier", null=True, blank=True, on_delete=models.CASCADE)
    modifier_group = models.ForeignKey("catalog.ModifierGroup", null=True, blank=True, on_delete=models.CASCADE)
    size_code = models.CharField(max_length=20, blank=True, help_text="Restrict to a size, e.g. L")
    slot = models.PositiveIntegerField(default=0)
    quantity = models.PositiveIntegerField(default=1)


class PromotionStore(models.Model):
    """Publication targets for HQ/franchise promotions. No rows = all stores within the owner's scope."""

    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="targets")
    franchisee = models.ForeignKey("organization.Franchisee", null=True, blank=True, on_delete=models.CASCADE)
    store = models.ForeignKey("organization.Store", null=True, blank=True, on_delete=models.CASCADE)


class PromotionRule(models.Model):
    """Customer-specific eligibility (any matching rule qualifies when rules exist)."""

    RULES = [("customer_phone", "Customer phone"), ("customer_email", "Customer email"),
             ("customer_id", "Customer id")]
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="rules")
    rule_type = models.CharField(max_length=20, choices=RULES)
    value = models.CharField(max_length=200)


class PromotionRedemption(TimeStampedModel):
    STATUS_APPLIED = "applied"
    STATUS_VOIDED = "voided"

    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="redemptions")
    code = models.ForeignKey(PromotionCode, null=True, blank=True, on_delete=models.PROTECT, related_name="redemptions")
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="promotion_redemptions")
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="+")
    customer_key = models.CharField(max_length=200, blank=True, db_index=True)
    channel = models.CharField(max_length=20)
    amount_cents = models.IntegerField()
    status = models.CharField(max_length=10, default=STATUS_APPLIED, db_index=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "promotion"], condition=models.Q(status="applied"),
                                               name="one_redemption_per_order_promotion")]
        indexes = [models.Index(fields=["promotion", "status"]), models.Index(fields=["store", "created_at"])]


class FranchisePromotionPolicy(models.Model):
    """HQ-defined limits on franchise-created promotions."""

    franchisee = models.OneToOneField("organization.Franchisee", on_delete=models.CASCADE,
                                      related_name="promotion_policy")
    can_create = models.BooleanField(default=True)
    allowed_types = models.JSONField(default=list, blank=True, help_text="Empty = none allowed")
    max_percent_bps = models.PositiveIntegerField(default=3000)
    max_fixed_cents = models.PositiveIntegerField(default=1000)
    store_managers_can_manage = models.BooleanField(default=False,
                                                    help_text="Set by the franchisee (within HQ permission)")
    hq_allows_store_managers = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
