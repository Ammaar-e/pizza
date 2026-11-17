from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import ConflictError
from apps.orders.models import Order, OrderStatus, OrderType
from apps.orders.realtime import broadcast
from apps.orders.state import transition

from .models import KitchenStation, KitchenTicket, TicketStatus

TICKET_FLOW = {
    TicketStatus.RECEIVED: {TicketStatus.PREPARING, TicketStatus.READY, TicketStatus.VOID},
    TicketStatus.PREPARING: {TicketStatus.READY, TicketStatus.VOID, TicketStatus.RECEIVED},
    TicketStatus.READY: {TicketStatus.COMPLETED, TicketStatus.PREPARING, TicketStatus.VOID},
    TicketStatus.COMPLETED: set(),
    TicketStatus.VOID: set(),
}


def item_payload(item) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "quantity": item.quantity,
        "size": item.size_name,
        "crust": item.crust_name,
        "prep_category": item.prep_category,
        "modifiers": [
            {"name": m.name, "placement": m.placement, "quantity": m.quantity, "is_default": m.is_default}
            for m in item.modifiers.all()
        ],
        "removed": [r.get("name") for r in item.removed or []],
        "special_instructions": item.special_instructions,
        "is_mapped": item.is_mapped,
    }


def ticket_payload(t: KitchenTicket) -> dict:
    o = t.order
    return {
        "id": t.id,
        "order_id": str(o.pk),
        "order_number": o.order_number,
        "store_number": o.store.number,
        "station": t.station.code,
        "source": o.source,
        "order_type": o.order_type,
        "external_display_id": o.external_display_id,
        "customer_name": (o.customer_snapshot or {}).get("name", ""),
        "special_instructions": o.special_instructions,
        "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
        "status": t.status,
        "items": t.items,
        "received_at": t.received_at.isoformat(),
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "ready_at": t.ready_at.isoformat() if t.ready_at else None,
    }


def ensure_default_station(store) -> KitchenStation:
    st = store.kitchen_stations.filter(is_active=True).first()
    if st:
        return st
    return KitchenStation.objects.create(store=store, code="MAIN", name="Main line", is_expo=True)


def create_tickets(order: Order) -> list[KitchenTicket]:
    stations = list(order.store.kitchen_stations.filter(is_active=True)) or [ensure_default_station(order.store)]
    items = list(order.items.prefetch_related("modifiers"))
    created = []
    for st in stations:
        if st.is_expo or not st.prep_categories:
            routed = items
        else:
            routed = [i for i in items if i.prep_category in st.prep_categories]
        if not routed:
            continue
        t, was_created = KitchenTicket.objects.get_or_create(
            order=order, station=st, defaults={"store": order.store, "items": [item_payload(i) for i in routed]})
        if was_created:
            created.append(t)
    for t in created:
        broadcast(order.store_id, "kds", {"event": "ticket.created", "ticket": ticket_payload(t)})
    return created


def on_order_status_changed(order: Order, from_status: str, to_status: str) -> None:
    if to_status == OrderStatus.ACCEPTED:
        create_tickets(order)
    elif to_status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
        for t in order.kitchen_tickets.exclude(status__in=[TicketStatus.COMPLETED, TicketStatus.VOID]):
            t.status = TicketStatus.VOID
            t.save(update_fields=["status", "updated_at"])
            broadcast(order.store_id, "kds", {"event": "ticket.voided", "ticket": ticket_payload(t)})


def bump_ticket(ticket: KitchenTicket, target: str, user) -> KitchenTicket:
    """Advance a kitchen ticket and derive the order status from all its tickets."""
    with transaction.atomic():
        t = KitchenTicket.objects.select_for_update().select_related("order", "station", "order__store").get(
            pk=ticket.pk)
        if target not in TICKET_FLOW.get(t.status, set()):
            raise ConflictError(f"Ticket cannot move from '{t.status}' to '{target}'", code="invalid_transition")
        now = timezone.now()
        t.status = target
        t.bumped_by = user
        if target == TicketStatus.PREPARING and not t.started_at:
            t.started_at = now
        if target == TicketStatus.READY:
            t.ready_at = now
        if target == TicketStatus.COMPLETED:
            t.completed_at = now
        t.save()
    order = t.order
    statuses = set(order.kitchen_tickets.exclude(status=TicketStatus.VOID).values_list("status", flat=True))
    if target == TicketStatus.PREPARING and order.status == OrderStatus.ACCEPTED:
        transition(order, OrderStatus.PREPARING, actor=user, reason="Kitchen started")
    elif statuses and statuses <= {TicketStatus.READY, TicketStatus.COMPLETED} and order.status in (
            OrderStatus.ACCEPTED, OrderStatus.PREPARING):
        transition(order, OrderStatus.READY, actor=user, reason="All kitchen stations ready")
    elif statuses == {TicketStatus.COMPLETED} and order.status == OrderStatus.READY and \
            order.order_type != OrderType.DELIVERY:
        transition(order, OrderStatus.COMPLETED, actor=user, reason="Handed off")
    broadcast(order.store_id, "kds", {"event": "ticket.updated", "ticket": ticket_payload(t)})
    return t
