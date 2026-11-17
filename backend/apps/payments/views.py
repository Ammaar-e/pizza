from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import IsStaffUser
from apps.accounts.services import authorize_manager, user_can_operate_store
from apps.core.audit import audit
from apps.core.tenancy import accessible_store_ids
from apps.core.views import ContextMixin
from apps.organization.models import Store

from .models import CashMovement, CashSession
from .services import close_cash_session, expected_cash, open_cash_session


class CashSessionSerializer(serializers.ModelSerializer):
    expected_now_cents = serializers.SerializerMethodField()
    opened_by_name = serializers.CharField(source="opened_by.username", read_only=True)

    class Meta:
        model = CashSession
        fields = ["id", "store", "device", "status", "opened_by", "opened_by_name", "opened_at",
                  "opening_float_cents", "closed_by", "closed_at", "counted_cash_cents", "expected_cash_cents",
                  "variance_cents", "notes", "expected_now_cents"]
        read_only_fields = fields

    def get_expected_now_cents(self, obj):
        return expected_cash(obj) if obj.status == CashSession.STATUS_OPEN else obj.expected_cash_cents


class CashSessionViewSet(ContextMixin, viewsets.ReadOnlyModelViewSet):
    """Cash register sessions per POS device: open with a float, record paid in/out, close with a count."""

    serializer_class = CashSessionSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = CashSession.objects.select_related("opened_by")
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if self.request.query_params.get("store"):
            qs = qs.filter(store_id=self.request.query_params["store"])
        return qs.order_by("-opened_at")

    @action(detail=False, methods=["get"])
    def current(self, request):
        device = getattr(request, "device", None)
        s = CashSession.objects.filter(device=device, status=CashSession.STATUS_OPEN).first() if device else None
        return Response(CashSessionSerializer(s).data if s else None)

    @extend_schema(request=dict, responses=CashSessionSerializer)
    @action(detail=False, methods=["post"])
    def open(self, request):
        if not request.user.has_permission("cash.manage_session"):
            raise PermissionDenied()
        device = getattr(request, "device", None)
        store = Store.objects.filter(pk=request.data.get("store") or getattr(device, "store_id", None)).first()
        if store is None or not user_can_operate_store(request.user, store):
            raise PermissionDenied("Store not accessible")
        if device is None:
            raise ValidationError("Cash sessions must be opened on a registered POS device")
        s = open_cash_session(store, device, request.user, int(request.data.get("opening_float_cents", 0)))
        return Response(CashSessionSerializer(s).data, status=201)

    @extend_schema(request=dict, responses=CashSessionSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        session = self.get_object()
        if not request.user.has_permission("cash.manage_session"):
            raise PermissionDenied()
        if "counted_cash_cents" not in request.data:
            raise ValidationError({"counted_cash_cents": "Required"})
        s = close_cash_session(session, request.user, int(request.data["counted_cash_cents"]),
                               request.data.get("notes", ""))
        return Response(CashSessionSerializer(s).data)

    @extend_schema(request=dict, responses=dict)
    @action(detail=True, methods=["post"])
    def movements(self, request, pk=None):
        session = self.get_object()
        if session.status != CashSession.STATUS_OPEN:
            raise ValidationError("Session is closed")
        mgr = authorize_manager(session.store, request.data.get("manager_code"), "cash.paid_in_out", request.user)
        kind = request.data.get("kind")
        if kind not in (CashMovement.KIND_PAID_IN, CashMovement.KIND_PAID_OUT):
            raise ValidationError({"kind": "paid_in or paid_out"})
        mv = CashMovement.objects.create(session=session, kind=kind, amount_cents=int(request.data["amount_cents"]),
                                         reason=request.data.get("reason", "")[:200], user=request.user,
                                         authorized_by=mgr)
        audit(f"cash.{kind}", mv, entity_type="CashMovement", store=session.store, authorized_by=mgr,
              data={"amount_cents": mv.amount_cents, "reason": mv.reason})
        return Response({"id": mv.id, "expected_now_cents": expected_cash(session)}, status=201)
