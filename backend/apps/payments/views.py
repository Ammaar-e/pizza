from datetime import date, timedelta

from django.http import HttpResponse
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
from apps.core.tenancy import accessible_store_ids
from apps.core.views import ContextMixin
from apps.organization.models import Store

from . import cash
from .models import CashSession, CashTransaction, Register, ShiftClosing


class RegisterSerializer(serializers.ModelSerializer):
    open_session = serializers.SerializerMethodField()
    device_name = serializers.CharField(source="device.name", read_only=True, default=None)

    class Meta:
        model = Register
        fields = ["id", "store", "device", "device_name", "code", "name", "is_active", "open_session"]

    def get_open_session(self, obj):
        s = obj.sessions.filter(status=CashSession.STATUS_OPEN).select_related("employee").first()
        return {"id": str(s.id), "employee": s.employee.username if s.employee else None,
                "opened_at": s.opened_at} if s else None


class RegisterViewSet(ContextMixin, viewsets.ModelViewSet):
    serializer_class = RegisterSerializer
    permission_classes = [IsStaffUser]
    pagination_class = None
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        ids = accessible_store_ids(self.request.user)
        qs = Register.objects.select_related("device")
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if self.request.query_params.get("store"):
            qs = qs.filter(store_id=self.request.query_params["store"])
        return qs

    def _check(self, store):
        u = self.request.user
        if not (u.has_permission("cash.review") and user_can_operate_store(u, store)):
            raise PermissionDenied("Managing registers requires cash.review for the store")

    def perform_create(self, serializer):
        self._check(serializer.validated_data["store"])
        dev = serializer.validated_data.get("device")
        if dev and dev.store_id != serializer.validated_data["store"].id:
            raise ValidationError({"device": "Device belongs to another store"})
        audit("register.create", serializer.save(), entity_type="Register")

    def perform_update(self, serializer):
        self._check(serializer.instance.store)
        audit("register.update", serializer.save(), entity_type="Register", data=dict(self.request.data))


class CashSessionSerializer(serializers.ModelSerializer):
    expected_now_cents = serializers.SerializerMethodField()
    opened_by_name = serializers.CharField(source="opened_by.username", read_only=True)
    employee_name = serializers.CharField(source="employee.username", read_only=True, default=None)
    register_code = serializers.CharField(source="register.code", read_only=True, default=None)
    register_name = serializers.CharField(source="register.name", read_only=True, default=None)
    store_number = serializers.CharField(source="store.number", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.username", read_only=True, default=None)
    adjustments = serializers.SerializerMethodField()

    class Meta:
        model = CashSession
        fields = ["id", "store", "store_number", "register", "register_code", "register_name", "device", "employee",
                  "employee_name", "status", "opened_by", "opened_by_name", "opened_at", "opening_float_cents",
                  "closed_by", "closed_at", "counted_cash_cents", "expected_cash_cents", "variance_cents",
                  "approved_by_name", "approved_at", "notes", "expected_now_cents", "adjustments"]
        read_only_fields = fields

    def get_expected_now_cents(self, obj):
        # Blind count: the expected amount is shown to managers, or after close.
        req = self.context.get("request")
        if obj.status == CashSession.STATUS_OPEN:
            if req is None or req.user.has_permission("cash.review") or req.query_params.get("show_expected") == "1":
                return cash.expected_cash(obj)
            return None
        return obj.expected_cash_cents

    def get_adjustments(self, obj):
        return cash.adjusted_figures(obj) if obj.status != CashSession.STATUS_OPEN else None


class TransactionSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.username", read_only=True)
    authorized_by_name = serializers.CharField(source="authorized_by.username", read_only=True, default=None)
    order_number = serializers.IntegerField(source="order.order_number", read_only=True, default=None)

    class Meta:
        model = CashTransaction
        fields = ["id", "kind", "amount_cents", "reason", "reference", "expense_category", "order", "order_number",
                  "payment", "refund", "counterpart_session", "user_name", "authorized_by_name", "post_close",
                  "created_at"]


class TransactionCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=sorted(cash.MANUAL_KINDS | {"paid_in"}))
    amount_cents = serializers.IntegerField(help_text="Positive; corrections may be negative")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    expense_category = serializers.CharField(required=False, allow_blank=True, default="")
    manager_code = serializers.CharField(required=False, allow_blank=True)


