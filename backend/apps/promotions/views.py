from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.catalog.models import Category, Modifier, ModifierGroup, Product
from apps.core.audit import audit
from apps.core.tenancy import accessible_franchisee_ids, accessible_store_ids, is_hq
from apps.core.views import ContextMixin
from apps.orders.models import Order
from apps.organization.models import Franchisee, Store

from . import services as svc
from .models import ALL_TYPES, FranchisePromotionPolicy, Promotion, PromotionRedemption, PromotionType


class ItemSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["eligible", "excluded", "reward", "bundle", "modifier"])
    product = serializers.SlugRelatedField(slug_field="sku", queryset=Product.objects.all(), required=False,
                                           allow_null=True)
    category = serializers.SlugRelatedField(slug_field="code", queryset=Category.objects.all(), required=False,
                                            allow_null=True)
    modifier = serializers.SlugRelatedField(slug_field="code", queryset=Modifier.objects.all(), required=False,
                                            allow_null=True)
    modifier_group = serializers.SlugRelatedField(slug_field="code", queryset=ModifierGroup.objects.all(),
                                                  required=False, allow_null=True)
    size_code = serializers.CharField(required=False, allow_blank=True, default="")
    slot = serializers.IntegerField(required=False, default=0, min_value=0)
    quantity = serializers.IntegerField(required=False, default=1, min_value=1)

    def validate(self, attrs):
        if not any(attrs.get(k) for k in ("product", "category", "modifier", "modifier_group")):
            raise serializers.ValidationError("Each item needs a product, category, modifier or modifier_group")
        return attrs


class TargetSerializer(serializers.Serializer):
    franchisee = serializers.PrimaryKeyRelatedField(queryset=Franchisee.objects.all(), required=False, allow_null=True)
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all(), required=False, allow_null=True)


class RuleSerializer(serializers.Serializer):
    rule_type = serializers.ChoiceField(choices=["customer_phone", "customer_email", "customer_id"])
    value = serializers.CharField(max_length=200)


class CodeSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^[A-Za-z0-9\-_]{3,40}$")
    max_uses = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    is_active = serializers.BooleanField(required=False, default=True)


class PromotionSerializer(serializers.ModelSerializer):
    items = ItemSerializer(many=True, required=False, write_only=True)
    targets = TargetSerializer(many=True, required=False, write_only=True)
    rules = RuleSerializer(many=True, required=False, write_only=True)
    codes = CodeSerializer(many=True, required=False, write_only=True)
    item_list = serializers.SerializerMethodField()
    target_list = serializers.SerializerMethodField()
    rule_list = serializers.SerializerMethodField()
    code_list = serializers.SerializerMethodField()
    scope = serializers.CharField(read_only=True)
    redemption_count = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = ["id", "name", "description", "campaign", "promo_type", "franchisee", "store", "scope", "value",
                  "max_discount_cents", "min_subtotal_cents", "buy_quantity", "get_quantity", "reward_percent_bps",
                  "max_applications", "starts_at", "ends_at", "start_time", "end_time", "days_of_week", "channels",
                  "order_types", "max_redemptions", "max_per_customer", "auto_apply", "requires_code", "priority",
                  "combinable", "requires_manager", "is_enabled", "status", "items", "targets", "rules", "codes",
                  "item_list", "target_list", "rule_list", "code_list", "redemption_count", "created_at",
                  "updated_at"]
        read_only_fields = ["status", "created_at", "updated_at"]

    def get_item_list(self, obj):
        return [{"role": i.role, "product": i.product.sku if i.product else None,
                 "category": i.category.code if i.category else None,
                 "modifier": i.modifier.code if i.modifier else None,
                 "modifier_group": i.modifier_group.code if i.modifier_group else None, "size_code": i.size_code,
                 "slot": i.slot, "quantity": i.quantity}
                for i in obj.items.select_related("product", "category", "modifier", "modifier_group")]

    def get_target_list(self, obj):
        return [{"franchisee": t.franchisee_id, "store": t.store_id} for t in obj.targets.all()]

    def get_rule_list(self, obj):
        return [{"rule_type": r.rule_type, "value": r.value} for r in obj.rules.all()]

    def get_code_list(self, obj):
        return [{"code": c.code, "max_uses": c.max_uses, "is_active": c.is_active,
                 "uses": c.redemptions.filter(status="applied").count()} for c in obj.codes.all()]

    def get_redemption_count(self, obj):
        return obj.redemptions.filter(status=PromotionRedemption.STATUS_APPLIED).count()

    def validate_days_of_week(self, v):
        if any(d not in range(7) for d in v):
            raise serializers.ValidationError("Days are 0 (Mon) to 6 (Sun)")
        return v

    def validate(self, attrs):
        s, e = attrs.get("starts_at"), attrs.get("ends_at")
        if s and e and e <= s:
            raise serializers.ValidationError("ends_at must be after starts_at")
        if (attrs.get("start_time") is None) != (attrs.get("end_time") is None):
            raise serializers.ValidationError("start_time and end_time must be set together")
        t = attrs.get("promo_type", getattr(self.instance, "promo_type", None))
        if t == PromotionType.BUNDLE and not any(i["role"] == "bundle" for i in attrs.get("items", [])) and \
                not (self.instance and self.instance.items.filter(role="bundle").exists()):
            raise serializers.ValidationError("Bundles need at least one bundle slot item")
        if attrs.get("requires_code") and not attrs.get("codes") and not (self.instance and self.instance.codes.exists()):
            raise serializers.ValidationError("Code-based promotions need at least one code")
        return attrs


