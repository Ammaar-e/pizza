"""Master product catalogue (HQ-controlled) and store-level menu configuration.

Money is stored as integer cents throughout the platform.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class TaxCategory(TimeStampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.code


class TaxRate(TimeStampedModel):
    """Tax rate applying to a tax category in a region (province/state)."""

    tax_category = models.ForeignKey(TaxCategory, on_delete=models.CASCADE, related_name="rates")
    region = models.CharField(max_length=100, help_text="Store.region this applies to; '*' for default")
    name = models.CharField(max_length=50, help_text="e.g. HST, GST")
    rate_bps = models.PositiveIntegerField(help_text="Basis points: 1300 = 13%")

    class Meta:
        unique_together = [("tax_category", "region", "name")]


class StoreTaxOverride(TimeStampedModel):
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="tax_overrides")
    tax_category = models.ForeignKey(TaxCategory, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    rate_bps = models.PositiveIntegerField()

    class Meta:
        unique_together = [("store", "tax_category", "name")]


class PrepCategory(TimeStampedModel):
    """Kitchen preparation category used for station routing (oven, fryer, cold...)."""

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.code


class Category(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Crust(TimeStampedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ModifierGroup(TimeStampedModel):
    """A set of options (e.g. Toppings, Sauces, Dips) with selection limits."""

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    min_select = models.PositiveIntegerField(default=0)
    max_select = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    max_quantity_per_option = models.PositiveIntegerField(default=2, help_text="2 allows 'extra'")
    free_selections = models.PositiveIntegerField(default=0, help_text="Number of selections included free")
    allow_half = models.BooleanField(default=False, help_text="Options may be placed on left/right half")
    half_price_percent = models.PositiveIntegerField(
        default=100, help_text="Price percentage charged for a half-pizza placement"
    )
    scale_with_size = models.BooleanField(default=False, help_text="Option price multiplied by size multiplier")
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Modifier(TimeStampedModel):
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="modifiers")
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField(default=0)
    is_topping = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    prep_note = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["group", "sort_order", "name"]

    def __str__(self):
        return self.name


class AdditionalCharge(TimeStampedModel):
    """Order-level charges, e.g. delivery fee, bag fee, service charge."""

    APPLIES = [("delivery", "Delivery orders"), ("pickup", "Pickup orders"), ("all", "All orders")]
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    amount_cents = models.PositiveIntegerField()
    applies_to = models.CharField(max_length=20, choices=APPLIES, default="all")
    tax_category = models.ForeignKey(TaxCategory, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)


class Product(TimeStampedModel):
    STATUS_DRAFT = "draft"
    STATUS_APPROVED = "approved"
    STATUS_RETIRED = "retired"
    STATUSES = [(STATUS_DRAFT, "Draft"), (STATUS_APPROVED, "Approved for franchise use"), (STATUS_RETIRED, "Retired")]

    sku = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    image_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_DRAFT, db_index=True)
    is_pizza = models.BooleanField(default=False)
    base_price_cents = models.PositiveIntegerField(default=0, help_text="Used when the product has no sizes")
    min_price_cents = models.PositiveIntegerField(null=True, blank=True, help_text="Lowest permitted franchise price")
    max_price_cents = models.PositiveIntegerField(null=True, blank=True, help_text="Highest permitted franchise price")
    allow_price_override = models.BooleanField(default=True, help_text="Franchisees may set price within range")
    tax_category = models.ForeignKey(TaxCategory, on_delete=models.PROTECT, related_name="products")
    prep_category = models.ForeignKey(PrepCategory, on_delete=models.PROTECT, related_name="products")
    crusts = models.ManyToManyField(Crust, blank=True, related_name="products")
    default_crust = models.ForeignKey(Crust, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    modifier_groups = models.ManyToManyField(ModifierGroup, through="ProductModifierGroup", blank=True)
    allow_half_and_half = models.BooleanField(default=False)
    max_toppings = models.PositiveIntegerField(default=0, help_text="0 = governed by modifier groups only")
    is_alcohol = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["category__sort_order", "sort_order", "name"]

    def __str__(self):
        return f"{self.sku} {self.name}"


class ProductSize(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="sizes")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=50)
    price_cents = models.PositiveIntegerField()
    modifier_multiplier_percent = models.PositiveIntegerField(default=100)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("product", "code")]
        ordering = ["product", "sort_order"]


class ProductModifierGroup(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE)
    sort_order = models.IntegerField(default=0)
    min_select_override = models.PositiveIntegerField(null=True, blank=True)
    max_select_override = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("product", "group")]
        ordering = ["sort_order"]


class ProductDefaultModifier(models.Model):
    """Ingredients included by default (e.g. pepperoni on a Pepperoni pizza). Removable."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="default_modifiers")
    modifier = models.ForeignKey(Modifier, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    removable = models.BooleanField(default=True)

    class Meta:
        unique_together = [("product", "modifier")]


# --------------------------------------------------------------------------
# Store-level menu configuration
# --------------------------------------------------------------------------
class StoreProduct(TimeStampedModel):
    """A franchisee enabling an HQ-approved product at a store, with permitted pricing."""

    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="store_products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="store_products")
    enabled = models.BooleanField(default=True)
    is_available = models.BooleanField(default=True, help_text="False = temporarily unavailable (86'd)")
    price_cents = models.PositiveIntegerField(null=True, blank=True, help_text="Override for unsized products")
    size_prices = models.JSONField(default=dict, blank=True, help_text="{size_code: cents} overrides")
    channel_prices = models.JSONField(
        default=dict, blank=True, help_text="{channel: {size_code|'_': cents}} e.g. third-party markup"
    )

    class Meta:
        unique_together = [("store", "product")]
        indexes = [models.Index(fields=["store", "enabled", "is_available"])]


class StoreModifierAvailability(TimeStampedModel):
    store = models.ForeignKey("organization.Store", on_delete=models.CASCADE, related_name="modifier_availability")
    modifier = models.ForeignKey(Modifier, on_delete=models.CASCADE)
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = [("store", "modifier")]


class ProductRequest(TimeStampedModel):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUSES = [(STATUS_PENDING, "Pending"), (STATUS_APPROVED, "Approved"), (STATUS_REJECTED, "Rejected")]

    franchisee = models.ForeignKey("organization.Franchisee", on_delete=models.CASCADE, related_name="product_requests")
    store = models.ForeignKey("organization.Store", null=True, blank=True, on_delete=models.SET_NULL)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    proposed_price_cents = models.PositiveIntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_PENDING, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    resulting_product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)


