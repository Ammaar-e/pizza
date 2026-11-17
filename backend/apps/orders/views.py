from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.accounts.services import authorize_manager, user_can_operate_store
from apps.core.audit import audit
from apps.core.exceptions import ConflictError, DomainError
from apps.core.tenancy import accessible_store_ids
from apps.core.views import ContextMixin
from apps.organization.models import Store
from apps.payments.models import Payment
from apps.payments.services import confirm_card_payment, recompute_order_payment_status, refund_payment, \
    take_payment

from .intake import (
    AcceptancePolicy,
    NormalizedCustomer,
    NormalizedItem,
    NormalizedModifier,
    NormalizedOrder,
    NormalizedPayment,
    OrderIntakeService,
)
from .models import THIRD_PARTY_SOURCES, Order, OrderDiscount, OrderSource, OrderStatus
from .serializers import (
    CancelSerializer,
    DiscountApplySerializer,
    OrderCreateSerializer,
    OrderEventSerializer,
    OrderListSerializer,
    OrderPatchSerializer,
    OrderSerializer,
    PaymentCreateSerializer,
    PublicOrderSerializer,
    RefundCreateSerializer,
    StatusChangeSerializer,
)
from .state import ACTIVE, log_event, transition

IDEMPOTENCY_HEADER = OpenApiParameter("Idempotency-Key", str, OpenApiParameter.HEADER, required=False,
                                      description="Unique key per logical order; retries with the same key return "
                                                  "the original order instead of creating a duplicate.")


def client_of(request):
    return getattr(request._request, "api_client", None)


class OrderAccess(permissions.BasePermission):
    def has_permission(self, request, view):
        client = client_of(request)
        if client is not None:
            if view.action == "create":
                return client.has_scope("orders:write")
            if view.action == "cancel":
                return client.has_scope("orders:cancel")
            if view.action in ("list", "retrieve", "status_view", "store_orders"):
                return client.has_scope("orders:read")
            return False
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if view.action == "create":
            return user.has_permission("orders.create")
        return user.has_permission("orders.view") or user.has_permission("kitchen.access")


def build_normalized(data: dict, source: str) -> NormalizedOrder:
    items = [NormalizedItem(
        product=i["product"], size=i.get("size") or None, crust=i.get("crust") or None, quantity=i["quantity"],
        modifiers=[NormalizedModifier(code=m.get("code"), placement=m.get("placement", "whole"),
                                      quantity=int(m.get("quantity", 1))) for m in i.get("modifiers", [])],
        removed=list(i.get("removed", [])), special_instructions=i.get("special_instructions", ""),
    ) for i in data["items"]]
    cust = data.get("customer") or {}
    pay = data.get("payment") or {}
    return NormalizedOrder(
        source=source, order_type=data["order_type"], items=items, store_id=data.get("store_id"),
        store_number=data.get("store_number"), external_order_id=data.get("external_order_id") or None,
        customer=NormalizedCustomer(name=cust.get("name", ""), phone=cust.get("phone", ""),
                                    email=cust.get("email", "")),
        delivery_address=dict(data.get("delivery_address") or {}),
        special_instructions=data.get("special_instructions", ""), scheduled_for=data.get("scheduled_for"),
        tip_cents=data.get("tip_cents", 0), expected_total_cents=data.get("expected_total_cents"),
        payment=NormalizedPayment(status=pay.get("status", "unpaid"), amount_cents=pay.get("amount_cents", 0),
                                  reference=pay.get("reference", "")),
        entry_channel=data.get("entry_channel", ""),
        promo_codes=list(data.get("promo_codes") or []),
        promotion_ids=list(data.get("promotion_ids") or []),
        manager_code=data.get("manager_code") or "",
        offline_client_ref=data.get("offline_client_ref", ""),
        is_offline_origin=bool(data.get("offline_client_ref")),
        raw={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in data.items()
             if k not in ("payment", "manager_code")},
    )


