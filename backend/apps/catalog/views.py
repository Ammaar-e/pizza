from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.accounts.services import user_can_operate_store
from apps.core.audit import audit
from apps.core.tenancy import accessible_franchisee_ids, can_access_store, is_hq
from apps.core.views import ContextMixin
from apps.organization.models import Store
from apps.organization.views import StaffOrScope

from . import models as m
from . import serializers as s
from .engine import LineValidationError, applicable_charges, compute_tax, price_line, tax_rates_for
from .services import record_version, review_request, snapshot_product, validate_store_pricing


class HQWritePermission(permissions.BasePermission):
    """Read for authenticated staff (and API clients with menu:read); write requires catalog.manage_master."""

    def has_permission(self, request, view):
        client = getattr(request._request, "api_client", None)
        if request.method in permissions.SAFE_METHODS:
            return (client.has_scope("menu:read")) if client else bool(request.user and request.user.is_authenticated)
        return client is None and request.user.is_authenticated and request.user.has_permission("catalog.manage_master")


class AuditedModelViewSet(ContextMixin, viewsets.ModelViewSet):
    audit_name = "catalog"

    def perform_create(self, serializer):
        obj = serializer.save()
        audit(f"{self.audit_name}.create", obj)

    def perform_update(self, serializer):
        obj = serializer.save()
        audit(f"{self.audit_name}.update", obj, data={k: v for k, v in self.request.data.items()
                                                      if not hasattr(v, "read")})


def simple_viewset(model, serializer, name, lookup="pk"):
    class VS(AuditedModelViewSet):
        queryset = model.objects.all()
        serializer_class = serializer
        permission_classes = [HQWritePermission]
        audit_name = name
        pagination_class = None

    VS.__name__ = f"{model.__name__}ViewSet"
    return VS


CategoryViewSet = simple_viewset(m.Category, s.CategorySerializer, "category")
CrustViewSet = simple_viewset(m.Crust, s.CrustSerializer, "crust")
PrepCategoryViewSet = simple_viewset(m.PrepCategory, s.PrepCategorySerializer, "prep_category")
TaxCategoryViewSet = simple_viewset(m.TaxCategory, s.TaxCategorySerializer, "tax_category")
TaxRateViewSet = simple_viewset(m.TaxRate, s.TaxRateSerializer, "tax_rate")
ModifierViewSet = simple_viewset(m.Modifier, s.ModifierSerializer, "modifier")
ChargeViewSet = simple_viewset(m.AdditionalCharge, s.AdditionalChargeSerializer, "charge")


class ModifierGroupViewSet(AuditedModelViewSet):
    queryset = m.ModifierGroup.objects.prefetch_related("modifiers")
    serializer_class = s.ModifierGroupSerializer
    permission_classes = [HQWritePermission]
    audit_name = "modifier_group"
    pagination_class = None


class ProductViewSet(AuditedModelViewSet):
    """Master product catalogue. Only headquarters can create or change products."""

    serializer_class = s.ProductSerializer
    permission_classes = [HQWritePermission]
    audit_name = "product"

    def get_queryset(self):
        qs = m.Product.objects.select_related("category", "default_crust").prefetch_related("sizes", "crusts")
        u = self.request.user
        if getattr(self.request._request, "api_client", None) is not None or not is_hq(u):
            qs = qs.filter(status=m.Product.STATUS_APPROVED)
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        if self.request.query_params.get("category"):
            qs = qs.filter(category__code=self.request.query_params["category"])
        return qs

    def perform_create(self, serializer):
        p = serializer.save()
        record_version(m.CatalogVersion.KIND_PRODUCT, p.pk, snapshot_product(p), self.request.user)
        audit("product.create", p)

    def perform_update(self, serializer):
        p = serializer.save()
        record_version(m.CatalogVersion.KIND_PRODUCT, p.pk, snapshot_product(p), self.request.user)
        audit("product.update", p, data={"version": p.version})

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        p = self.get_object()
        qs = m.CatalogVersion.objects.filter(kind=m.CatalogVersion.KIND_PRODUCT, entity_id=str(p.pk))
        return Response(s.CatalogVersionSerializer(qs, many=True).data)


