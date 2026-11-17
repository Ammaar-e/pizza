from datetime import date, timedelta

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsStaffUser
from apps.core.models import AuditLog
from apps.core.tenancy import accessible_store_ids
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.payments.models import Payment, Refund

EXCLUDED = [OrderStatus.CANCELLED, OrderStatus.REJECTED]


def _range(request):
    try:
        end = date.fromisoformat(request.query_params.get("to")) if request.query_params.get("to") else date.today()
        start = date.fromisoformat(request.query_params["from"]) if request.query_params.get("from") \
            else end - timedelta(days=29)
    except ValueError:
        raise ValidationError("Dates must be YYYY-MM-DD")
    if start > end:
        raise ValidationError("'from' must be before 'to'")
    return start, end


def scoped_orders(request):
    u = request.user
    if not any(u.has_permission(p) for p in ("reports.network", "reports.franchise", "reports.store")):
        raise PermissionDenied("No reporting permission")
    start, end = _range(request)
    qs = Order.objects.filter(business_date__gte=start, business_date__lte=end)
    ids = accessible_store_ids(u)
    if ids is not None:
        qs = qs.filter(store_id__in=ids)
    p = request.query_params
    if p.get("franchisee"):
        qs = qs.filter(franchisee_id=p["franchisee"])
    if p.get("store"):
        qs = qs.filter(store_id=p["store"])
    return qs, start, end


PARAMS = [OpenApiParameter("from", str), OpenApiParameter("to", str), OpenApiParameter("franchisee", int),
          OpenApiParameter("store", int)]


class SalesSummaryView(APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(parameters=PARAMS, responses=dict)
    def get(self, request):
        orders, start, end = scoped_orders(request)
        valid = orders.exclude(status__in=EXCLUDED)
        agg = valid.aggregate(sales=Sum("total_cents"), net=Sum(F("subtotal_cents") - F("discount_cents")),
                              tax=Sum("tax_cents"), discounts=Sum("discount_cents"), tips=Sum("tip_cents"),
                              count=Count("id"), avg=Avg("total_cents"))
        payments = Payment.objects.filter(order__in=valid)
        refunds = Refund.objects.filter(payment__order__in=orders, status=Refund.STATUS_SUCCEEDED)
        items = OrderItem.objects.filter(order__in=valid)
        return Response({
            "range": {"from": start, "to": end},
            "totals": {
                "gross_sales_cents": agg["sales"] or 0, "net_sales_cents": agg["net"] or 0, "tax_cents": agg["tax"] or 0,
                "discount_cents": agg["discounts"] or 0, "tip_cents": agg["tips"] or 0, "order_count": agg["count"],
                "average_order_cents": int(agg["avg"] or 0),
                "refund_cents": refunds.aggregate(s=Sum("amount_cents"))["s"] or 0,
                "refund_count": refunds.count(),
                "cancelled_count": orders.filter(status=OrderStatus.CANCELLED).count(),
                "rejected_count": orders.filter(status=OrderStatus.REJECTED).count(),
            },
            "by_day": list(valid.values("business_date").annotate(sales_cents=Sum("total_cents"), orders=Count("id"))
                           .order_by("business_date")),
            "by_channel": list(valid.values("source").annotate(sales_cents=Sum("total_cents"), orders=Count("id"))
                               .order_by("-sales_cents")),
            "by_order_type": list(valid.values("order_type").annotate(sales_cents=Sum("total_cents"),
                                                                       orders=Count("id"))),
            "by_payment_method": list(payments.filter(status__in=Payment.SETTLED_STATUSES).values("method")
                                      .annotate(amount_cents=Sum("amount_cents"), refunded_cents=Sum("refunded_cents"),
                                                count=Count("id")).order_by("-amount_cents")),
            "tax_breakdown": _tax_breakdown(valid),
            "top_products": list(items.values("sku", "name").annotate(quantity=Sum("quantity"),
                                                                      sales_cents=Sum("line_total_cents"))
                                 .order_by("-sales_cents")[:25]),
            "by_employee": list(valid.filter(created_by__isnull=False).values("created_by__username")
                                .annotate(orders=Count("id"), sales_cents=Sum("total_cents"),
                                          avg_cents=Avg("total_cents"), discounts_cents=Sum("discount_cents"))
                                .order_by("-sales_cents")),
            "by_store": list(valid.values("store_id", "store__number", "store__name", "franchisee__code")
                             .annotate(orders=Count("id"), sales_cents=Sum("total_cents"), avg_cents=Avg("total_cents"),
                                       third_party=Count("id", filter=Q(source__in=["ubereats", "doordash", "skip"])))
                             .order_by("-sales_cents")),
        })


def _tax_breakdown(qs):
    out: dict[str, int] = {}
    for tb in qs.values_list("tax_breakdown", flat=True):
        for k, v in (tb or {}).items():
            out[k] = out.get(k, 0) + int(v)
    return out


class AuditLogView(APIView):
    permission_classes = [IsStaffUser]

    @extend_schema(parameters=[OpenApiParameter("store", int), OpenApiParameter("action", str),
                               OpenApiParameter("entity_type", str), OpenApiParameter("entity_id", str)],
                   responses=dict)
    def get(self, request):
        if not request.user.has_permission("audit.view"):
            raise PermissionDenied()
        qs = AuditLog.objects.select_related("store")
        ids = accessible_store_ids(request.user)
        if ids is not None:
            fr = request.user.franchisee_id
            qs = qs.filter(Q(store_id__in=ids) | (Q(franchisee_id=fr) if fr else Q(pk__in=[])))
        p = request.query_params
        for f in ("action", "entity_type", "entity_id"):
            if p.get(f):
                qs = qs.filter(**{f: p[f]})
        if p.get("store"):
            qs = qs.filter(store_id=p["store"])
        return Response([{"id": a.id, "at": a.created_at, "action": a.action, "entity_type": a.entity_type,
                          "entity_id": a.entity_id, "actor": a.actor_label,
                          "authorized_by": a.authorized_by_id, "store": a.store.number if a.store else None,
                          "data": a.data, "ip": a.ip_address} for a in qs[:500]])