def submit_order(request, data: dict, idempotency_key: str | None):
    client = client_of(request)
    user = None if client else request.user
    policy = None
    if client is not None:
        source = client.order_source
    else:
        source = data.get("source") or OrderSource.POS
        if source in THIRD_PARTY_SOURCES:
            if not data.get("external_order_id"):
                raise ValidationError({"external_order_id": "Required when recording a provider order manually"})
            data["entry_channel"] = "manual_provider"
            policy = AcceptancePolicy(auto_accept=True, reject_unavailable_items=False, reject_when_closed=False,
                                      reject_unmapped_items=False, price_tolerance_cents=10 ** 9)
        store = Store.objects.filter(Q(pk=data.get("store_id")) | Q(number=data.get("store_number") or "-")).first()
        if store is None or not user_can_operate_store(user, store):
            raise PermissionDenied("You are not authorized for this store")
        pinned = getattr(request, "pos_store_id", None)
        if pinned and pinned != store.id:
            raise PermissionDenied("POS session is pinned to another store")
    norm = build_normalized(data, source)
    result = OrderIntakeService(actor=user, api_client=client, device=getattr(request, "device", None),
                                policy=policy).submit(norm, idempotency_key=idempotency_key)
    order = result.order
    if result.created and norm.payment.status == "prepaid":
        try:
            take_payment(order, method="online", amount_cents=min(norm.payment.amount_cents or order.total_cents,
                                                                  order.total_cents),
                         user=user, reference=norm.payment.reference,
                         idempotency_key=f"intake:{order.pk}")
        except DomainError as e:
            log_event(order, "payment_failed", str(e.detail))
        order.refresh_from_db()
    return result