class DiscountViewSet(AuditedModelViewSet):
    serializer_class = s.DiscountSerializer
    permission_classes = [IsStaffUser]
    audit_name = "discount"

    def get_queryset(self):
        ids = accessible_franchisee_ids(self.request.user)
        qs = m.Discount.objects.all()
        if ids is not None:
            qs = qs.filter(franchisee__isnull=True) | qs.filter(franchisee_id__in=ids)
        return qs

    def _check(self, franchisee):
        u = self.request.user
        if franchisee is None and not u.has_permission("catalog.manage_master"):
            raise PermissionDenied("Only headquarters can create network-wide discounts")
        if franchisee is not None and not (is_hq(u) or (u.franchisee_id == franchisee.id and u.has_permission(
                "catalog.configure_store_menu"))):
            raise PermissionDenied()

    def perform_create(self, serializer):
        self._check(serializer.validated_data.get("franchisee"))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check(serializer.validated_data.get("franchisee", serializer.instance.franchisee))
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        self._check(instance.franchisee)
        instance.is_active = False
        instance.save(update_fields=["is_active"])


class ProductRequestViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = s.ProductRequestSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post"]

    def get_queryset(self):
        ids = accessible_franchisee_ids(self.request.user)
        qs = m.ProductRequest.objects.select_related("franchisee", "submitted_by")
        return qs if ids is None else qs.filter(franchisee_id__in=ids)

    def perform_create(self, serializer):
        u = self.request.user
        if not u.has_permission("catalog.request_products") or not u.franchisee_id:
            raise PermissionDenied("Only franchise users can submit product requests")
        store = serializer.validated_data.get("store")
        if store and store.franchisee_id != u.franchisee_id:
            raise PermissionDenied("Store belongs to another franchisee")
        req = serializer.save(franchisee_id=u.franchisee_id, submitted_by=u)
        audit("product_request.create", req)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not request.user.has_permission("catalog.approve_requests"):
            raise PermissionDenied()
        req = self.get_object()
        product = None
        if request.data.get("product"):
            product = m.Product.objects.filter(pk=request.data["product"]).first()
        review_request(req, request.user, True, request.data.get("notes", ""), product)
        return Response(s.ProductRequestSerializer(req).data)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        if not request.user.has_permission("catalog.approve_requests"):
            raise PermissionDenied()
        req = self.get_object()
        review_request(req, request.user, False, request.data.get("notes", ""))
        return Response(s.ProductRequestSerializer(req).data)


def _store_for(request, store_id, perm: str | None = None) -> Store:
    store = Store.objects.filter(pk=store_id).select_related("franchisee__headquarters").first()
    if store is None or not can_access_store(request, store):
        raise PermissionDenied("Store not accessible")
    if perm and not request.user.has_permission(perm):
        raise PermissionDenied(f"Missing permission: {perm}")
    return store


class StoreMenuConfigView(ContextMixin, APIView):
    """Franchisee menu configuration for a store: enable approved products and set permitted prices."""

    permission_classes = [IsStaffUser]

    def get(self, request, store_id):
        store = _store_for(request, store_id)
        existing = {sp.product_id: sp for sp in m.StoreProduct.objects.filter(store=store).select_related(
            "product__category").prefetch_related("product__sizes")}
        rows = []
        for p in m.Product.objects.filter(status=m.Product.STATUS_APPROVED).select_related("category") \
                .prefetch_related("sizes"):
            sp = existing.get(p.id) or m.StoreProduct(store=store, product=p, enabled=False)
            rows.append(s.StoreProductSerializer(sp).data)
        mods = [{"modifier": md.id, "code": md.code, "name": md.name, "group": md.group.name,
                 "is_available": md.id not in set(m.StoreModifierAvailability.objects.filter(
                     store=store, is_available=False).values_list("modifier_id", flat=True))}
                for md in m.Modifier.objects.select_related("group").filter(is_active=True)]
        return Response({"products": rows, "modifiers": mods})

    @extend_schema(request=dict, responses=dict)
    @transaction.atomic
    def patch(self, request, store_id):
        """Body: {"products": [{product, enabled?, price_cents?, size_prices?, channel_prices?, is_available?}]}"""
        store = _store_for(request, store_id, "catalog.configure_store_menu")
        updated = []
        for row in request.data.get("products", []):
            product = m.Product.objects.filter(pk=row.get("product"), status=m.Product.STATUS_APPROVED).first()
            if product is None:
                raise ValidationError({"product": f"Product {row.get('product')} is not approved by headquarters"})
            sp, _ = m.StoreProduct.objects.get_or_create(store=store, product=product,
                                                         defaults={"enabled": False})
            before = {"price_cents": sp.price_cents, "size_prices": sp.size_prices, "channel_prices": sp.channel_prices}
            for f in ("enabled", "is_available", "price_cents", "size_prices", "channel_prices"):
                if f in row:
                    setattr(sp, f, row[f])
            validate_store_pricing(sp)
            sp.save()
            after = {"price_cents": sp.price_cents, "size_prices": sp.size_prices, "channel_prices": sp.channel_prices}
            if before != after:
                record_version(m.CatalogVersion.KIND_STORE_PRICE, sp.pk, after, request.user, store)
            updated.append(sp.id)
        audit("store_menu.update", store, data={"store_products": updated})
        return Response({"updated": updated})


