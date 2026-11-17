from django.db import transaction
from rest_framework import serializers

from .models import (
    AdditionalCharge,
    CatalogVersion,
    Category,
    Crust,
    Discount,
    Modifier,
    ModifierGroup,
    PrepCategory,
    Product,
    ProductDefaultModifier,
    ProductModifierGroup,
    ProductRequest,
    ProductSize,
    StoreModifierAvailability,
    StoreProduct,
    TaxCategory,
    TaxRate,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "code", "name", "description", "sort_order", "is_active"]


class CrustSerializer(serializers.ModelSerializer):
    class Meta:
        model = Crust
        fields = ["id", "code", "name", "price_cents", "is_active", "sort_order"]


class PrepCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PrepCategory
        fields = ["id", "code", "name"]


class TaxCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxCategory
        fields = ["id", "code", "name"]


class TaxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRate
        fields = ["id", "tax_category", "region", "name", "rate_bps"]


class ModifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Modifier
        fields = ["id", "group", "code", "name", "price_cents", "is_topping", "is_active", "sort_order", "prep_note"]


class ModifierGroupSerializer(serializers.ModelSerializer):
    modifiers = ModifierSerializer(many=True, read_only=True)

    class Meta:
        model = ModifierGroup
        fields = ["id", "code", "name", "min_select", "max_select", "max_quantity_per_option", "free_selections",
                  "allow_half", "half_price_percent", "scale_with_size", "sort_order", "modifiers"]


class AdditionalChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdditionalCharge
        fields = ["id", "code", "name", "amount_cents", "applies_to", "tax_category", "is_active"]


class DiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Discount
        fields = ["id", "franchisee", "code", "name", "discount_type", "value", "requires_manager", "is_active",
                  "max_amount_cents"]


class ProductSizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSize
        fields = ["code", "name", "price_cents", "modifier_multiplier_percent", "sort_order", "is_active"]


class ProductGroupSerializer(serializers.Serializer):
    group = serializers.SlugRelatedField(slug_field="code", queryset=ModifierGroup.objects.all())
    sort_order = serializers.IntegerField(default=0)
    min_select_override = serializers.IntegerField(required=False, allow_null=True)
    max_select_override = serializers.IntegerField(required=False, allow_null=True)


class ProductDefaultSerializer(serializers.Serializer):
    modifier = serializers.SlugRelatedField(slug_field="code", queryset=Modifier.objects.all())
    quantity = serializers.IntegerField(default=1, min_value=1)
    removable = serializers.BooleanField(default=True)


