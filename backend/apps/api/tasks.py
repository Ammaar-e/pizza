from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import OutboundWebhook


def enqueue_order_status_webhook(order, from_status: str, to_status: str) -> None:
    client = order.api_client
    if client is None or not client.webhook_url or not client.is_active:
        return
    payload = {
        "id": uuid.uuid4().hex,
        "type": "order.status_changed",
        "created_at": timezone.now().isoformat(),
        "data": {"order_id": str(order.pk), "order_number": order.order_number, "store_number": order.store.number,
                 "previous_status": from_status, "status": to_status,
                 "rejection_reason": order.rejection_reason or None,
                 "cancellation_reason": order.cancellation_reason or None},
    }
    wh = OutboundWebhook.objects.create(client=client, order=order, event_id=payload["id"],
                                        event_type=payload["type"], payload=payload, next_attempt_at=timezone.now())
    transaction.on_commit(lambda: deliver_outbound_webhook.delay(wh.pk))


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


@shared_task
def deliver_outbound_webhook(pk: int):
    wh = OutboundWebhook.objects.select_related("client").filter(pk=pk, status="pending").first()
    if wh is None:
        return
    secret = (wh.client.webhook_secret or {}).get("secret", "")
    body = json.dumps(wh.payload, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-Pizza-Event-Id": wh.event_id, "X-Pizza-Timestamp": ts,
               "X-Pizza-Signature": f"sha256={sign(secret, ts, body)}"}
    wh.attempts += 1
    try:
        r = requests.post(wh.client.webhook_url, data=body, headers=headers, timeout=settings.PROVIDER_HTTP_TIMEOUT)
        wh.last_status_code = r.status_code
        ok = 200 <= r.status_code < 300
        wh.last_error = "" if ok else r.text[:500]
    except requests.RequestException as exc:
        ok = False
        wh.last_error = exc.__class__.__name__
    if ok:
        wh.status, wh.delivered_at, wh.next_attempt_at = "delivered", timezone.now(), None
    elif wh.attempts >= 8:
        wh.status, wh.next_attempt_at = "failed", None
    else:
        wh.next_attempt_at = timezone.now() + timedelta(seconds=min(30 * 2 ** wh.attempts, 3600))
    wh.save()


@shared_task
def retry_outbound_webhooks():
    for pk in OutboundWebhook.objects.filter(status="pending", next_attempt_at__lte=timezone.now()).values_list(
            "pk", flat=True)[:200]:
        deliver_outbound_webhook.delay(pk)
