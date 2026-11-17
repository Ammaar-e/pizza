from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import IsStaffUser
from apps.accounts.services import user_can_operate_store
from apps.core.audit import audit
from apps.core.tenancy import accessible_store_ids
from apps.core.views import ContextMixin

from .models import KitchenStation, KitchenTicket, TicketStatus
from .services import bump_ticket, ticket_payload


class KitchenStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = KitchenStation
        fields = ["id", "store", "code", "name", "prep_categories", "is_expo", "is_active", "sort_order"]


class KitchenStationViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = KitchenStationSerializer
    permission_classes = [IsStaffUser]
    pagination_class = None

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = KitchenStation.objects.all()
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if self.request.query_params.get("store"):
            qs = qs.filter(store_id=self.request.query_params["store"])
        return qs

    def _check(self, store):
        u = self.request.user
        if not user_can_operate_store(u, store) or not u.has_permission("org.configure_store") and not \
                u.has_permission("org.manage_devices"):
            raise PermissionDenied()

    def perform_create(self, serializer):
        self._check(serializer.validated_data["store"])
        audit("kitchen_station.create", serializer.save())

    def perform_update(self, serializer):
        self._check(serializer.instance.store)
        audit("kitchen_station.update", serializer.save())


class KitchenTicketViewSet(ContextMixin, viewsets.GenericViewSet):
    """Kitchen display tickets. Initial state is delivered over WebSocket; this endpoint gives a snapshot."""

    permission_classes = [IsStaffUser]

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = KitchenTicket.objects.select_related("order__store", "station")
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        return qs

    def list(self, request):
        if not request.user.has_permission("kitchen.access") and not request.user.has_permission("orders.view"):
            raise PermissionDenied()
        store = request.query_params.get("store") or getattr(request, "pos_store_id", None)
        if not store:
            raise ValidationError({"store": "Required"})
        qs = self.get_queryset().filter(store_id=store)
        if request.query_params.get("station"):
            qs = qs.filter(station__code=request.query_params["station"])
        if request.query_params.get("include_completed") != "1":
            qs = qs.exclude(status__in=[TicketStatus.COMPLETED, TicketStatus.VOID])
        return Response([ticket_payload(t) for t in qs.order_by("received_at")[:200]])

    @extend_schema(request=dict, responses=dict)
    @action(detail=True, methods=["post"])
    def bump(self, request, pk=None):
        if not request.user.has_permission("kitchen.access"):
            raise PermissionDenied()
        t = self.get_queryset().filter(pk=pk).first()
        if t is None:
            raise ValidationError("Ticket not found")
        target = request.data.get("status")
        if target not in TicketStatus.values:
            raise ValidationError({"status": f"One of {TicketStatus.values}"})
        t = bump_ticket(t, target, request.user)
        return Response(ticket_payload(t))
