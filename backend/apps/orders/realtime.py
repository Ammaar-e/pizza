from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def store_group(store_id: int, kind: str) -> str:
    return f"store_{store_id}_{kind}"


def order_summary(order) -> dict:
    return {
        "id": str(order.pk),
        "order_number": order.order_number,
        "store_id": order.store_id,
        "store_number": order.store.number,
        "source": order.source,
        "order_type": order.order_type,
        "status": order.status,
        "payment_status": order.payment_status,
        "provider_sync_status": order.provider_sync_status,
        "requires_attention": order.requires_attention,
        "attention_reason": order.attention_reason,
        "external_display_id": order.external_display_id,
        "customer_name": (order.customer_snapshot or {}).get("name", ""),
        "total_cents": order.total_cents,
        "placed_at": order.placed_at.isoformat(),
    }


def broadcast(store_id: int, kind: str, message: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(store_group(store_id, kind), {"type": "push", "payload": message})


def broadcast_order(order, event: str) -> None:
    payload = {"event": event, "order": order_summary(order)}
    broadcast(order.store_id, "pos", payload)
