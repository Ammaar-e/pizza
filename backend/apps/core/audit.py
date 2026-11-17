from __future__ import annotations

from typing import Any

from .context import get_request_context
from .models import AuditLog


def audit(
    action: str,
    entity: Any = None,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor=None,
    actor_label: str | None = None,
    authorized_by=None,
    store=None,
    franchisee=None,
    data: dict | None = None,
) -> AuditLog:
    ctx = get_request_context()
    if actor is None and ctx.user is not None and getattr(ctx.user, "is_authenticated", False):
        actor = ctx.user
    if entity is not None:
        entity_type = entity_type or entity.__class__.__name__
        entity_id = entity_id or str(entity.pk)
        if store is None:
            store = getattr(entity, "store", None) if not entity.__class__.__name__ == "Store" else entity
        if franchisee is None:
            franchisee = getattr(entity, "franchisee", None)
            if franchisee is None and store is not None:
                franchisee = getattr(store, "franchisee", None)
    if actor_label is None:
        if actor is not None:
            actor_label = f"user:{actor.username}"
        elif ctx.api_client_label:
            actor_label = ctx.api_client_label
        else:
            actor_label = "system"
    return AuditLog.objects.create(
        action=action,
        entity_type=entity_type or "unknown",
        entity_id=str(entity_id or ""),
        actor=actor,
        actor_label=actor_label,
        authorized_by=authorized_by,
        store=store,
        franchisee=franchisee,
        data=_safe(data or {}),
        ip_address=ctx.ip,
        request_id=ctx.request_id or "",
    )


_SECRET_HINTS = ("secret", "password", "token", "api_key", "apikey", "signing", "pin", "cvv", "card_number")


def _safe(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if any(h in k.lower() for h in _SECRET_HINTS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _safe(v)
        else:
            out[k] = v
    return out
