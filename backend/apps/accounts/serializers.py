from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.organization.models import Store

from .models import Role, StoreAssignment, User


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(many=True, read_only=True, slug_field="code")

    class Meta:
        model = Role
        fields = ["id", "code", "name", "level", "permissions"]


class UserSerializer(serializers.ModelSerializer):
    role = serializers.SlugRelatedField(slug_field="code", queryset=Role.objects.all())
    store_ids = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Store.objects.all(), write_only=True, required=False)
    stores = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    pin = serializers.RegexField(r"^\d{4,8}$", write_only=True, required=False, allow_blank=True)
    manager_code = serializers.RegexField(r"^\d{4,10}$", write_only=True, required=False, allow_blank=True)
    has_pin = serializers.SerializerMethodField()
    has_manager_code = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "phone", "employee_number", "role",
                  "franchisee", "is_active", "stores", "store_ids", "password", "pin", "manager_code", "has_pin",
                  "has_manager_code", "last_login", "date_joined"]
        read_only_fields = ["last_login", "date_joined"]

    def get_stores(self, obj):
        return [{"id": a.store_id, "number": a.store.number} for a in obj.store_assignments.filter(active=True)
                .select_related("store")]

    def get_has_pin(self, obj):
        return bool(obj.pin_hash)

    def get_has_manager_code(self, obj):
        return bool(obj.manager_code_hash)

    def validate_password(self, value):
        validate_password(value)
        return value

    def _apply_secrets(self, user, data):
        if data.get("password"):
            user.set_password(data["password"])
        if "pin" in data:
            user.set_pin(data["pin"] or None)
        if "manager_code" in data:
            user.set_manager_code(data["manager_code"] or None)

    def create(self, validated):
        stores = validated.pop("store_ids", [])
        secrets = {k: validated.pop(k) for k in ("password", "pin", "manager_code") if k in validated}
        if not secrets.get("password"):
            raise serializers.ValidationError({"password": "Password is required"})
        user = User(**validated)
        self._apply_secrets(user, secrets)
        user.save()
        for s in stores:
            StoreAssignment.objects.get_or_create(user=user, store=s)
        return user

    def update(self, user, validated):
        stores = validated.pop("store_ids", None)
        secrets = {k: validated.pop(k) for k in ("password", "pin", "manager_code") if k in validated}
        for k, v in validated.items():
            setattr(user, k, v)
        self._apply_secrets(user, secrets)
        user.save()
        if stores is not None:
            user.store_assignments.exclude(store__in=stores).update(active=False)
            for s in stores:
                StoreAssignment.objects.update_or_create(user=user, store=s, defaults={"active": True})
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class POSLoginSerializer(serializers.Serializer):
    store_number = serializers.CharField()
    username = serializers.CharField()
    password = serializers.CharField(required=False, allow_blank=True)
    pin = serializers.CharField(required=False, allow_blank=True)


class ManagerAuthorizeSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    permission = serializers.CharField()
    manager_code = serializers.CharField()