class ProductSerializer(serializers.ModelSerializer):
    sizes = ProductSizeSerializer(many=True, required=False)
    crusts = serializers.SlugRelatedField(many=True, slug_field="code", queryset=Crust.objects.all(), required=False)
    default_crust = serializers.SlugRelatedField(slug_field="code", queryset=Crust.objects.all(), required=False,
                                                 allow_null=True)
    modifier_groups = serializers.SerializerMethodField()
    modifier_group_links = ProductGroupSerializer(many=True, write_only=True, required=False)
    default_modifiers = serializers.SerializerMethodField()
    default_modifier_links = ProductDefaultSerializer(many=True, write_only=True, required=False)
    category_code = serializers.CharField(source="category.code", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "sku", "name", "description", "category", "category_code", "image", "image_url", "status",
                  "is_pizza", "base_price_cents", "min_price_cents", "max_price_cents", "allow_price_override",
                  "tax_category", "prep_category", "crusts", "default_crust", "allow_half_and_half", "max_toppings",
                  "is_alcohol", "sort_order", "version", "sizes", "modifier_groups", "modifier_group_links",
                  "default_modifiers", "default_modifier_links", "updated_at"]
        read_only_fields = ["version", "updated_at"]

    def get_modifier_groups(self, obj):
        return [{"code": l.group.code, "name": l.group.name, "sort_order": l.sort_order,
                 "min_select_override": l.min_select_override, "max_select_override": l.max_select_override}
                for l in obj.productmodifiergroup_set.select_related("group")]

    def get_default_modifiers(self, obj):
        return [{"code": d.modifier.code, "name": d.modifier.name, "quantity": d.quantity, "removable": d.removable}
                for d in obj.default_modifiers.select_related("modifier")]

    def validate(self, attrs):
        lo = attrs.get("min_price_cents", getattr(self.instance, "min_price_cents", None))
        hi = attrs.get("max_price_cents", getattr(self.instance, "max_price_cents", None))
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError("min_price_cents cannot exceed max_price_cents")
        return attrs

    @transaction.atomic
    def _save_nested(self, product, sizes, crusts, groups, defaults):
        if sizes is not None:
            product.sizes.all().delete()
            ProductSize.objects.bulk_create([ProductSize(product=product, **s) for s in sizes])
        if crusts is not None:
            product.crusts.set(crusts)
        if groups is not None:
            ProductModifierGroup.objects.filter(product=product).delete()
            ProductModifierGroup.objects.bulk_create([ProductModifierGroup(product=product, **g) for g in groups])
        if defaults is not None:
            ProductDefaultModifier.objects.filter(product=product).delete()
            ProductDefaultModifier.objects.bulk_create([ProductDefaultModifier(product=product, **d)
                                                        for d in defaults])

    def create(self, validated):
        nested = [validated.pop(k, None) for k in ("sizes", "crusts", "modifier_group_links", "default_modifier_links")]
        product = Product.objects.create(**validated)
        self._save_nested(product, *nested)
        return product

    def update(self, instance, validated):
        nested = [validated.pop(k, None) for k in ("sizes", "crusts", "modifier_group_links", "default_modifier_links")]
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.version += 1
        instance.save()
        self._save_nested(instance, *nested)
        return instance


class StoreProductSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    name = serializers.CharField(source="product.name", read_only=True)
    category = serializers.CharField(source="product.category.name", read_only=True)
    min_price_cents = serializers.IntegerField(source="product.min_price_cents", read_only=True)
    max_price_cents = serializers.IntegerField(source="product.max_price_cents", read_only=True)
    default_sizes = serializers.SerializerMethodField()

    class Meta:
        model = StoreProduct
        fields = ["id", "store", "product", "sku", "name", "category", "enabled", "is_available", "price_cents",
                  "size_prices", "channel_prices", "min_price_cents", "max_price_cents", "default_sizes"]
        read_only_fields = ["store"]

    def get_default_sizes(self, obj):
        return [{"code": s.code, "name": s.name, "price_cents": s.price_cents} for s in obj.product.sizes.all()]


class StoreModifierAvailabilitySerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="modifier.code", read_only=True)

    class Meta:
        model = StoreModifierAvailability
        fields = ["id", "modifier", "code", "is_available"]


class ProductRequestSerializer(serializers.ModelSerializer):
    franchisee_code = serializers.CharField(source="franchisee.code", read_only=True)
    submitted_by_name = serializers.CharField(source="submitted_by.username", read_only=True)

    class Meta:
        model = ProductRequest
        fields = ["id", "franchisee", "franchisee_code", "store", "submitted_by_name", "name", "description",
                  "category", "proposed_price_cents", "details", "status", "reviewed_by", "reviewed_at",
                  "review_notes", "resulting_product", "created_at"]
        read_only_fields = ["franchisee", "status", "reviewed_by", "reviewed_at", "review_notes", "resulting_product"]


class CatalogVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogVersion
        fields = ["id", "kind", "entity_id", "store", "version", "data", "changed_by", "created_at", "note"]


class QuoteLineSerializer(serializers.Serializer):
    product = serializers.CharField()
    size = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    crust = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1, max_value=99, default=1)
    modifiers = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    removed = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    special_instructions = serializers.CharField(required=False, allow_blank=True, default="")
