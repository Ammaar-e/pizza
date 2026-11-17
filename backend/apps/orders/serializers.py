from rest_framework import serializers

from apps.catalog.serializers import QuoteLineSerializer

from .models import Order, OrderDiscount, OrderEvent, OrderItem, OrderItemModifier, OrderSource, OrderStatus, \
    OrderType


class CustomerInputSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    phone = serializers.CharField(required=False, allow_blank=True, max_length=40, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")


class AddressSerializer(serializers.Serializer):
    line1 = serializers.CharField(max_length=200)
    line2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100)
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)
    postal_code = serializers.CharField(max_length=20)
    instructions = serializers.CharField(max_length=500, required=False, allow_blank=True)
    latitude = serializers.FloatField(required=False)
    longitude = serializers.FloatField(required=False)


class PaymentDeclarationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["unpaid", "prepaid"], default="unpaid")
    amount_cents = serializers.IntegerField(min_value=0, required=False, default=0)
    reference = serializers.CharField(max_length=200, required=False, allow_blank=True, default="",
                                      help_text="Payment gateway reference for prepaid orders (never card data)")


class OrderCreateSerializer(serializers.Serializer):
    """Normalized order submission used by the POS, call centre and external applications."""

    store_id = serializers.IntegerField(required=False)
    store_number = serializers.CharField(required=False)
    order_type = serializers.ChoiceField(choices=[OrderType.PICKUP, OrderType.DELIVERY, OrderType.DINE_IN,
                                                  OrderType.PROVIDER_DELIVERY])
    items = QuoteLineSerializer(many=True)
    customer = CustomerInputSerializer(required=False)
    delivery_address = AddressSerializer(required=False)
    special_instructions = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True)
    tip_cents = serializers.IntegerField(required=False, min_value=0, default=0)
    expected_total_cents = serializers.IntegerField(required=False, allow_null=True,
                                                    help_text="If provided, must equal the calculated total")
    payment = PaymentDeclarationSerializer(required=False)
    # POS only:
    entry_channel = serializers.ChoiceField(choices=["walk_in", "phone", "manual_provider"], required=False)
    source = serializers.ChoiceField(choices=[OrderSource.POS, OrderSource.UBER_EATS, OrderSource.DOORDASH,
                                              OrderSource.SKIP], required=False,
                                     help_text="POS only: record a provider order manually")
    external_order_id = serializers.CharField(required=False, allow_blank=True, max_length=100)
    offline_client_ref = serializers.CharField(required=False, allow_blank=True, max_length=100)
    promo_codes = serializers.ListField(child=serializers.CharField(max_length=40), required=False, default=list,
                                        help_text="Coupon / promotion codes; validated server-side")
    promotion_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list,
                                          help_text="POS only: manually selected promotions without codes")
    manager_code = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        if not attrs.get("store_id") and not attrs.get("store_number"):
            raise serializers.ValidationError("store_id or store_number is required")
        if attrs["order_type"] == OrderType.DELIVERY and not attrs.get("delivery_address"):
            raise serializers.ValidationError({"delivery_address": "Required for delivery orders"})
        if not attrs.get("items"):
            raise serializers.ValidationError({"items": "At least one item is required"})
        return attrs


class OrderItemModifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemModifier
        fields = ["code", "name", "group_code", "placement", "quantity", "price_cents", "is_default", "external_id"]


class OrderItemSerializer(serializers.ModelSerializer):
    modifiers = OrderItemModifierSerializer(many=True, read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "position", "sku", "name", "size_code", "size_name", "crust_code", "crust_name", "quantity",
                  "unit_price_cents", "line_total_cents", "discount_cents", "tax_cents", "removed", "special_instructions",
                  "prep_category", "external_item_id", "is_mapped", "modifiers"]


class OrderEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderEvent
        fields = ["id", "event_type", "from_status", "to_status", "actor_label", "message", "data", "created_at"]


class OrderDiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderDiscount
        fields = ["id", "promotion", "code", "name", "amount_cents", "allocation", "applied_by", "authorized_by",
                  "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    discounts = OrderDiscountSerializer(many=True, read_only=True)
    store_number = serializers.CharField(source="store.number", read_only=True)
    payments = serializers.SerializerMethodField()
    amount_paid_cents = serializers.SerializerMethodField()
    integration = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "order_number", "business_date", "franchisee", "store", "store_number", "source",
                  "entry_channel", "external_order_id", "external_display_id", "order_type", "status",
                  "payment_status", "provider_sync_status", "provider_sync_error", "customer_snapshot",
                  "delivery_address", "special_instructions", "scheduled_for", "currency", "subtotal_cents",
                  "discount_cents", "charges", "charges_cents", "tax_cents", "tax_breakdown", "tip_cents",
                  "total_cents", "amount_paid_cents", "provider_totals", "validation_issues", "requires_attention",
                  "attention_reason", "rejection_reason", "cancellation_reason", "is_offline_origin",
                  "offline_client_ref", "placed_at", "accepted_at", "ready_at", "completed_at", "cancelled_at",
                  "provider_ack_deadline", "estimated_ready_at", "items", "discounts", "payments", "integration",
                  "updated_at"]

    def get_payments(self, obj):
        return [{"id": str(p.id), "method": p.method, "processor": p.processor, "status": p.status,
                 "amount_cents": p.amount_cents, "refunded_cents": p.refunded_cents, "tip_cents": p.tip_cents,
                 "tendered_cents": p.tendered_cents, "change_cents": p.change_cents, "card_brand": p.card_brand,
                 "card_last4": p.card_last4, "created_at": p.created_at} for p in obj.payments.all()]

    def get_amount_paid_cents(self, obj):
        return obj.amount_paid_cents

    def get_integration(self, obj):
        si = obj.store_integration
        if si is None:
            return None
        return {"provider": si.provider.code, "provider_name": si.provider.name, "sync_status": obj.provider_sync_status,
                "sync_error": obj.provider_sync_error,
                "webhook_events": [{"event_id": e.event_id, "type": e.event_type, "status": e.status,
                                    "error": e.last_error, "received_at": e.received_at}
                                   for e in obj.webhook_events.all()[:10]]}


class PublicOrderSerializer(serializers.ModelSerializer):
    """Order representation for external API clients."""

    items = OrderItemSerializer(many=True, read_only=True)
    store_number = serializers.CharField(source="store.number", read_only=True)

    class Meta:
        model = Order
        fields = ["id", "order_number", "store", "store_number", "source", "order_type", "status", "payment_status",
                  "customer_snapshot", "delivery_address", "special_instructions", "scheduled_for", "currency",
                  "subtotal_cents", "discount_cents", "charges", "charges_cents", "tax_cents", "tip_cents",
                  "total_cents", "rejection_reason", "cancellation_reason", "placed_at", "accepted_at", "ready_at",
                  "completed_at", "cancelled_at", "estimated_ready_at", "items"]


class OrderListSerializer(serializers.ModelSerializer):
    store_number = serializers.CharField(source="store.number", read_only=True)
    customer_name = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "order_number", "store", "store_number", "source", "entry_channel", "external_display_id",
                  "order_type", "status", "payment_status", "provider_sync_status", "requires_attention",
                  "attention_reason", "customer_name", "total_cents", "item_count", "placed_at", "is_offline_origin"]

    def get_customer_name(self, obj):
        return (obj.customer_snapshot or {}).get("name", "")

    def get_item_count(self, obj):
        return sum(i.quantity for i in obj.items.all())


class OrderPatchSerializer(serializers.Serializer):
    special_instructions = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    customer = CustomerInputSerializer(required=False)
    delivery_address = AddressSerializer(required=False)
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True)


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    manager_code = serializers.CharField(required=False, allow_blank=True)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)
    manager_code = serializers.CharField(required=False, allow_blank=True)


class DiscountApplySerializer(serializers.Serializer):
    discount_code = serializers.CharField(required=False, help_text="Promotion / coupon code")
    promotion_id = serializers.IntegerField(required=False, help_text="Manually selectable promotion")
    amount_cents = serializers.IntegerField(required=False, min_value=1, help_text="Open (manual) discount amount")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    manager_code = serializers.CharField(required=False, allow_blank=True)


class PaymentCreateSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=["cash", "card", "online"])
    amount_cents = serializers.IntegerField(min_value=1)
    tendered_cents = serializers.IntegerField(required=False, min_value=0)
    tip_cents = serializers.IntegerField(required=False, min_value=0, default=0)
    reference = serializers.CharField(required=False, allow_blank=True, default="")


class RefundCreateSerializer(serializers.Serializer):
    amount_cents = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=300)
    manager_code = serializers.CharField(required=False, allow_blank=True)
