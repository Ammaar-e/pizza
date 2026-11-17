"""Integration orchestration: webhook ingestion → intake engine → provider sync."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.audit import audit
from apps.core.exceptions import DomainError
from apps.orders.intake import AcceptancePolicy, OrderIntakeService
from apps.orders.models import Order, OrderStatus, ProviderSyncStatus
from apps.orders.state import log_event, transition

from .adapters import ProviderError, get_adapter
from .adapters.base import ProviderUnsupported
from .models import ConnectionStatus, IntegrationLog, StoreIntegration, SyncRecord, WebhookEvent

logger = logging.getLogger("integrations")

PROVIDER_CANCEL_PREFIX = "Cancelled by provider"


class EventProcessingError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def find_store_integration(provider, external_store_id: str, account=None) -> StoreIntegration | None:
    qs = StoreIntegration.objects.select_related("store__franchisee", "account", "provider").filter(
        provider=provider, external_store_id=external_store_id)
    if account is not None:
        qs = qs.filter(account=account)
    si = qs.first()
    if si is None and provider.code == "doordash" and external_store_id:
        # DoorDash merchant_supplied_id defaults to our store number when not set explicitly.
        si = StoreIntegration.objects.select_related("store__franchisee", "account", "provider").filter(
            provider=provider, external_store_id="", store__number=external_store_id).first()
    return si


def mark_success(si: StoreIntegration):
    StoreIntegration.objects.filter(pk=si.pk).update(last_success_at=timezone.now(),
                                                      connection_status=ConnectionStatus.CONNECTED)


def mark_error(si: StoreIntegration | None, message: str):
    if si is None:
        return
    StoreIntegration.objects.filter(pk=si.pk).update(last_error_at=timezone.now(), last_error=message[:2000],
                                                      connection_status=ConnectionStatus.ERROR)


def log(provider, si, message, level="info", order=None, operation="event", direction="inbound"):
    IntegrationLog.objects.create(provider=provider, store_integration=si, order=order, level=level,
                                  direction=direction, operation=operation, message=message[:4000])


# ---------------------------------------------------------------------------
# Inbound events
# ---------------------------------------------------------------------------
def process_webhook_event(event: WebhookEvent) -> WebhookEvent:
    with transaction.atomic():
        ev = WebhookEvent.objects.select_for_update(of=("self",)).select_related("provider", "account").get(
            pk=event.pk)
        if ev.status in (WebhookEvent.STATUS_PROCESSED, WebhookEvent.STATUS_IGNORED):
            return ev
        ev.status = WebhookEvent.STATUS_PROCESSING
        ev.attempts += 1
        ev.save(update_fields=["status", "attempts"])
    adapter = get_adapter(ev.provider)
    si = None
    try:
        parsed = adapter.parse_events(ev.payload)[0]
        if parsed.kind == "ignored":
            _finish(ev, WebhookEvent.STATUS_IGNORED, f"Event type '{ev.event_type}' not handled")
            return ev
        si = find_store_integration(ev.provider, parsed.external_store_id, ev.account)
        if si is None:
            raise EventProcessingError(
                f"No store is connected to {ev.provider.name} store id '{parsed.external_store_id}'. "
                "Configure the store's integration (store/merchant identifier) in the franchise portal.")
        ev.store_integration = si
        ev.save(update_fields=["store_integration"])
        if parsed.kind == "order_new":
            _handle_new_order(adapter, ev, si, parsed)
        elif parsed.kind == "order_cancelled":
            _handle_cancel(ev, si, parsed)
        elif parsed.kind == "menu_status":
            _handle_menu_status(ev, si, parsed)
        _finish(ev, WebhookEvent.STATUS_PROCESSED)
    except (EventProcessingError, ProviderError) as exc:
        retryable = getattr(exc, "retryable", False)
        _fail(ev, si, str(exc), retryable)
    except DomainError as exc:
        _fail(ev, si, f"Order rejected by intake ({exc.code}): {exc.detail}", False)
    except Exception as exc:  # unexpected bug: keep event for retry, record actionable error
        logger.exception("Webhook event %s processing crashed", ev.pk)
        _fail(ev, si, f"Internal error: {exc.__class__.__name__}: {exc}", True)
    return ev


def _finish(ev: WebhookEvent, status: str, note: str = ""):
    ev.status = status
    ev.processed_at = timezone.now()
    ev.last_error = note
    ev.next_retry_at = None
    ev.save(update_fields=["status", "processed_at", "last_error", "next_retry_at", "order"])


def _fail(ev: WebhookEvent, si, message: str, retryable: bool):
    ev.status = WebhookEvent.STATUS_FAILED
    ev.last_error = message[:4000]
    ev.next_retry_at = timezone.now() + timedelta(seconds=min(30 * (2 ** ev.attempts), 900)) \
        if retryable and ev.attempts < 6 else None
    ev.save(update_fields=["status", "last_error", "next_retry_at", "store_integration", "order"])
    log(ev.provider, si, f"Event {ev.event_id} failed: {message}", level="error", order=ev.order)
    mark_error(si, message)


def _handle_new_order(adapter, ev: WebhookEvent, si: StoreIntegration, parsed):
    if not ev.provider.network_enabled:
        raise EventProcessingError(f"{ev.provider.name} is not permitted by headquarters")
    if not si.enabled:
        raise EventProcessingError(f"{ev.provider.name} integration is disabled for store {si.store.number}")
    existing = Order.objects.filter(source=ev.provider.code, external_order_id=parsed.external_order_id).first()
    if existing:
        ev.order = existing
        log(ev.provider, si, f"Duplicate delivery of order {parsed.external_order_id} ignored", order=existing)
        return
    raw = adapter.fetch_order(si, ev)
    norm = adapter.normalize(si, raw)
    policy = AcceptancePolicy.from_dict((si.settings or {}).get("acceptance_policy"))
    result = OrderIntakeService(store_integration=si, policy=policy).submit(norm)
    ev.order = result.order
    if result.created:
        from apps.payments.services import record_provider_payment

        record_provider_payment(result.order)
    log(ev.provider, si, f"Order {parsed.external_order_id} → #{result.order.order_number} ({result.decision})",
        order=result.order)
    mark_success(si)


def _handle_cancel(ev: WebhookEvent, si, parsed):
    order = Order.objects.filter(source=ev.provider.code, external_order_id=parsed.external_order_id).first()
    if order is None:
        raise EventProcessingError(f"Cancellation for unknown order {parsed.external_order_id}", retryable=True)
    ev.order = order
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.COMPLETED):
        log(ev.provider, si, f"Cancellation for order in status {order.status} ignored", order=order)
        return
    transition(order, OrderStatus.CANCELLED, reason=f"{PROVIDER_CANCEL_PREFIX} ({ev.provider.name})",
               data={"provider_initiated": True, "event_id": ev.event_id})
    Order.objects.filter(pk=order.pk).update(provider_sync_status=ProviderSyncStatus.SYNCED)


def _handle_menu_status(ev: WebhookEvent, si, parsed):
    payload = parsed.data or {}
    event = payload.get("event") or {}
    menu_id = (payload.get("menu") or {}).get("id")
    status = event.get("status", "").upper()
    si.menu_sync_status = "succeeded" if status == "SUCCESS" else "failed"
    si.menu_synced_at = timezone.now()
    if menu_id:
        si.external_menu_id = menu_id
    if status != "SUCCESS":
        si.last_error = f"Menu sync failed: {event.get('details', 'no details')}"
        si.last_error_at = timezone.now()
    si.save()
    SyncRecord.objects.filter(store_integration=si, kind=SyncRecord.KIND_MENU, reference=event.get("reference", "")) \
        .update(status=si.menu_sync_status, detail=event.get("details", "") or "", completed_at=timezone.now())


# ---------------------------------------------------------------------------
# Outbound sync
# ---------------------------------------------------------------------------
def _already(order: Order, kind: str, reference: str) -> bool:
    return SyncRecord.objects.filter(order=order, kind=kind, reference=reference, status="succeeded").exists()


def sync_status(order: Order, status: str) -> SyncRecord | None:
    """Push an order status change to its provider. Returns the SyncRecord (or None if n/a)."""
    si = order.store_integration
    if si is None:
        return None
    adapter = get_adapter(si.provider)
    if status == OrderStatus.ACCEPTED or status == OrderStatus.REJECTED:
        kind, ref = SyncRecord.KIND_ORDER_ACK, status
        if _already(order, kind, "accepted") or _already(order, kind, "rejected"):
            return None
        if status == OrderStatus.ACCEPTED:
            call = lambda: adapter.accept(si, order)  # noqa: E731
        else:
            reason = order.rejection_reason or "ITEM_UNAVAILABLE"
            msg = "; ".join(i.get("message", "") for i in (order.validation_issues or [])) or reason
            call = lambda: adapter.reject(si, order, reason.split(";")[0].strip().upper(), msg)  # noqa: E731
    elif status == OrderStatus.READY:
        if not (si.settings or {}).get("send_ready_signal", True):
            return None
        kind, ref = SyncRecord.KIND_ORDER_STATUS, "ready"
        if _already(order, kind, ref):
            return None
        call = lambda: adapter.mark_ready(si, order)  # noqa: E731
    elif status == OrderStatus.CANCELLED:
        if (order.cancellation_reason or "").startswith(PROVIDER_CANCEL_PREFIX):
            return None
        kind, ref = SyncRecord.KIND_ORDER_STATUS, "cancelled"
        if _already(order, kind, ref):
            return None
        if not order.accepted_at:
            # Never acknowledged: reject instead of cancel.
            kind, ref = SyncRecord.KIND_ORDER_ACK, "rejected"
            call = lambda: adapter.reject(si, order, "OTHER", order.cancellation_reason or "Cancelled by store")  # noqa
        else:
            call = lambda: adapter.cancel(si, order, "OTHER", order.cancellation_reason or "Cancelled by store")  # noqa
    else:
        return None

    rec = SyncRecord.objects.create(store_integration=si, kind=kind, order=order, reference=ref, attempts=1)
    try:
        detail = call()
        rec.status, rec.detail, rec.completed_at = "succeeded", str(detail), timezone.now()
        rec.save()
        Order.objects.filter(pk=order.pk).update(provider_sync_status=ProviderSyncStatus.SYNCED, provider_sync_error="")
        log_event(order, "provider_sync", f"{si.provider.name}: {ref} synced", {"detail": str(detail)})
        mark_success(si)
    except ProviderUnsupported as exc:
        rec.status, rec.detail, rec.completed_at = "unsupported", str(exc), timezone.now()
        rec.save()
        log_event(order, "provider_sync_unsupported", str(exc))
        if ref == "cancelled":
            Order.objects.filter(pk=order.pk).update(
                requires_attention=True, provider_sync_status=ProviderSyncStatus.FAILED,
                attention_reason=f"Cancel this order in the {si.provider.name} merchant tools: API cancellation "
                                 "is not available")
    except ProviderError as exc:
        rec.status, rec.detail = "failed", str(exc)[:2000]
        rec.save()
        Order.objects.filter(pk=order.pk).update(provider_sync_status=ProviderSyncStatus.FAILED,
                                                  provider_sync_error=str(exc)[:2000], requires_attention=True,
                                                  attention_reason=f"{si.provider.name} sync failed: {ref}")
        log_event(order, "provider_sync_failed", str(exc), {"retryable": exc.retryable})
        mark_error(si, str(exc))
        if exc.retryable:
            raise
    return rec


def push_menu(si: StoreIntegration, user=None) -> SyncRecord:
    adapter = get_adapter(si.provider)
    rec = SyncRecord.objects.create(store_integration=si, kind=SyncRecord.KIND_MENU, attempts=1)
    try:
        ref = adapter.push_menu(si)
        rec.reference = ref
        rec.status = "awaiting_callback" if adapter.code == "doordash" else "succeeded"
        si.menu_sync_status = "pending" if adapter.code == "doordash" else "succeeded"
        si.save(update_fields=["menu_sync_status", "updated_at"])
    except ProviderUnsupported as exc:
        rec.status, rec.detail = "unsupported", str(exc)
    except ProviderError as exc:
        rec.status, rec.detail = "failed", str(exc)[:2000]
        si.menu_sync_status = "failed"
        si.save(update_fields=["menu_sync_status", "updated_at"])
        mark_error(si, str(exc))
    rec.save()
    audit("integration.menu_push", si, actor=user, store=si.store, data={"status": rec.status})
    return rec


def test_connection(si: StoreIntegration, user=None) -> tuple[bool, str]:
    adapter = get_adapter(si.provider)
    try:
        msg = adapter.test_connection(si)
        mark_success(si)
        ok = True
    except ProviderError as exc:
        msg = str(exc)
        status = ConnectionStatus.PENDING_ACCESS if not adapter.available else ConnectionStatus.ERROR
        StoreIntegration.objects.filter(pk=si.pk).update(connection_status=status, last_error=msg[:2000],
                                                          last_error_at=timezone.now())
        ok = False
    log(si.provider, si, f"Test connection: {'OK' if ok else 'FAILED'} — {msg}", level="info" if ok else "error",
        operation="test_connection", direction="outbound")
    audit("integration.test_connection", si, actor=user, store=si.store, data={"ok": ok})
    return ok, msg


def manual_decision(order: Order, accept: bool, user, reason: str = "") -> Order:
    if order.status not in (OrderStatus.PENDING_REVIEW, OrderStatus.RECEIVED):
        from apps.core.exceptions import ConflictError

        raise ConflictError("Order is not awaiting a decision", code="not_pending")
    target = OrderStatus.ACCEPTED if accept else OrderStatus.REJECTED
    transition(order, target, actor=user, reason=reason or ("Accepted by staff" if accept else "ITEM_UNAVAILABLE"))
    audit("order.manual_decision", order, actor=user, data={"accept": accept, "reason": reason})
    return order