def _owner_scope(data, instance=None):
    store = data.get("store", instance.store if instance else None)
    franchisee = data.get("franchisee", instance.franchisee if instance else None)
    if store is not None:
        franchisee = store.franchisee
    return {"store": store, "franchisee_id": franchisee.id if franchisee else None}


class PromotionViewSet(ContextMixin, viewsets.ModelViewSet):
    """Promotions & discounts. HQ promotions can be published to selected franchisees/stores;
    franchisees (and authorized store managers) create local promotions within HQ policy."""

    serializer_class = PromotionSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        u = self.request.user
        qs = Promotion.objects.prefetch_related("targets", "rules", "codes").select_related("franchisee", "store")
        if not is_hq(u):
            fids = accessible_franchisee_ids(u) or set()
            sids = accessible_store_ids(u) or set()
            # Own-scope promotions plus HQ promotions published to (or open to) the user's stores.
            qs = qs.filter(Q(franchisee_id__in=fids, store__isnull=True) | Q(store_id__in=sids) |
                           Q(franchisee__isnull=True, store__isnull=True, targets__isnull=True) |
                           Q(franchisee__isnull=True, store__isnull=True, targets__franchisee_id__in=fids) |
                           Q(franchisee__isnull=True, store__isnull=True, targets__store_id__in=sids)).distinct()
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("scope") == "hq":
            qs = qs.filter(franchisee__isnull=True, store__isnull=True)
        if p.get("store"):
            qs = qs.filter(Q(store_id=p["store"]) | Q(store__isnull=True))
        return qs.order_by("-priority", "name")

    def _check(self, scope):
        u = self.request.user
        if not (u.has_permission("promotions.manage_network") or u.has_permission("promotions.manage_franchise")
                or u.has_permission("promotions.manage_store")):
            raise PermissionDenied("Missing promotion management permission")
        ok, why = svc.can_manage(u, scope)
        if not ok:
            raise PermissionDenied(why)

    def _nested(self, s):
        d = s.validated_data
        return {k: d.pop(k, None) for k in ("items", "targets", "rules", "codes")}

    def perform_create(self, serializer):
        scope = _owner_scope(serializer.validated_data)
        self._check(scope)
        nested = self._nested(serializer)
        promo = Promotion(**serializer.validated_data)
        if scope["store"] is not None:
            promo.franchisee_id = scope["store"].franchisee_id
        serializer.instance = svc.save_promotion(promo, self.request.user, is_new=True, **nested)

    def perform_update(self, serializer):
        scope = _owner_scope(serializer.validated_data, serializer.instance)
        self._check(_owner_scope({}, serializer.instance))
        self._check(scope)
        nested = self._nested(serializer)
        promo = serializer.instance
        for k, v in serializer.validated_data.items():
            setattr(promo, k, v)
        svc.save_promotion(promo, self.request.user, is_new=False, **nested)

    def perform_destroy(self, instance):
        self._check(_owner_scope({}, instance))
        if instance.redemptions.exists():
            instance.is_enabled = False
            instance.status = "disabled"
            instance.save(update_fields=["is_enabled", "status", "updated_at"])
            audit("promotion.disable", instance, entity_type="Promotion")
        else:
            audit("promotion.delete", instance, entity_type="Promotion", data={"name": instance.name})
            instance.delete()

    @extend_schema(request=None, responses=PromotionSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        return self._toggle(True)

    @extend_schema(request=None, responses=PromotionSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        return self._toggle(False)

    def _toggle(self, on: bool):
        p = self.get_object()
        if not (is_hq(self.request.user) and self.request.user.has_permission("promotions.manage_network")):
            self._check(_owner_scope({}, p))
        p.is_enabled = on
        p.status = p.derive_status()
        p.save(update_fields=["is_enabled", "status", "updated_at"])
        audit("promotion.enable" if on else "promotion.disable", p, entity_type="Promotion")
        return Response(PromotionSerializer(p).data)

    @extend_schema(request=TargetSerializer(many=True), responses=PromotionSerializer)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """Replace publication targets (``[{franchisee}, {store}]``; empty list = whole scope)."""
        p = self.get_object()
        self._check(_owner_scope({}, p))
        s = TargetSerializer(data=request.data.get("targets", []), many=True)
        s.is_valid(raise_exception=True)
        svc.save_promotion(p, request.user, targets=list(s.validated_data))
        audit("promotion.publish", p, entity_type="Promotion", data={"targets": [
            {"franchisee": t.get("franchisee").id if t.get("franchisee") else None,
             "store": t.get("store").id if t.get("store") else None} for t in s.validated_data]})
        return Response(PromotionSerializer(p).data)

    @extend_schema(parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str)], responses=dict)
    @action(detail=True, methods=["get"])
    def performance(self, request, pk=None):
        p = self.get_object()
        return Response(svc.performance(Promotion.objects.filter(pk=p.pk), _orders_in_scope(request)))

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="types")
    def types(self, request):
        u = request.user
        pol = FranchisePromotionPolicy.objects.filter(franchisee_id=u.franchisee_id).first() if u.franchisee_id \
            else None
        allowed = ALL_TYPES if is_hq(u) else (pol.allowed_types if pol and pol.can_create else [])
        return Response({"types": [{"value": t.value, "label": t.label, "allowed": t.value in allowed}
                                   for t in PromotionType]})


