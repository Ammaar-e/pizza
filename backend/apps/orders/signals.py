"""Post-commit fan-out of order lifecycle changes to kitchen, real-time clients,
external providers and API client webhooks. Lazy imports keep apps decoupled."""
from __future__ import annotations

import logging

logger = logging.getLogger("orders.signals")


def order_status_changed(order_id, from_status: str, to_status: str) -> None:
    from apps.kitchen.services import on_order_status_changed as kitchen_hook
    from apps.orders.models import Order
    from apps.orders.realtime import broadcast_order

    order = Order.objects.select_related("store").filter(pk=order_id).first()
    if order is None:
        return
    for hook in (kitchen_hook,):
        try:
            hook(order, from_status, to_status)
        except Exception:  # pragma: no cover - never break the chain
            logger.exception("kitchen hook failed for order %s", order_id)
    try:
        broadcast_order(order, event="order.status_changed")
    except Exception:  # pragma: no cover
        logger.exception("broadcast failed for order %s", order_id)
    if order.is_third_party and order.store_integration_id:
        from apps.integrations.tasks import sync_order_status_to_provider

        sync_order_status_to_provider.delay(str(order.pk), to_status)
    if order.api_client_id:
        from apps.api.tasks import enqueue_order_status_webhook

        enqueue_order_status_webhook(order, from_status, to_status)
