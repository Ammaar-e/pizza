from celery import shared_task

from .services import refresh_statuses


@shared_task
def refresh_promotion_statuses():
    """Activate scheduled promotions and expire ended ones; notify POS terminals of changes."""
    from apps.orders.realtime import broadcast
    from apps.organization.models import Store

    from .models import Promotion

    changed = refresh_statuses()
    if changed:
        ids = [c[0] for c in changed]
        stores = set()
        for p in Promotion.objects.filter(id__in=ids).prefetch_related("targets"):
            if p.store_id:
                stores.add(p.store_id)
            elif p.franchisee_id:
                stores |= set(Store.objects.filter(franchisee_id=p.franchisee_id).values_list("id", flat=True))
            else:
                stores |= set(Store.objects.values_list("id", flat=True))
        for sid in stores:
            broadcast(sid, "pos", {"event": "promotions.updated", "changes": [
                {"promotion_id": i, "from": a, "to": b} for i, a, b in changed]})
    return len(changed)