def _orders_in_scope(request):
    try:
        end = date.fromisoformat(request.query_params.get("to") or date.today().isoformat())
        start = date.fromisoformat(request.query_params.get("from") or (end - timedelta(days=29)).isoformat())
    except ValueError:
        raise ValidationError("Dates must be YYYY-MM-DD")
    qs = Order.objects.filter(business_date__gte=start, business_date__lte=end)
    ids = accessible_store_ids(request.user)
    if ids is not None:
        qs = qs.filter(store_id__in=ids)
    if request.query_params.get("store"):
        qs = qs.filter(store_id=request.query_params["store"])
    if request.query_params.get("franchisee"):
        qs = qs.filter(franchisee_id=request.query_params["franchisee"])
    return qs


class PromotionReportView(APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str),
                               OpenApiParameter("store", int), OpenApiParameter("franchisee", int),
                               OpenApiParameter("campaign", str)], responses=dict)
    def get(self, request):
        u = request.user
        if not any(u.has_permission(x) for x in ("reports.network", "reports.franchise", "reports.store",
                                                  "promotions.view")):
            raise PermissionDenied()
        orders = _orders_in_scope(request)
        promos = Promotion.objects.filter(redemptions__order__in=orders).distinct()
        if request.query_params.get("campaign"):
            promos = promos.filter(campaign=request.query_params["campaign"])
        return Response(svc.performance(promos, orders))


class PolicySerializer(serializers.ModelSerializer):
    franchisee_code = serializers.CharField(source="franchisee.code", read_only=True)

    class Meta:
        model = FranchisePromotionPolicy
        fields = ["franchisee", "franchisee_code", "can_create", "allowed_types", "max_percent_bps", "max_fixed_cents",
                  "store_managers_can_manage", "hq_allows_store_managers", "updated_at"]

    def validate_allowed_types(self, v):
        bad = set(v) - set(ALL_TYPES)
        if bad:
            raise serializers.ValidationError(f"Unknown types: {', '.join(bad)}")
        return v


class PolicyViewSet(ContextMixin, viewsets.ModelViewSet):
    """HQ sets what each franchisee may create; the franchisee decides whether store managers may
    manage promotions (only if HQ allows it)."""

    serializer_class = PolicySerializer
    permission_classes = [IsStaffUser]
    pagination_class = None
    lookup_field = "franchisee"
    http_method_names = ["get", "put", "patch"]

    def get_queryset(self):
        ids = accessible_franchisee_ids(self.request.user)
        for f in Franchisee.objects.filter(**({} if ids is None else {"id__in": ids})):
            FranchisePromotionPolicy.objects.get_or_create(franchisee=f)
        qs = FranchisePromotionPolicy.objects.select_related("franchisee")
        return qs if ids is None else qs.filter(franchisee_id__in=ids)

    def perform_update(self, serializer):
        u = self.request.user
        changed = set(serializer.validated_data)
        hq_fields = {"can_create", "allowed_types", "max_percent_bps", "max_fixed_cents", "hq_allows_store_managers"}
        if changed & hq_fields and not (is_hq(u) and u.has_permission("promotions.manage_network")):
            raise PermissionDenied("Only headquarters can change these limits")
        if "store_managers_can_manage" in changed:
            inst = serializer.instance
            if not (is_hq(u) or (u.franchisee_id == inst.franchisee_id and u.has_permission(
                    "promotions.manage_franchise"))):
                raise PermissionDenied()
            if serializer.validated_data["store_managers_can_manage"] and not inst.hq_allows_store_managers:
                raise PermissionDenied("Headquarters does not allow store-manager promotions for this franchisee")
        pol = serializer.save()
        audit("promotion_policy.update", pol.franchisee, entity_type="Franchisee", data=dict(self.request.data))
