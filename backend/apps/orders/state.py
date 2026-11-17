"""Order state machine. All status changes go through ``transition``."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.core.context import get_request_context
from apps.core.exceptions import ConflictError

from .models import Order, OrderEvent, OrderStatus

S = OrderStatus

ALLOWED = {
    S.RECEIVED: {S.ACCEPTED, S.PENDING_REVIEW, S.REJECTED, S.CANCELLED},
    S.PENDING_REVIEW: {S.ACCEPTED, S.REJECTED, S.CANCELLED},
    S.ACCEPTED: {S.PREPARING, S.READY, S.CANCELLED, S.COMPLETED},
    S.PREPARING: {S.READY, S.CANCELLED},
    S.READY: {S.OUT_FOR_DELIVERY, S.COMPLETED, S.CANCELLED, S.PREPARING},
    S.OUT_FOR_DELIVERY: {S.COMPLETED, S.CANCELLED},
    S.COMPLETED: set(),
    S.REJECTED: set(),
    S.CANCELLED: set(),
}

TERMINAL = {S.COMPLETED, S.REJECTED, S.CANCELLED}
ACTIVE = {S.RECEIVED, S.PENDING_REVIEW, S.ACCEPTED, S.PREPARING, S.READY, S.OUT_FOR_DELIVERY}

TIMESTAMP_FIELDS = {
    S.ACCEPTED: "accepted_at",
    S.READY: "ready_at",
    S.COMPLETED: "completed_at",
    S.CANCELLED: "cancelled_at",
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, set())


def actor_label(actor=None) -> str:
    if actor is not None:
        return f"user:{actor.username}"
    ctx = get_request_context()
    if ctx.user is not None and getattr(ctx.user, "is_authenticated", False):
        return f"user:{ctx.user.username}"
    return ctx.api_client_label or "system"


def log_event(order: Order, event_type: str, message: str = "", data: dict | None = None, actor=None,
              from_status: str = "", to_status: str = "") -> OrderEvent:
    return OrderEvent.objects.create(
        order=order, event_type=event_type, message=message, data=data or {}, actor_label=actor_label(actor),
        from_status=from_status, to_status=to_status,
    )


def transition(order: Order, target: str, *, actor=None, reason: str = "", data: dict | None = None,
               notify: bool = True) -> Order:
    """Atomically move an order to ``target`` (row-locked), log and broadcast it."""
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        current = locked.status
        if current == target:
            return locked
        if not can_transition(current, target):
            raise ConflictError(f"Cannot change order from '{current}' to '{target}'", code="invalid_transition")
        locked.status = target
        update = ["status", "updated_at"]
        ts = TIMESTAMP_FIELDS.get(target)
        if ts:
            setattr(locked, ts, timezone.now())
            update.append(ts)
        if target == S.REJECTED:
            locked.rejection_reason = reason[:300]
            update.append("rejection_reason")
        if target == S.CANCELLED:
            locked.cancellation_reason = reason[:300]
            update.append("cancellation_reason")
        if target in TERMINAL or target == S.ACCEPTED:
            locked.requires_attention = False if target != S.REJECTED else locked.requires_attention
            update.append("requires_attention")
        locked.save(update_fields=update)
        log_event(locked, "status_changed", reason, data, actor, from_status=current, to_status=target)
        if notify:
            from .signals import order_status_changed

            transaction.on_commit(lambda: order_status_changed(locked.pk, current, target))
    order.refresh_from_db()
    return order