class CashSessionViewSet(ContextMixin, viewsets.ReadOnlyModelViewSet):
    """Register sessions: open with a float, record the cash ledger, transfer between registers,
    blind-count close, manager approval of discrepancies, shift closing report."""

    serializer_class = CashSessionSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        u = self.request.user
        ids = accessible_store_ids(u)
        qs = CashSession.objects.select_related("opened_by", "employee", "register", "store", "approved_by")
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if not u.has_permission("cash.review") and not u.has_permission("cash.approve"):
            qs = qs.filter(employee=u)  # cashiers see only their own drawers
        p = self.request.query_params
        if p.get("store"):
            qs = qs.filter(store_id=p["store"])
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("employee"):
            qs = qs.filter(employee_id=p["employee"])
        return qs.order_by("-opened_at")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    def _ser(self, s):
        return CashSessionSerializer(s, context={"request": self.request}).data

    @action(detail=False, methods=["get"])
    def current(self, request):
        """Open session on this device's register (on a terminal), else the caller's own open session."""
        device = getattr(request, "device", None)
        if device is not None:
            # On a terminal, only that terminal's register is "current".
            s = CashSession.objects.filter(register__device=device, status=CashSession.STATUS_OPEN).first()
        else:
            s = CashSession.objects.filter(employee=request.user, status=CashSession.STATUS_OPEN).first()
        return Response(self._ser(s) if s else None)

    @extend_schema(request=dict, responses=CashSessionSerializer)
    @action(detail=False, methods=["post"])
    def open(self, request):
        """``{opening_float_cents, register?, employee?}`` — defaults to this device's register and the
        signed-in employee."""
        if not request.user.has_permission("cash.manage_session"):
            raise PermissionDenied()
        device = getattr(request, "device", None)
        if request.data.get("register"):
            register = Register.objects.select_related("store", "device").filter(pk=request.data["register"]).first()
        else:
            register = cash.register_for_device(device)
        if register is None or not user_can_operate_store(request.user, register.store):
            raise PermissionDenied("Register not accessible")
        pinned = getattr(request, "pos_store_id", None)
        if pinned and pinned != register.store_id:
            raise PermissionDenied("POS session is pinned to another store")
        employee = request.user
        if request.data.get("employee"):
            employee = User.objects.filter(pk=request.data["employee"]).first()
        s = cash.open_session(register, user=request.user, employee=employee, device=device,
                              opening_float_cents=int(request.data.get("opening_float_cents", 0)))
        return Response(self._ser(s), status=201)

    @extend_schema(request=dict, responses=CashSessionSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Blind count: ``{counted_cash_cents, explanation?, denominations? {"2000": 3, ...}, notes?}``.
        Returns 422 ``explanation_required`` when the variance exceeds the configured threshold."""
        session = self.get_object()
        if not request.user.has_permission("cash.manage_session"):
            raise PermissionDenied()
        if "counted_cash_cents" not in request.data:
            raise ValidationError({"counted_cash_cents": "Required"})
        s = cash.close_session(session, request.user, int(request.data["counted_cash_cents"]),
                               request.data.get("notes", ""), explanation=request.data.get("explanation", ""),
                               denominations=request.data.get("denominations") or None)
        body = self._ser(s)
        body["shift_closing_id"] = str(s.shift_closing.pk) if hasattr(s, "shift_closing") else None
        return Response(body)

    @extend_schema(request=dict, responses=CashSessionSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        s = cash.approve_session(self.get_object(), request.user, request.data.get("notes", ""))
        return Response(self._ser(s))

    @extend_schema(methods=["GET"], responses=TransactionSerializer(many=True))
    @extend_schema(methods=["POST"], request=TransactionCreateSerializer, responses=TransactionSerializer,
                   parameters=[OpenApiParameter("Idempotency-Key", str, OpenApiParameter.HEADER)])
    @action(detail=True, methods=["get", "post"])
    def transactions(self, request, pk=None):
        """Drawer ledger. POST records a drop, addition, paid-out, petty expense or (manager-authorized)
        correction. Corrections are the only entries accepted after the session is closed."""
        session = self.get_object()
        if request.method == "GET":
            return Response(TransactionSerializer(session.transactions.select_related(
                "user", "authorized_by", "order"), many=True).data)
        if not request.user.has_permission("cash.manage_session"):
            raise PermissionDenied()
        s = TransactionCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        kind = CashTransaction.ADDITION if d["kind"] == "paid_in" else d["kind"]
        txn = cash.add_transaction(session, kind=kind, amount_cents=d["amount_cents"], user=request.user,
                                   reason=d["reason"], manager_code=d.get("manager_code"), reference=d["reference"],
                                   expense_category=d["expense_category"],
                                   idempotency_key=request.headers.get("Idempotency-Key"))
        session.refresh_from_db()
        return Response({**TransactionSerializer(txn).data, "expected_now_cents": cash.expected_cash(session)},
                        status=201)

    @extend_schema(request=dict, responses=dict)
    @action(detail=True, methods=["post"])
    def movements(self, request, pk=None):
        """Deprecated alias of ``transactions`` (kinds ``paid_in``/``paid_out``)."""
        return self.transactions(request, pk)

    @extend_schema(request=dict, responses=dict)
    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        """``{to_session, amount_cents, reason, manager_code}`` — move cash between two open registers."""
        source = self.get_object()
        target = self.get_queryset().filter(pk=request.data.get("to_session")).first() or \
            CashSession.objects.filter(pk=request.data.get("to_session"), store=source.store).first()
        if target is None:
            raise NotFound("Target session not found")
        out, inn = cash.transfer(source, target, amount_cents=int(request.data.get("amount_cents", 0)),
                                 user=request.user, reason=request.data.get("reason", "Register transfer"),
                                 manager_code=request.data.get("manager_code"))
        return Response({"transfer_out": TransactionSerializer(out).data, "transfer_in": TransactionSerializer(inn).data},
                        status=201)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def reconciliation(self, request, pk=None):
        s = self.get_object()
        rec = getattr(s, "reconciliation", None)
        if rec is None:
            if not request.user.has_permission("cash.review"):
                raise PermissionDenied("Blind count: running totals are visible to managers only")
            return Response({"status": s.status, "live": cash.breakdown(s)})
        data = {f.name: getattr(rec, f.name) for f in rec._meta.fields if f.name not in ("id", "session", "created_by")}
        data["discrepancies"] = [{"id": d.id, "amount_cents": d.amount_cents, "kind": d.kind, "status": d.status,
                                  "exceeds_threshold": d.exceeds_threshold, "explanation": d.explanation,
                                  "reviewed_by": d.reviewed_by.username if d.reviewed_by else None}
                                 for d in s.discrepancies.select_related("reviewed_by")]
        data.update(cash.adjusted_figures(s))
        return Response({"status": s.status, "reconciliation": data})


class ShiftClosingSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.username", read_only=True)
    store_number = serializers.CharField(source="store.number", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.username", read_only=True, default=None)

    class Meta:
        model = ShiftClosing
        fields = ["id", "store", "store_number", "employee", "employee_name", "session", "period_start", "period_end",
                  "summary", "status", "requires_approval", "approved_by_name", "approved_at", "notes", "created_at"]


class ShiftClosingViewSet(ContextMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ShiftClosingSerializer
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        u = self.request.user
        ids = accessible_store_ids(u)
        qs = ShiftClosing.objects.select_related("employee", "store", "approved_by")
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if not (u.has_permission("cash.review") or u.has_permission("cash.approve")):
            qs = qs.filter(employee=u)
        p = self.request.query_params
        if p.get("store"):
            qs = qs.filter(store_id=p["store"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        return qs

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def print(self, request, pk=None):
        sc = self.get_object()
        audit("shift_closing.print", sc, entity_type="ShiftClosing")
        return Response({"id": str(sc.pk), "text": cash.shift_report_text(sc)})

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        sc = self.get_object()
        audit("shift_closing.export", sc, entity_type="ShiftClosing")
        resp = HttpResponse(cash.shift_report_csv(sc), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="shift-{sc.store.number}-{sc.period_end:%Y%m%d-%H%M}.csv"'
        return resp

    @extend_schema(request=dict, responses=ShiftClosingSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        sc = self.get_object()
        if sc.session_id and sc.session.status == CashSession.STATUS_PENDING_APPROVAL:
            cash.approve_session(sc.session, request.user, request.data.get("notes", ""))
        elif sc.status == ShiftClosing.STATUS_PENDING:
            if not request.user.has_permission("cash.approve") or sc.employee_id == request.user.pk:
                raise PermissionDenied()
            sc.status, sc.approved_by = ShiftClosing.STATUS_APPROVED, request.user
            from django.utils import timezone

            sc.approved_at = timezone.now()
            sc.save()
            audit("shift_closing.approve", sc, entity_type="ShiftClosing")
        sc.refresh_from_db()
        return Response(ShiftClosingSerializer(sc).data)


class CashReportView(APIView):
    """Cash management report: HQ (network), franchise or store scope, by date range."""

    permission_classes = [IsStaffUser]

    @extend_schema(parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str),
                               OpenApiParameter("store", int), OpenApiParameter("franchisee", int)], responses=dict)
    def get(self, request):
        u = request.user
        if not (u.has_permission("cash.review") or u.has_permission("reports.network")):
            raise PermissionDenied()
        try:
            end = date.fromisoformat(request.query_params.get("to") or date.today().isoformat())
            start = date.fromisoformat(request.query_params.get("from") or (end - timedelta(days=6)).isoformat())
        except ValueError:
            raise ValidationError("Dates must be YYYY-MM-DD")
        qs = CashSession.objects.filter(opened_at__date__gte=start, opened_at__date__lte=end)
        ids = accessible_store_ids(u)
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        if request.query_params.get("store"):
            qs = qs.filter(store_id=request.query_params["store"])
        if request.query_params.get("franchisee"):
            qs = qs.filter(store__franchisee_id=request.query_params["franchisee"])
        return Response({"range": {"from": start, "to": end}, **cash.cash_report(qs)})
