from __future__ import annotations

import base64
import binascii
import uuid
from datetime import date, timedelta

from django.core.files.base import ContentFile
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import IsStaffUser
from apps.accounts.services import user_can_operate_store
from apps.core.audit import audit
from apps.core.tenancy import accessible_store_ids, can_access_store
from apps.core.views import ContextMixin
from apps.organization.models import Store

from . import services as svc
from .models import DeliveryAssignment, DeliveryZone, DriverProfile


def _store(request, store_id, perm="dispatch.view") -> Store:
    store = Store.objects.filter(pk=store_id).first()
    if store is None or not can_access_store(request.user, store):
        raise NotFound("Store not found")
    if not request.user.has_permission(perm):
        raise PermissionDenied(f"Missing permission: {perm}")
    pinned = getattr(request, "pos_store_id", None)
    if pinned and pinned != store.id:
        raise PermissionDenied("Session is pinned to another store")
    return store


class DriverSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    name = serializers.SerializerMethodField()
    stores = serializers.SerializerMethodField()
    active_deliveries = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = DriverProfile
        fields = ["id", "user", "username", "name", "phone", "vehicle", "license_plate", "is_active",
                  "max_active_deliveries", "status", "current_store", "status_changed_at", "stores",
                  "active_deliveries", "notes"]
        read_only_fields = ["status", "status_changed_at"]

    def get_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_stores(self, obj):
        return sorted(obj.store_ids())

    def get_active_deliveries(self, obj):
        return svc.active_load(obj)


class DriverViewSet(ContextMixin, viewsets.ModelViewSet):
    """Driver profiles. A driver is an employee (role ``delivery``) whose authorized stores are
    their store assignments."""

    serializer_class = DriverSerializer
    permission_classes = [IsStaffUser]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        u = self.request.user
        if not (u.has_permission("dispatch.view") or u.has_permission("drivers.manage")):
            raise PermissionDenied()
        ids = accessible_store_ids(u)
        qs = DriverProfile.objects.select_related("user")
        if ids is not None:
            qs = qs.filter(user__store_assignments__store_id__in=ids, user__store_assignments__active=True).distinct()
        if self.request.query_params.get("store"):
            qs = qs.filter(user__store_assignments__store_id=self.request.query_params["store"]).distinct()
        return qs.order_by("user__username")

    def _check_user(self, user):
        u = self.request.user
        if not u.has_permission("drivers.manage"):
            raise PermissionDenied("Missing permission: drivers.manage")
        if not user.has_permission("delivery.access"):
            raise ValidationError({"user": "User must have the delivery driver role"})
        ids = accessible_store_ids(u)
        stores = set(user.store_assignments.filter(active=True).values_list("store_id", flat=True))
        if not stores or (ids is not None and not stores <= ids):
            raise PermissionDenied("Driver must be assigned only to stores you manage")

    def perform_create(self, serializer):
        self._check_user(serializer.validated_data["user"])
        audit("driver.create", serializer.save(), entity_type="DriverProfile")

    def perform_update(self, serializer):
        self._check_user(serializer.instance.user)
        audit("driver.update", serializer.save(), entity_type="DriverProfile", data=dict(self.request.data))

    @extend_schema(request=dict, responses=DriverSerializer)
    @action(detail=True, methods=["post"])
    def availability(self, request, pk=None):
        """``{status: available|unavailable|on_break|off_shift, store}`` (dispatchers)."""
        d = self.get_object()
        if not request.user.has_permission("dispatch.manage"):
            raise PermissionDenied()
        store = Store.objects.filter(pk=request.data.get("store")).first() if request.data.get("store") else None
        if store is not None and not can_access_store(request.user, store):
            raise PermissionDenied()
        return Response(DriverSerializer(svc.set_driver_status(d, request.data.get("status"), request.user,
                                                               store)).data)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        d = self.get_object()
        return Response({
            "availability": [{"status": a.status, "store": a.store_id, "at": a.created_at,
                              "by": a.changed_by.username if a.changed_by else None}
                             for a in d.availability_log.select_related("changed_by")[:100]],
            "deliveries": [svc.summary(a) for a in d.deliveries.select_related("order", "driver__user", "zone")
                           .order_by("-created_at")[:100]],
        })


class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = ["id", "store", "name", "min_distance_km", "max_distance_km", "postal_prefixes", "fee_cents",
                  "min_order_cents", "estimated_minutes", "is_active", "sort_order"]


class ZoneViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = ZoneSerializer
    permission_classes = [IsStaffUser]
    pagination_class = None

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = DeliveryZone.objects.all()
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if self.request.query_params.get("store"):
            qs = qs.filter(store_id=self.request.query_params["store"])
        return qs

    def _check(self, store):
        if not (self.request.user.has_permission("org.configure_store") and can_access_store(self.request.user,
                                                                                               store)):
            raise PermissionDenied("Missing permission: org.configure_store")

    def perform_create(self, serializer):
        self._check(serializer.validated_data["store"])
        audit("delivery_zone.create", serializer.save(), entity_type="DeliveryZone")

    def perform_update(self, serializer):
        self._check(serializer.instance.store)
        audit("delivery_zone.update", serializer.save(), entity_type="DeliveryZone")

    def perform_destroy(self, instance):
        self._check(instance.store)
        audit("delivery_zone.delete", instance, entity_type="DeliveryZone")
        instance.delete()


class DispatchBoardView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(parameters=[OpenApiParameter("status", str), OpenApiParameter("driver", int),
                               OpenApiParameter("source", str), OpenApiParameter("hours", int)], responses=dict)
    def get(self, request, store_id):
        store = _store(request, store_id)
        return Response({"store": {"id": store.id, "number": store.number,
                                   "auto_assign": bool((store.delivery_config or {}).get("auto_assign")),
                                   "proof_of_delivery": svc.proof_required(store)},
                         **svc.dispatch_board(store, request.query_params)})


class DeliveryViewSet(ContextMixin, viewsets.GenericViewSet):
    """Dispatcher actions on a store delivery."""

    permission_classes = [IsStaffUser]

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = DeliveryAssignment.objects.select_related("order", "store", "driver__user", "zone")
        return qs if ids is None else qs.filter(store_id__in=ids)

    def _get(self, pk, perm="dispatch.manage"):
        a = self.get_queryset().filter(pk=pk).first()
        if a is None:
            raise NotFound()
        if not self.request.user.has_permission(perm):
            raise PermissionDenied(f"Missing permission: {perm}")
        return a

    @extend_schema(responses=dict)
    def retrieve(self, request, pk=None):
        a = self._get(pk, "dispatch.view")
        return Response({**svc.summary(a), "history": [
            {"from": h.from_status, "to": h.to_status, "at": h.created_at, "actor": h.actor_label, "note": h.note,
             "driver": h.driver.user.username if h.driver else None,
             "previous_driver": h.previous_driver.user.username if h.previous_driver else None}
            for h in a.history.select_related("driver__user", "previous_driver__user")],
            "proofs": [{"kind": p.kind, "recipient_name": p.recipient_name, "note": p.note, "at": p.created_at,
                        "image_url": p.image.url if p.image else None} for p in a.proofs.all()]})

    @extend_schema(request=dict, responses=dict)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """``{driver, version?}`` assign or reassign; ``version`` guards against concurrent dispatchers."""
        a = self._get(pk)
        d = DriverProfile.objects.filter(pk=request.data.get("driver")).select_related("user").first()
        if d is None:
            raise ValidationError({"driver": "Unknown driver"})
        a = svc.assign(a, d, request.user, expected_version=request.data.get("version"))
        return Response(svc.summary(a))

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"])
    def unassign(self, request, pk=None):
        return Response(svc.summary(svc.unassign(self._get(pk), request.user)))

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"], url_path="auto-assign")
    def auto_assign(self, request, pk=None):
        return Response(svc.summary(svc.auto_assign(self._get(pk), request.user)))

    @extend_schema(request=dict, responses=dict)
    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        """Dispatcher status change (e.g. ``ready_for_dispatch`` to re-dispatch a failed delivery,
        ``returned`` to cancel it, or recording pickup/delivery on the driver's behalf)."""
        a = self._get(pk)
        a = svc.update_status(a, request.data.get("status"), request.user, by_driver=False,
                              note=request.data.get("note", ""), failure_reason=request.data.get("failure_reason", ""))
        return Response(svc.summary(a))


class AssignManyView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(request=dict, responses=dict)
    def post(self, request, store_id):
        """Assign several deliveries to one driver: ``{driver, deliveries: [ids]}``."""
        store = _store(request, store_id, "dispatch.manage")
        d = DriverProfile.objects.filter(pk=request.data.get("driver")).first()
        if d is None:
            raise ValidationError({"driver": "Unknown driver"})
        results = []
        for pk in request.data.get("deliveries", []):
            a = DeliveryAssignment.objects.filter(pk=pk, store=store).first()
            if a is None:
                results.append({"id": pk, "ok": False, "error": "not found"})
                continue
            try:
                svc.assign(a, d, request.user)
                results.append({"id": pk, "ok": True})
            except Exception as e:  # report per-delivery outcome
                results.append({"id": pk, "ok": False, "error": str(getattr(e, "detail", e))})
        return Response({"results": results})


