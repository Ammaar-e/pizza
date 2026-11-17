from drf_spectacular.utils import extend_schema
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.core.audit import audit
from apps.core.tenancy import accessible_store_ids, is_hq, scope_franchisee_queryset
from apps.core.views import ContextMixin

from .models import Franchisee, Headquarters, Store
from .serializers import FranchiseeSerializer, HeadquartersSerializer, PublicStoreSerializer, StoreSerializer


class StaffOrScope(permissions.BasePermission):
    """Staff users, or API clients holding the required scope."""

    def has_permission(self, request, view):
        client = getattr(request._request, "api_client", None)
        if client is not None:
            scope = getattr(view, "client_scope", None)
            if callable(scope):
                scope = scope(request)
            write_ok = getattr(view, "client_write_allowed", False)
            return bool(scope) and client.has_scope(scope) and (request.method in permissions.SAFE_METHODS or write_ok)
        return bool(request.user and request.user.is_authenticated)


class HeadquartersView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(responses=HeadquartersSerializer)
    def get(self, request):
        hq = Headquarters.objects.first()
        return Response(HeadquartersSerializer(hq).data if hq else {})

    @extend_schema(request=HeadquartersSerializer, responses=HeadquartersSerializer)
    def patch(self, request):
        if not request.user.has_permission("org.manage_franchisees"):
            raise PermissionDenied()
        hq = Headquarters.objects.first()
        s = HeadquartersSerializer(hq, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        audit("headquarters.update", hq, data=request.data)
        return Response(s.data)


class FranchiseeViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = FranchiseeSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        return scope_franchisee_queryset(Franchisee.objects.all(), self.request.user, field="id")

    def _require(self):
        if not self.request.user.has_permission("org.manage_franchisees"):
            raise PermissionDenied("Only headquarters can manage franchisees")

    def perform_create(self, serializer):
        self._require()
        f = serializer.save(headquarters=Headquarters.objects.first())
        audit("franchisee.create", f)

    def perform_update(self, serializer):
        self._require()
        f = serializer.save()
        audit("franchisee.update", f, data=self.request.data)

    def perform_destroy(self, instance):
        self._require()
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        audit("franchisee.deactivate", instance)


class StoreViewSet(ContextMixin, viewsets.ModelViewSet):
    """Stores. Headquarters creates stores and assigns them to franchisees; franchisees configure them.

    External API clients (scope ``stores:read``) can list and read stores they are authorized for,
    retrieve the menu (scope ``menu:read``) and availability."""

    permission_classes = [StaffOrScope]
    http_method_names = ["get", "post", "patch", "delete"]

    def client_scope(self, request):
        return "menu:read" if self.action in ("menu", "availability", "quote") else "stores:read"

    def get_serializer_class(self):
        if getattr(self.request._request, "api_client", None) is not None:
            return PublicStoreSerializer
        return StoreSerializer

    def get_queryset(self):
        qs = Store.objects.select_related("franchisee__headquarters").prefetch_related("hours", "special_hours")
        ids = accessible_store_ids(self.request)
        if ids is not None:
            qs = qs.filter(id__in=ids)
        if self.request.query_params.get("franchisee"):
            qs = qs.filter(franchisee_id=self.request.query_params["franchisee"])
        return qs

    def perform_create(self, serializer):
        if not self.request.user.has_permission("org.manage_stores"):
            raise PermissionDenied("Only headquarters can create stores")
        store = serializer.save()
        from apps.kitchen.services import ensure_default_station

        ensure_default_station(store)
        audit("store.create", store)

    def perform_update(self, serializer):
        u = self.request.user
        if "franchisee" in serializer.validated_data and serializer.validated_data["franchisee"] != \
                serializer.instance.franchisee and not u.has_permission("org.manage_stores"):
            raise PermissionDenied("Only headquarters can reassign stores")
        if "number" in serializer.validated_data and serializer.validated_data["number"] != \
                serializer.instance.number and not u.has_permission("org.manage_stores"):
            raise PermissionDenied("Only headquarters can change store numbers")
        if not (u.has_permission("org.configure_store") or u.has_permission("org.manage_stores")):
            raise PermissionDenied("Missing permission: org.configure_store")
        store = serializer.save()
        audit("store.update", store, data={k: v for k, v in self.request.data.items() if k != "payment_secrets"})

    def perform_destroy(self, instance):
        if not self.request.user.has_permission("org.manage_stores"):
            raise PermissionDenied()
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        audit("store.deactivate", instance)

    @action(detail=True, methods=["get"])
    def menu(self, request, pk=None):
        """Full store menu with sizes, crusts, modifier groups and prices. ``?channel=`` for channel pricing."""
        from apps.catalog.engine import build_store_menu

        store = self.get_object()
        return Response(build_store_menu(store, channel=request.query_params.get("channel")))

    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        from apps.catalog.models import StoreModifierAvailability, StoreProduct

        store = self.get_object()
        return Response({
            "store_id": store.id,
            "is_open_now": store.is_open(),
            "is_temporarily_closed": store.is_temporarily_closed,
            "unavailable_products": list(StoreProduct.objects.filter(store=store, enabled=True, is_available=False)
                                         .values_list("product__sku", flat=True)),
            "unavailable_modifiers": list(StoreModifierAvailability.objects.filter(store=store, is_available=False)
                                          .values_list("modifier__code", flat=True)),
            "delivery_enabled": bool((store.delivery_config or {}).get("enabled")),
            "pickup_enabled": bool((store.pickup_config or {}).get("enabled", True)),
        })

    @action(detail=True, methods=["post"], url_path="payment-secrets", permission_classes=[IsStaffUser])
    def payment_secrets(self, request, pk=None):
        """Write-only: set card-processor secrets (encrypted at rest, never returned)."""
        store = self.get_object()
        if not request.user.has_permission("org.configure_store"):
            raise PermissionDenied()
        secrets = dict(store.payment_secrets or {})
        for k, v in request.data.items():
            if v in (None, ""):
                secrets.pop(k, None)
            else:
                secrets[k] = str(v)
        store.payment_secrets = secrets
        store.save(update_fields=["payment_secrets", "updated_at"])
        audit("store.payment_secrets_updated", store, data={"keys": sorted(secrets)})
        return Response({"configured": sorted(secrets)})


class NetworkSummaryView(APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(responses=dict)
    def get(self, request):
        if not is_hq(request.user):
            raise PermissionDenied()
        data = []
        for f in Franchisee.objects.prefetch_related("stores"):
            data.append({"id": f.id, "code": f.code, "name": f.name, "is_active": f.is_active,
                         "stores": [{"id": s.id, "number": s.number, "name": s.name, "is_active": s.is_active,
                                     "is_open_now": s.is_open()} for s in f.stores.all()]})
        return Response(data)