class StoreAvailabilityView(ContextMixin, APIView):
    """86 / un-86 products and modifiers (POS and portals)."""

    permission_classes = [IsStaffUser]

    @extend_schema(request=dict, responses=dict)
    def post(self, request, store_id):
        store = _store_for(request, store_id, "catalog.set_availability")
        if not user_can_operate_store(request.user, store):
            raise PermissionDenied()
        changes = []
        for row in request.data.get("products", []):
            n = m.StoreProduct.objects.filter(store=store, product__sku=row["sku"]).update(
                is_available=bool(row["is_available"]))
            if n:
                changes.append(("product", row["sku"], row["is_available"]))
        for row in request.data.get("modifiers", []):
            mod = m.Modifier.objects.filter(code=row["code"]).first()
            if mod:
                m.StoreModifierAvailability.objects.update_or_create(
                    store=store, modifier=mod, defaults={"is_available": bool(row["is_available"])})
                changes.append(("modifier", row["code"], row["is_available"]))
        audit("store_availability.update", store, data={"changes": changes})
        return Response({"changes": changes})


class PublishMenuView(ContextMixin, APIView):
    """Snapshot the store's current menu as a new version (and optionally sync to providers)."""

    permission_classes = [IsStaffUser]

    def post(self, request, store_id):
        from .engine import build_store_menu

        store = _store_for(request, store_id, "catalog.configure_store_menu")
        v = record_version(m.CatalogVersion.KIND_MENU, store.pk, build_store_menu(store), request.user, store,
                           note=request.data.get("note", ""))
        audit("menu.publish", store, data={"version": v.version})
        return Response({"version": v.version, "created_at": v.created_at})

    def get(self, request, store_id):
        store = _store_for(request, store_id)
        qs = m.CatalogVersion.objects.filter(kind=m.CatalogVersion.KIND_MENU, entity_id=str(store.pk))[:50]
        return Response([{"version": v.version, "created_at": v.created_at, "note": v.note,
                          "changed_by": v.changed_by_id} for v in qs])


class QuoteView(ContextMixin, APIView):
    """Price and validate order lines without creating an order (POS, call centre, external apps)."""

    permission_classes = [StaffOrScope]
    client_scope = "menu:read"
    client_write_allowed = True  # quoting is read-only despite using POST

    @extend_schema(request=s.QuoteLineSerializer(many=True), responses=dict)
    def post(self, request, store_id):
        store = Store.objects.filter(pk=store_id).select_related("franchisee__headquarters").first()
        if store is None or not can_access_store(request, store):
            raise PermissionDenied("Store not accessible")
        lines = s.QuoteLineSerializer(data=request.data.get("items", []), many=True)
        lines.is_valid(raise_exception=True)
        out, issues, subtotal, tax = [], [], 0, 0
        for idx, line in enumerate(lines.validated_data):
            try:
                pl = price_line(store, line, channel=request.data.get("channel"))
                subtotal += pl.line_total_cents
                tax += pl.tax_cents
                out.append({"product": pl.product.sku, "name": pl.product.name,
                            "size": pl.size.code if pl.size else None, "crust": pl.crust.code if pl.crust else None,
                            "quantity": pl.quantity, "unit_price_cents": pl.unit_price_cents,
                            "line_total_cents": pl.line_total_cents, "tax_cents": pl.tax_cents,
                            "modifiers": [x.as_dict() for x in pl.modifiers],
                            "removed": [r.code for r in pl.removed]})
            except LineValidationError as e:
                issues += [{**i.as_dict(), "line": idx} for i in e.issues]
        charges = applicable_charges(store, request.data.get("order_type", "pickup"))
        for c in charges:
            tc = c.pop("tax_category_id", None)
            if tc:
                tax += compute_tax(c["amount_cents"], tax_rates_for(store, tc))[0]
        ch_total = sum(c["amount_cents"] for c in charges)
        return Response({"valid": not issues, "issues": issues, "lines": out, "subtotal_cents": subtotal,
                         "charges": charges, "tax_cents": tax, "total_cents": subtotal + tax + ch_total})
