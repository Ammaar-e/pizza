from rest_framework import serializers

from .models import Franchisee, Headquarters, Store, StoreHours, StoreSpecialHours


class HeadquartersSerializer(serializers.ModelSerializer):
    class Meta:
        model = Headquarters
        fields = ["id", "name", "legal_name", "currency", "default_timezone", "policies"]


class FranchiseeSerializer(serializers.ModelSerializer):
    store_count = serializers.SerializerMethodField()

    class Meta:
        model = Franchisee
        fields = ["id", "code", "name", "legal_name", "contact_email", "contact_phone", "is_active", "store_count",
                  "created_at"]

    def get_store_count(self, obj):
        return obj.stores.count()


class StoreHoursSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreHours
        fields = ["day_of_week", "opens_at", "closes_at"]


class StoreSpecialHoursSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreSpecialHours
        fields = ["date", "is_closed", "opens_at", "closes_at", "note"]


class StoreSerializer(serializers.ModelSerializer):
    hours = StoreHoursSerializer(many=True, required=False)
    special_hours = StoreSpecialHoursSerializer(many=True, required=False)
    franchisee_code = serializers.CharField(source="franchisee.code", read_only=True)
    is_open_now = serializers.SerializerMethodField()
    currency = serializers.CharField(source="franchisee.headquarters.currency", read_only=True)
    payment_secrets_configured = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = ["id", "franchisee", "franchisee_code", "number", "name", "address_line1", "address_line2", "city",
                  "region", "postal_code", "country", "latitude", "longitude", "phone", "timezone", "is_active",
                  "is_temporarily_closed", "temporary_closure_reason", "delivery_config", "pickup_config",
                  "ordering_channels", "payment_config", "payment_secrets_configured", "receipt_header",
                  "receipt_footer", "hours", "special_hours", "is_open_now", "currency"]

    def get_is_open_now(self, obj):
        return obj.is_open()

    def get_payment_secrets_configured(self, obj):
        return sorted(k for k, v in (obj.payment_secrets or {}).items() if v)

    def _save_hours(self, store, hours, special):
        if hours is not None:
            store.hours.all().delete()
            StoreHours.objects.bulk_create([StoreHours(store=store, **h) for h in hours])
        if special is not None:
            store.special_hours.all().delete()
            StoreSpecialHours.objects.bulk_create([StoreSpecialHours(store=store, **h) for h in special])

    def create(self, validated):
        hours = validated.pop("hours", None)
        special = validated.pop("special_hours", None)
        store = Store.objects.create(**validated)
        self._save_hours(store, hours, special)
        return store

    def update(self, instance, validated):
        hours = validated.pop("hours", None)
        special = validated.pop("special_hours", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()
        self._save_hours(instance, hours, special)
        return instance


class PublicStoreSerializer(serializers.ModelSerializer):
    """Store representation for external API clients (no internal config)."""

    hours = StoreHoursSerializer(many=True, read_only=True)
    is_open_now = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()
    pickup = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = ["id", "number", "name", "address_line1", "address_line2", "city", "region", "postal_code",
                  "country", "latitude", "longitude", "phone", "timezone", "is_open_now", "is_temporarily_closed",
                  "hours", "delivery", "pickup"]

    def get_is_open_now(self, obj):
        return obj.is_open()

    def get_delivery(self, obj):
        c = obj.delivery_config or {}
        return {k: c.get(k) for k in ("enabled", "radius_km", "minimum_order_cents", "delivery_fee_cents",
                                      "estimated_minutes")}

    def get_pickup(self, obj):
        c = obj.pickup_config or {}
        return {k: c.get(k) for k in ("enabled", "estimated_minutes")}
