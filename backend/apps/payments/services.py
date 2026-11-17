from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.services import authorize_manager
from apps.core.audit import audit
from apps.core.exceptions import ConflictError, DomainError
from apps.orders.models import Order, OrderStatus, PaymentStatus
from apps.orders.state import log_event

from .models import CashSession, CashTransaction, Payment, PaymentMethod, PaymentState, Refund
from .processors import ProcessorError, ProcessorNotConfigured, processor_for

PAYMENT_TRANSITIONS = {
    PaymentState.PENDING: {PaymentState.AUTHORIZED, PaymentState.CAPTURED, PaymentState.FAILED, PaymentState.VOIDED},
    PaymentState.AUTHORIZED: {PaymentState.CAPTURED, PaymentState.VOIDED, PaymentState.FAILED},
    PaymentState.CAPTURED: {PaymentState.PARTIALLY_REFUNDED, PaymentState.REFUNDED},
    PaymentState.PARTIALLY_REFUNDED: {PaymentState.PARTIALLY_REFUNDED, PaymentState.REFUNDED},
    PaymentState.FAILED: set(),
    PaymentState.VOIDED: set(),
    PaymentState.REFUNDED: set(),
}


def set_payment_state(payment: Payment, target: str) -> None:
    if payment.status == target and target != PaymentState.PARTIALLY_REFUNDED:
        return
    if target not in PAYMENT_TRANSITIONS.get(payment.status, set()):
        raise ConflictError(f"Payment cannot move from '{payment.status}' to '{target}'",
                            code="invalid_payment_transition")
    payment.status = target


def recompute_order_payment_status(order: Order) -> str:
    if order.payment_status == PaymentStatus.PROVIDER_PAID and not order.payments.exclude(
            method=PaymentMethod.PROVIDER).exists():
        return order.payment_status
    settled = order.payments.filter(status__in=Payment.SETTLED_STATUSES)
    paid = sum(p.amount_cents for p in settled)
    refunded = sum(p.refunded_cents for p in settled)
    if paid == 0:
        status = PaymentStatus.UNPAID
    elif refunded and refunded >= paid:
        status = PaymentStatus.REFUNDED
    elif refunded:
        status = PaymentStatus.PARTIALLY_REFUNDED
    elif settled.filter(method=PaymentMethod.PROVIDER).exists():
        status = PaymentStatus.PROVIDER_PAID
    elif paid >= order.total_cents:
        status = PaymentStatus.PAID
    else:
        status = PaymentStatus.PARTIALLY_PAID
    Order.objects.filter(pk=order.pk).update(payment_status=status)
    order.payment_status = status
    return status


def open_session_for(store, device) -> CashSession | None:
    """The open register session on the register attached to ``device``."""
    if device is None:
        return None
    return CashSession.objects.filter(store=store, register__device=device, status=CashSession.STATUS_OPEN).first()


def take_payment(order: Order, *, method: str, amount_cents: int, user, tendered_cents: int | None = None,
                 tip_cents: int = 0, reference: str = "", device=None, idempotency_key: str | None = None) -> Payment:
    if method == PaymentMethod.PROVIDER:
        raise DomainError("Provider payments are recorded automatically", code="invalid_method")
    if order.is_third_party:
        raise DomainError("Third-party orders are paid through the provider and must not be charged again",
                          code="provider_paid_order")
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
        raise ConflictError("Cannot take payment for a cancelled/rejected order", code="order_closed")
    if amount_cents <= 0:
        raise DomainError("Amount must be positive", code="invalid_amount", status_code=400)
    if idempotency_key:
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.order_id != order.pk:
                raise ConflictError("Idempotency key belongs to another order", code="idempotency_key_reused")
            return existing
    outstanding = order.total_cents - sum(
        p.net_amount_cents for p in order.payments.filter(status__in=Payment.SETTLED_STATUSES))
    if amount_cents > outstanding:
        raise DomainError(f"Amount exceeds outstanding balance ({outstanding})", code="overpayment")

    session = None
    if method == PaymentMethod.CASH:
        session = open_session_for(order.store, device)
        if session is None:
            raise DomainError("Open a cash session before accepting cash", code="no_cash_session")
        from apps.devices.services import feature_enabled

        if not feature_enabled(device, "cash_drawer"):
            raise DomainError("Cash handling is disabled on this device", code="feature_disabled", status_code=403)

    processor = processor_for(method, order.store)
    with transaction.atomic():
        payment = Payment.objects.create(
            order=order, store=order.store, method=method, processor=processor.name, amount_cents=amount_cents,
            tip_cents=tip_cents, tendered_cents=tendered_cents, cash_session=session, created_by=user,
            idempotency_key=idempotency_key,
        )
    try:
        result = processor.charge(amount_cents=amount_cents, tendered_cents=tendered_cents, reference=reference,
                                  currency=order.currency, idempotency_key=str(payment.pk), order_ref=str(order.pk))
    except ProcessorNotConfigured as e:
        result_status, reason = PaymentState.FAILED, str(e)
        payment.failure_reason = reason
        set_payment_state(payment, result_status)
        payment.save()
        raise DomainError(reason, code="processor_not_configured")
    except ProcessorError as e:
        payment.failure_reason = str(e)[:300]
        set_payment_state(payment, PaymentState.FAILED)
        payment.save()
        raise DomainError(f"Card processor error: {e}", code="processor_error", status_code=502)

    payment.processor_reference = result.reference
    payment.card_brand = result.card_brand
    payment.card_last4 = result.card_last4
    payment.failure_reason = result.failure_reason
    set_payment_state(payment, result.status)
    with transaction.atomic():
        if method == PaymentMethod.CASH and result.status == PaymentState.CAPTURED:
            payment.change_cents = (tendered_cents or 0) - amount_cents
            from .cash import record_ledger

            # Net cash kept in the drawer = amount applied (+ cash tip); change is not a drawer movement.
            record_ledger(session, CashTransaction.SALE, amount_cents + (tip_cents or 0), user=user,
                          reason=f"Order #{order.order_number}", order=order, payment=payment)
        payment.save()
    recompute_order_payment_status(order)
    log_event(order, "payment", f"{method} payment {payment.status}", {"payment_id": str(payment.pk),
                                                                       "amount_cents": amount_cents}, user)
    audit("payment.create", payment, actor=user, store=order.store,
          data={"order": str(order.pk), "method": method, "amount_cents": amount_cents, "status": payment.status})
    return payment