class CatalogVersion(models.Model):
    """Version history for products, prices and published menus."""

    KIND_PRODUCT = "product"
    KIND_STORE_PRICE = "store_price"
    KIND_MENU = "menu"
    KINDS = [(KIND_PRODUCT, "Product"), (KIND_STORE_PRICE, "Store pricing"), (KIND_MENU, "Published menu")]

    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    entity_id = models.CharField(max_length=64, db_index=True)
    store = models.ForeignKey("organization.Store", null=True, blank=True, on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    data = models.JSONField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("kind", "entity_id", "version")]
        ordering = ["-created_at"]


class Discount(TimeStampedModel):
    TYPE_PERCENT = "percent"
    TYPE_FIXED = "fixed"
    TYPES = [(TYPE_PERCENT, "Percent"), (TYPE_FIXED, "Fixed amount")]

    franchisee = models.ForeignKey(
        "organization.Franchisee", null=True, blank=True, on_delete=models.CASCADE, related_name="discounts",
        help_text="Null = network-wide (HQ) discount",
    )
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    discount_type = models.CharField(max_length=10, choices=TYPES)
    value = models.PositiveIntegerField(help_text="Basis points for percent (1000=10%), cents for fixed")
    requires_manager = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    max_amount_cents = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("franchisee", "code")]
