from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.orders.models import Order, OrderStatus, ProviderSyncStatus

from .adapters.base import ProviderError
from .models import WebhookEvent

logger = logging.getLogger("integrations")


@shared_task(bind=True, max_retries=5, acks_late=True)
def process_webhook_event_task(self, event_pk: int):
    from .services import process_webhook_event

    ev = WebhookEvent.objects.filter(pk=event_pk).first()
    if ev is None:
        return
    process_webhook_event(ev)


@shared_task(bind=True, max_retries=6, acks_late=True)
def sync_order_status_to_provider(self, order_id: str, status: str):
    from .services import sync_status

    order = Order.objects.select_related("store_integration__provider", "store_integration__account",
                                         "store").filter(pk=order_id).first()
    if order is None:
        return
    try:
        sync_status(order, status)
    except ProviderError as exc:
        if order.provider_ack_deadline and timezone.now() > order.provider_ack_deadline and status in (
                OrderStatus.ACCEPTED, OrderStatus.REJECTED):
            logger.error("Provider ack deadline passed for order %s", order_id)
            return
        raise self.retry(exc=exc, countdown=min(10 * (2 ** self.request.retries), 120))


@shared_task
def sweep_pending_provider_acknowledgements():
    """Never let a provider acknowledgement time out while waiting for staff.

    Orders still in staff review shortly before the provider deadline are resolved
    per the integration's ``on_review_timeout`` policy (default: reject with reason).
    Failed retryable acknowledgements are re-sent.
    """
    from .services import manual_decision, sync_status

    soon = timezone.now() + timedelta(seconds=60)
    for order in Order.objects.filter(status=OrderStatus.PENDING_REVIEW, provider_ack_deadline__lte=soon,
                                      store_integration__isnull=False).select_related("store_integration"):
        policy = (order.store_integration.settings or {}).get("on_review_timeout", "reject")
        manual_decision(order, accept=(policy == "accept"), user=None,
                        reason="Accepted automatically before provider deadline" if policy == "accept"
                        else "ITEM_UNAVAILABLE: not reviewed before provider deadline")
    for order in Order.objects.filter(provider_sync_status=ProviderSyncStatus.FAILED,
                                      status__in=[OrderStatus.ACCEPTED, OrderStatus.REJECTED],
                                      provider_ack_deadline__gt=timezone.now()):
        try:
            sync_status(order, order.status)
        except ProviderError:
            continue


@shared_task
def retry_failed_webhook_events():
    now = timezone.now()
    for ev in WebhookEvent.objects.filter(status=WebhookEvent.STATUS_FAILED, next_retry_at__lte=now)[:100]:
        process_webhook_event_task.delay(ev.pk)


@shared_task
def push_menu_task(store_integration_id: int, user_id: int | None = None):
    from apps.accounts.models import User

    from .models import StoreIntegration
    from .services import push_menu

    si = StoreIntegration.objects.select_related("provider", "account", "store").get(pk=store_integration_id)
    push_menu(si, User.objects.filter(pk=user_id).first() if user_id else None)