def confirm_card_payment(payment: Payment, user) -> Payment:
    if payment.method != PaymentMethod.CARD or payment.status != PaymentState.PENDING:
        return payment
    proc = processor_for("card", payment.store)
    result = proc.confirm(payment.processor_reference)
    if result.status != payment.status:
        set_payment_state(payment, result.status)
        payment.card_brand = result.card_brand or payment.card_brand
        payment.card_last4 = result.card_last4 or payment.card_last4
        payment.failure_reason = result.failure_reason
        payment.save()
        recompute_order_payment_status(payment.order)
    return payment


def record_provider_payment(order: Order) -> Payment | None:
    """Third-party orders: record the provider's collection for settlement reconciliation."""
    if not order.is_third_party:
        return None
    existing = order.payments.filter(method=PaymentMethod.PROVIDER).first()
    if existing:
        return existing
    p = Payment.objects.create(order=order, store=order.store, method=PaymentMethod.PROVIDER,
                               processor=order.source, amount_cents=order.total_cents,
                               status=PaymentState.CAPTURED, processor_reference=order.external_order_id or "",
                               metadata={"provider_totals": order.provider_totals})
    return p


def refund_payment(payment: Payment, *, amount_cents: int, reason: str, user, manager_code: str | None = None,
                   device=None) -> Refund:
    if not reason:
        raise DomainError("Refund reason is required", code="reason_required", status_code=400)
    if payment.method == PaymentMethod.PROVIDER:
        raise DomainError("Provider-paid orders are refunded through the provider's merchant tools",
                          code="provider_refund")
    if payment.status not in (PaymentState.CAPTURED, PaymentState.PARTIALLY_REFUNDED):
        raise ConflictError("Only captured payments can be refunded", code="not_refundable")
    refundable = payment.amount_cents - payment.refunded_cents
    if amount_cents <= 0 or amount_cents > refundable:
        raise DomainError(f"Refund amount must be between 1 and {refundable}", code="invalid_refund_amount")
    manager = authorize_manager(payment.store, manager_code, "payments.refund", user)
    session = None
    if payment.method == PaymentMethod.CASH:
        session = open_session_for(payment.store, device)
        if session is None and payment.cash_session and payment.cash_session.status == CashSession.STATUS_OPEN:
            session = payment.cash_session
        if session is None:
            raise DomainError("Cash refunds must be paid out of an open register session on this device",
                              code="no_cash_session")
        from apps.devices.services import feature_enabled

        if not feature_enabled(device, "refunds"):
            raise DomainError("Refunds are disabled on this device", code="feature_disabled", status_code=403)
    proc = processor_for(payment.method, payment.store)
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if amount_cents > locked.amount_cents - locked.refunded_cents:
            raise ConflictError("Refund exceeds remaining refundable amount", code="invalid_refund_amount")
        refund = Refund.objects.create(payment=locked, amount_cents=amount_cents, reason=reason,
                                       requested_by=user, authorized_by=manager, cash_session=session)
        try:
            result = proc.refund(locked, amount_cents)
        except ProcessorError as e:
            refund.status = Refund.STATUS_FAILED
            refund.failure_reason = str(e)[:300]
            refund.save()
            raise DomainError(f"Refund failed: {e}", code="refund_failed", status_code=502)
        refund.processor_reference = result.reference
        if result.status == "captured":
            refund.status = Refund.STATUS_SUCCEEDED
            locked.refunded_cents += amount_cents
            set_payment_state(locked, PaymentState.REFUNDED if locked.refunded_cents >= locked.amount_cents
                              else PaymentState.PARTIALLY_REFUNDED)
            locked.save()
            if session is not None:
                from .cash import record_ledger

                refund.save()
                record_ledger(session, CashTransaction.REFUND, amount_cents, user=user, reason=reason[:200],
                              order=payment.order, payment=locked, refund=refund, authorized_by=manager)
        else:
            refund.failure_reason = result.failure_reason
        refund.save()
    recompute_order_payment_status(payment.order)
    log_event(payment.order, "refund", f"Refund {refund.status}: {reason}",
              {"refund_id": str(refund.pk), "amount_cents": amount_cents}, user)
    audit("payment.refund", refund, entity_type="Refund", actor=user, authorized_by=manager, store=payment.store,
          data={"payment": str(payment.pk), "amount_cents": amount_cents, "reason": reason, "status": refund.status})
    return refund


# Cash register sessions, the cash ledger, reconciliation and shift closing live in payments.cash.
from .cash import close_session as close_cash_session  # noqa: E402,F401  (backwards-compatible names)
from .cash import expected_cash  # noqa: E402,F401
from .cash import open_session as _open_session  # noqa: E402


def open_cash_session(store, device, user, opening_float_cents: int) -> CashSession:
    from .cash import register_for_device

    return _open_session(register_for_device(device), user=user, employee=user,
                         opening_float_cents=opening_float_cents, device=device)