# ---------------------------------------------------------------------------
# Driver app
# ---------------------------------------------------------------------------
def _me(request) -> DriverProfile:
    if not request.user.has_permission("delivery.access"):
        raise PermissionDenied("Driver access only")
    d = DriverProfile.objects.filter(user=request.user, is_active=True).select_related("user").first()
    if d is None:
        raise PermissionDenied("No active driver profile for this account")
    return d


def _driver_view(a: DeliveryAssignment) -> dict:
    o = a.order
    addr = o.delivery_address or {}
    query = ", ".join(x for x in [addr.get("line1"), addr.get("city"), addr.get("postal_code")] if x)
    return {**svc.summary(a), "special_instructions": o.special_instructions,
            "delivery_instructions": addr.get("instructions", ""),
            "maps_url": "https://www.google.com/maps/dir/?api=1&destination=" + (
                f"{addr['latitude']},{addr['longitude']}" if addr.get("latitude") else query.replace(" ", "+")),
            "store": {"number": a.store.number, "name": a.store.name, "phone": a.store.phone,
                      "address": a.store.address_line1},
            "items": [{"quantity": i.quantity, "name": f"{i.size_name} {i.name}".strip()} for i in o.items.all()],
            "amount_due_cents": max(0, o.total_cents - o.amount_paid_cents),
            "proof_required": svc.proof_required(a.store)}


class DriverMeView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(responses=dict)
    def get(self, request):
        """The signed-in driver's profile and their own active and recent deliveries only."""
        d = _me(request)
        qs = d.deliveries.select_related("order", "store", "zone", "driver__user").order_by("-assigned_at")
        active = [_driver_view(a) for a in qs.filter(status__in=["assigned", "picked_up", "out_for_delivery",
                                                                   "failed"])]
        recent = [svc.summary(a) for a in qs.filter(status__in=["delivered", "returned", "cancelled"])[:20]]
        return Response({"driver": DriverSerializer(d).data, "active": active, "recent": recent})

    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        """Driver sets own availability: ``{status}`` (store = the POS-pinned store)."""
        d = _me(request)
        store = Store.objects.filter(pk=getattr(request, "pos_store_id", None) or d.current_store_id).first()
        return Response(DriverSerializer(svc.set_driver_status(d, request.data.get("status"), request.user,
                                                               store)).data)


class DriverDeliveryView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    def _mine(self, request, pk) -> DeliveryAssignment:
        d = _me(request)
        a = DeliveryAssignment.objects.select_related("order", "store", "driver__user", "zone").filter(
            pk=pk, driver=d).first()
        if a is None:
            raise NotFound("Delivery not found")  # never reveal other drivers' deliveries
        return a

    @extend_schema(responses=dict)
    def get(self, request, pk):
        return Response(_driver_view(self._mine(request, pk)))

    @extend_schema(request=dict, responses=dict)
    def post(self, request, pk, op):
        a = self._mine(request, pk)
        if op == "status":
            a = svc.update_status(a, request.data.get("status"), request.user, by_driver=True,
                                  note=request.data.get("note", ""),
                                  failure_reason=request.data.get("failure_reason", ""),
                                  lat=request.data.get("latitude"), lng=request.data.get("longitude"))
            return Response(_driver_view(a))
        if op == "proof":
            image = request.FILES.get("image")
            data_url = request.data.get("image_data")
            if image is None and data_url:
                try:
                    header, b64 = data_url.split(",", 1)
                    ext = "png" if "png" in header else "jpg"
                    raw = base64.b64decode(b64)
                except (ValueError, binascii.Error):
                    raise ValidationError({"image_data": "Invalid data URL"})
                if len(raw) > 4 * 1024 * 1024:
                    raise ValidationError({"image_data": "Image too large (max 4 MB)"})
                image = ContentFile(raw, name=f"{uuid.uuid4().hex}.{ext}")
            p = svc.add_proof(a, request.user, kind=request.data.get("kind", "photo"), image=image,
                              recipient_name=request.data.get("recipient_name", ""), note=request.data.get("note", ""),
                              lat=request.data.get("latitude"), lng=request.data.get("longitude"))
            return Response({"id": p.id, "kind": p.kind}, status=201)
        raise NotFound()


class DriverReportView(APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str),
                               OpenApiParameter("store", int)], responses=dict)
    def get(self, request):
        u = request.user
        if not (u.has_permission("dispatch.view") or u.has_permission("reports.franchise")):
            raise PermissionDenied()
        end = date.fromisoformat(request.query_params.get("to") or date.today().isoformat())
        start = date.fromisoformat(request.query_params.get("from") or (end - timedelta(days=29)).isoformat())
        qs = DeliveryAssignment.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
        ids = accessible_store_ids(u)
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if request.query_params.get("store"):
            qs = qs.filter(store_id=request.query_params["store"])
        return Response({"range": {"from": start, "to": end}, "drivers": svc.driver_performance(qs)})