class OrderViewSet(ContextMixin, viewsets.GenericViewSet):
    """Orders from every channel. Staff see orders for their stores; API clients see only their own orders."""

    permission_classes = [OrderAccess]
    lookup_value_regex = "[0-9a-f-]{36}"

    def get_queryset(self):
        qs = Order.objects.select_related("store", "store_integration__provider").prefetch_related(
            Prefetch("items"), "items__modifiers", "payments", "discounts")
        client = client_of(self.request)
        if client is not None:
            return qs.filter(api_client=client)
        ids = accessible_store_ids(self.request.user)
        if ids is not None:
            qs = qs.filter(store_id__in=ids)
        pinned = getattr(self.request, "pos_store_id", None)
        if pinned:
            qs = qs.filter(store_id=pinned)
        p = self.request.query_params
        if p.get("store"):
            qs = qs.filter(store_id=p["store"])
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("source"):
            qs = qs.filter(source__in=p["source"].split(","))
        if p.get("active") == "1":
            qs = qs.filter(status__in=ACTIVE)
        if p.get("attention") == "1":
            qs = qs.filter(requires_attention=True)
        if p.get("date") == "today":
            # Business date is the store's local date, not the client's or UTC.
            store_id = p.get("store") or pinned
            store = Store.objects.filter(pk=store_id).first() if store_id else None
            if store is None:
                raise ValidationError({"date": "'today' requires a store"})
            qs = qs.filter(business_date=store.local_now().date())
        elif p.get("date"):
            qs = qs.filter(business_date=p["date"])
        if p.get("q"):
            q = p["q"]
            cond = Q(external_order_id__icontains=q) | Q(external_display_id__icontains=q) | Q(
                customer_snapshot__name__icontains=q) | Q(customer_snapshot__phone__icontains=q)
            if q.isdigit():
                cond |= Q(order_number=int(q))
            qs = qs.filter(cond)
        return qs

    def get_object(self):
        obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
        if obj is None:
            raise NotFound("Order not found")
        return obj

    def _out(self, order):
        order = Order.objects.select_related("store", "store_integration__provider").prefetch_related(
            "items__modifiers", "payments", "discounts").get(pk=order.pk)
        if client_of(self.request) is not None:
            return PublicOrderSerializer(order).data
        return OrderSerializer(order).data

    @extend_schema(responses=OrderListSerializer(many=True),
                   parameters=[OpenApiParameter("store", int), OpenApiParameter("status", str),
                               OpenApiParameter("source", str), OpenApiParameter("active", str),
                               OpenApiParameter("attention", str), OpenApiParameter("date", str),
                               OpenApiParameter("q", str)])
    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        ser = PublicOrderSerializer if client_of(request) else OrderListSerializer
        return self.get_paginated_response(ser(page, many=True).data)

    @extend_schema(responses=OrderSerializer)
    def retrieve(self, request, pk=None):
        return Response(self._out(self.get_object()))

    @extend_schema(request=OrderCreateSerializer, responses={201: OrderSerializer, 200: OrderSerializer},
                   parameters=[IDEMPOTENCY_HEADER])
    def create(self, request):
        """Submit an order through the central intake engine.

        Returns 201 for a new order, 200 when the Idempotency-Key (or provider order id) matches an
        existing order. Validation failures return 422 with per-line issues."""
        s = OrderCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if client_of(request) is not None and not key:
            raise ValidationError({"Idempotency-Key": "Header is required for API clients"})
        if client_of(request) is not None and s.validated_data.get("source"):
            raise ValidationError({"source": "Determined by the API client"})
        result = submit_order(request, dict(s.validated_data), key)
        body = self._out(result.order)
        body["intake"] = {"decision": result.decision, "created": result.created}
        return Response(body, status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)

    @extend_schema(request=OrderPatchSerializer, responses=OrderSerializer)
    def partial_update(self, request, pk=None):
        if client_of(request) is not None:
            raise PermissionDenied("API clients cannot modify orders; cancel and resubmit")
        order = self.get_object()
        if not request.user.has_permission("orders.create"):
            raise PermissionDenied()
        if order.status not in (OrderStatus.RECEIVED, OrderStatus.PENDING_REVIEW, OrderStatus.ACCEPTED):
            raise ConflictError("Order can no longer be modified", code="order_locked")
        if order.is_third_party:
            raise ConflictError("Third-party orders are modified through the provider", code="provider_order")
        s = OrderPatchSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if "special_instructions" in d:
            order.special_instructions = d["special_instructions"]
        if "customer" in d:
            order.customer_snapshot = {k: v for k, v in d["customer"].items() if v}
        if "delivery_address" in d:
            order.delivery_address = d["delivery_address"]
        if "scheduled_for" in d:
            order.scheduled_for = d["scheduled_for"]
        order.save()
        log_event(order, "order_updated", "Order details updated", {"fields": sorted(d)}, request.user)
        audit("order.update", order, data={"fields": sorted(d)})
        return Response(self._out(order))

    @extend_schema(request=CancelSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        s = CancelSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        mgr = None
        if client_of(request) is not None:
            if order.status not in (OrderStatus.RECEIVED, OrderStatus.PENDING_REVIEW, OrderStatus.ACCEPTED):
                raise ConflictError("Order is already being prepared; contact the store", code="cannot_cancel")
        else:
            mgr = authorize_manager(order.store, s.validated_data.get("manager_code"), "orders.cancel", request.user)
        transition(order, OrderStatus.CANCELLED, actor=None if client_of(request) else request.user,
                   reason=s.validated_data["reason"])
        audit("order.cancel", order, authorized_by=mgr, data={"reason": s.validated_data["reason"]})
        return Response(self._out(order))

    @extend_schema(methods=["GET"], responses=dict)
    @extend_schema(methods=["POST"], request=StatusChangeSerializer, responses=dict)
    @action(detail=True, methods=["get", "post"], url_path="status", url_name="status")
    def status_view(self, request, pk=None):
        order = self.get_object()
        if request.method == "POST":
            if client_of(request) is not None:
                raise PermissionDenied("API clients cannot change order status")
            s = StatusChangeSerializer(data=request.data)
            s.is_valid(raise_exception=True)
            target = s.validated_data["status"]
            if target in (OrderStatus.CANCELLED,):
                raise ValidationError({"status": "Use the cancel endpoint"})
            if target in (OrderStatus.ACCEPTED, OrderStatus.REJECTED) and order.status == OrderStatus.PENDING_REVIEW:
                if not request.user.has_permission("orders.manual_integration_action"):
                    authorize_manager(order.store, s.validated_data.get("manager_code"),
                                      "orders.manual_integration_action", request.user)
            transition(order, target, actor=request.user, reason=s.validated_data.get("reason", ""))
            audit("order.status", order, data={"status": target})
        order.refresh_from_db()
        return Response({"id": str(order.pk), "order_number": order.order_number, "status": order.status,
                         "payment_status": order.payment_status, "provider_sync_status": order.provider_sync_status,
                         "updated_at": order.updated_at, "accepted_at": order.accepted_at, "ready_at": order.ready_at,
                         "completed_at": order.completed_at, "cancelled_at": order.cancelled_at,
                         "rejection_reason": order.rejection_reason or None,
                         "cancellation_reason": order.cancellation_reason or None})

    @extend_schema(responses=OrderEventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        if client_of(request) is not None:
            raise PermissionDenied()
        return Response(OrderEventSerializer(self.get_object().events.all(), many=True).data)

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Manually accept an order awaiting staff review (e.g. third-party order with exceptions)."""
        from apps.integrations.services import manual_decision

        order = self.get_object()
        mgr = authorize_manager(order.store, request.data.get("manager_code"), "orders.manual_integration_action",
                                request.user)
        manual_decision(order, True, request.user, request.data.get("reason", ""))
        audit("order.manual_accept", order, authorized_by=mgr)
        return Response(self._out(order))

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        from apps.integrations.services import manual_decision

        order = self.get_object()
        mgr = authorize_manager(order.store, request.data.get("manager_code"), "orders.manual_integration_action",
                                request.user)
        manual_decision(order, False, request.user, request.data.get("reason") or "ITEM_UNAVAILABLE")
        audit("order.manual_reject", order, authorized_by=mgr)
        return Response(self._out(order))

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"], url_path="retry-sync")
    def retry_sync(self, request, pk=None):
        """Retry a failed provider synchronization (acknowledgement / ready / cancel)."""
        from apps.integrations.services import sync_status
        from apps.integrations.adapters.base import ProviderError

        order = self.get_object()
        if not request.user.has_permission("orders.manual_integration_action"):
            raise PermissionDenied()
        try:
            rec = sync_status(order, order.status)
        except ProviderError as e:
            return Response({"ok": False, "error": str(e)}, status=502)
        return Response({"ok": True, "result": rec.status if rec else "nothing_to_sync"})

    @extend_schema(request=DiscountApplySerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def discounts(self, request, pk=None):
        """Apply a promotion code, a manually selectable promotion, or an open (manual) discount.
        All promotions on the order are re-validated and taxes/totals recalculated server-side."""
        from apps.promotions.services import apply_promotions

        order = self.get_object()
        s = DiscountApplySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if order.is_third_party:
            raise ConflictError("Discounts on provider orders are managed by the provider", code="provider_order")
        if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.COMPLETED):
            raise ConflictError("Order is closed", code="order_closed")
        if order.payment_status not in ("unpaid",):
            raise ConflictError("Apply discounts before taking payment", code="payment_started")
        from apps.devices.services import feature_enabled

        device = getattr(request, "device", None)
        if d.get("discount_code") or d.get("promotion_id"):
            if not (request.user.has_permission("promotions.apply_code") or
                    request.user.has_permission("orders.apply_discount")):
                raise PermissionDenied()
            if not feature_enabled(device, "coupon_entry"):
                raise DomainError("Coupon entry is disabled on this device", code="feature_disabled", status_code=403)
            with transaction.atomic():
                o = Order.objects.select_for_update().get(pk=order.pk)
                ev = apply_promotions(o, codes=[d["discount_code"]] if d.get("discount_code") else [],
                                      promotion_ids=[d["promotion_id"]] if d.get("promotion_id") else [],
                                      user=request.user, manager_code=d.get("manager_code"))
            log_event(order, "promotion", "Promotions re-evaluated", {"applied": [a.as_dict() for a in ev.applied]},
                      request.user)
            body = self._out(order)
            body["promotions"] = ev.as_dict()
            return Response(body)
        if not d.get("amount_cents"):
            raise ValidationError("discount_code, promotion_id or amount_cents required")
        if not request.user.has_permission("orders.apply_discount"):
            raise PermissionDenied()
        if not feature_enabled(device, "discounts"):
            raise DomainError("Manual discounts are disabled on this device", code="feature_disabled",
                              status_code=403)
        # Open (manual) discounts always need a manager.
        mgr = authorize_manager(order.store, d.get("manager_code"), "orders.apply_discount", request.user)
        hq_policy = order.store.franchisee.headquarters.policies or {}
        max_pct = hq_policy.get("max_discount_percent")
        with transaction.atomic():
            o = Order.objects.select_for_update().get(pk=order.pk)
            amount = min(d["amount_cents"], o.subtotal_cents - o.discount_cents)
            if amount <= 0:
                raise ValidationError("Nothing to discount")
            if max_pct is not None and (o.discount_cents + amount) * 100 > o.subtotal_cents * int(max_pct):
                raise DomainError(f"Discount exceeds company policy maximum of {max_pct}%", code="discount_policy")
            OrderDiscount.objects.create(order=o, code="MANUAL", name=d.get("reason") or "Manual discount",
                                         amount_cents=amount, applied_by=request.user, authorized_by=mgr)
            apply_promotions(o, user=request.user)
        log_event(order, "discount", f"Manual discount {amount} applied", {"amount_cents": amount}, request.user)
        audit("order.discount", order, authorized_by=mgr, data={"amount_cents": amount, "reason": d.get("reason")})
        return Response(self._out(order))

    @extend_schema(request=PaymentCreateSerializer, responses=OrderSerializer, parameters=[IDEMPOTENCY_HEADER])
    @action(detail=True, methods=["post"])
    def payments(self, request, pk=None):
        order = self.get_object()
        if not request.user.has_permission("payments.take"):
            raise PermissionDenied()
        s = PaymentCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        payment = take_payment(order, method=d["method"], amount_cents=d["amount_cents"], user=request.user,
                               tendered_cents=d.get("tendered_cents"), tip_cents=d.get("tip_cents", 0),
                               reference=d.get("reference", ""), device=getattr(request, "device", None),
                               idempotency_key=request.headers.get("Idempotency-Key"))
        body = self._out(order)
        body["payment"] = {"id": str(payment.pk), "status": payment.status, "change_cents": payment.change_cents,
                           "failure_reason": payment.failure_reason}
        return Response(body, status=201)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def receipt(self, request, pk=None):
        from .receipts import render_receipt

        order = self.get_object()
        audit("order.receipt", order)
        return Response(render_receipt(order))


class PaymentActionsView(ContextMixin, APIView):
    permission_classes = [IsStaffUser]

    def _payment(self, request, pk):
        p = Payment.objects.select_related("order__store", "store").filter(pk=pk).first()
        ids = accessible_store_ids(request.user)
        if p is None or (ids is not None and p.store_id not in ids):
            raise NotFound()
        return p

    @extend_schema(request=RefundCreateSerializer, responses=dict)
    def post(self, request, pk, op):
        p = self._payment(request, pk)
        if op == "refund":
            if not (request.user.has_permission("payments.take") or request.user.has_permission("payments.refund")):
                raise PermissionDenied()
            s = RefundCreateSerializer(data=request.data)
            s.is_valid(raise_exception=True)
            r = refund_payment(p, amount_cents=s.validated_data["amount_cents"], reason=s.validated_data["reason"],
                               user=request.user, manager_code=s.validated_data.get("manager_code"),
                               device=getattr(request, "device", None))
            p.refresh_from_db()
            return Response({"refund_id": str(r.pk), "status": r.status, "payment_status": p.status,
                             "order_payment_status": Order.objects.get(pk=p.order_id).payment_status})
        if op == "confirm":
            p = confirm_card_payment(p, request.user)
            return Response({"payment_id": str(p.pk), "status": p.status, "card_brand": p.card_brand,
                             "card_last4": p.card_last4})
        if op == "reconcile":
            if not request.user.has_permission("reports.store"):
                raise PermissionDenied()
            p.reconciled = True
            p.settlement_reference = request.data.get("settlement_reference", "")[:200]
            p.save(update_fields=["reconciled", "settlement_reference", "updated_at"])
            recompute_order_payment_status(p.order)
            audit("payment.reconcile", p, store=p.store, data={"settlement_reference": p.settlement_reference})
            return Response({"payment_id": str(p.pk), "reconciled": True})
        raise NotFound()


class OfflineSyncView(ContextMixin, APIView):
    """Upload orders queued by a POS while offline. Each order is idempotent on its client reference,
    so re-uploading after a partial failure never creates duplicates."""

    permission_classes = [IsStaffUser]

    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        device = getattr(request, "device", None)
        if device is None:
            raise PermissionDenied("Offline sync requires a registered POS device (X-Device-Token)")
        results = []
        for entry in request.data.get("orders", [])[:100]:
            ref = str(entry.get("offline_client_ref") or "")
            if not ref:
                results.append({"offline_client_ref": None, "status": "error", "error": "offline_client_ref missing"})
                continue
            payload = dict(entry.get("order") or {})
            payload["offline_client_ref"] = ref
            payload.setdefault("store_id", device.store_id)
            s = OrderCreateSerializer(data=payload)
            if not s.is_valid():
                results.append({"offline_client_ref": ref, "status": "error", "error": s.errors})
                continue
            key = f"offline:{device.pk}:{ref}"
            try:
                result = submit_order(request, dict(s.validated_data), key)
                order = result.order
                for i, pay in enumerate(entry.get("payments") or []):
                    if pay.get("method") != "cash":
                        continue  # card payments cannot be captured offline
                    outstanding = order.total_cents - order.amount_paid_cents
                    amount = int(pay.get("amount_cents") or outstanding)
                    tendered = int(pay.get("tendered_cents") or amount)
                    try:
                        if tendered < amount:
                            raise DomainError(f"Cash tendered offline ({tendered}) is less than the order total "
                                              f"({amount})", code="offline_cash_short")
                        take_payment(order, method="cash", amount_cents=amount, user=request.user,
                                     tendered_cents=tendered, device=device, idempotency_key=f"{key}:pay:{i}")
                    except DomainError as e:
                        log_event(order, "offline_payment_failed", str(e.detail))
                        Order.objects.filter(pk=order.pk).update(
                            requires_attention=True, attention_reason=f"Offline payment not applied: {e.detail}"[:300])
                order.refresh_from_db()
                results.append({"offline_client_ref": ref, "status": "duplicate" if not result.created else "created",
                                "order_id": str(order.pk), "order_number": order.order_number,
                                "order_status": order.status})
            except DomainError as e:
                results.append({"offline_client_ref": ref, "status": "error", "error": str(e.detail),
                                "code": e.code, "issues": e.errors})
        return Response({"results": results, "synced_at": timezone.now()})
