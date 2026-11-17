"""Cash management: register sessions, the cash ledger, reconciliation, discrepancies and shift closing.

Rules:
* Every drawer movement is a ``CashTransaction`` (immutable, signed). Expected cash is always
  computed from the ledger — never entered by hand.
* All financial operations run in a database transaction with the session row locked.
* Finalized sessions are immutable; later findings are post-close ``correction`` transactions
  authorized by a manager, shown as adjustments on reports.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounts.services import authorize_manager, user_can_operate_store
from apps.core.audit import audit
from apps.core.exceptions import ConflictError, DomainError
from apps.orders.realtime import broadcast

from .models import (
    CashDiscrepancy,
    CashReconciliation,
    CashSession,
    CashTransaction,
    Payment,
    PaymentMethod,
    Refund,
    Register,
    ShiftClosing,
)

T = CashTransaction
DEFAULT_THRESHOLD_CENTS = 500


def variance_threshold(store) -> int:
    v = (store.payment_config or {}).get("cash_variance_threshold_cents")
    if v is None:
        v = (store.franchisee.headquarters.policies or {}).get("cash_variance_threshold_cents", DEFAULT_THRESHOLD_CENTS)
    return int(v)


def close_requires_approval(store) -> bool:
    return bool((store.payment_config or {}).get("cash_close_requires_approval")
                or (store.franchisee.headquarters.policies or {}).get("cash_close_requires_approval"))


def _push(session: CashSession, event: str):
    transaction.on_commit(lambda: broadcast(session.store_id, "pos", {"event": event, "session": {
        "id": str(session.pk), "register": session.register.code if session.register else None,
        "status": session.status, "employee": session.employee.username if session.employee else None}}))


# ---------------------------------------------------------------------------
# Registers & sessions
# ---------------------------------------------------------------------------
def register_for_device(device) -> Register:
    if device is None:
        raise DomainError("Cash sessions must be opened on a registered POS device", code="device_required",
                          status_code=400)
    reg = Register.objects.filter(device=device).first()
    if reg:
        if reg.store_id != device.store_id:
            Register.objects.filter(pk=reg.pk).update(store=device.store)
            reg.store = device.store
        return reg
    code = f"R{device.pk}"
    reg, _ = Register.objects.get_or_create(store=device.store, code=code,
                                            defaults={"device": device, "name": f"Register — {device.name}"})
    if reg.device_id is None:
        reg.device = device
        reg.save(update_fields=["device", "updated_at"])
    return reg


def open_session(register: Register, *, user, employee, opening_float_cents: int, device=None) -> CashSession:
    from apps.devices.services import feature_enabled

    if not register.is_active:
        raise DomainError("Register is inactive", code="register_inactive")
    if opening_float_cents < 0:
        raise DomainError("Opening float cannot be negative", code="invalid_amount", status_code=400)
    if not feature_enabled(device or register.device, "cash_drawer"):
        raise DomainError("Cash handling is disabled on this device", code="feature_disabled", status_code=403)
    if employee is None or not employee.has_permission("cash.manage_session") or not user_can_operate_store(
            employee, register.store):
        raise DomainError("Employee is not authorized to operate a register at this store",
                          code="employee_not_authorized", status_code=403)
    if employee != user and not user.has_permission("cash.review"):
        raise DomainError("Only managers can open a register for another employee", code="forbidden",
                          status_code=403)
    with transaction.atomic():
        Register.objects.select_for_update().get(pk=register.pk)
        if CashSession.objects.filter(register=register, status=CashSession.STATUS_OPEN).exists():
            raise ConflictError("This register already has an open session", code="session_open")
        try:
            s = CashSession.objects.create(store=register.store, register=register, device=register.device,
                                           employee=employee, opened_by=user, opening_float_cents=opening_float_cents)
        except IntegrityError:
            raise ConflictError("This register already has an open session", code="session_open")
        audit("cash_session.open", s, entity_type="CashSession", actor=user, store=register.store,
              data={"register": register.code, "employee": employee.username, "opening_float_cents": opening_float_cents})
        _push(s, "cash.session_opened")
    return s


def record_ledger(session: CashSession, kind: str, amount_cents: int, *, user, reason="", order=None, payment=None,
                  refund=None, authorized_by=None, reference="", expense_category="", counterpart=None,
                  post_close=False, idempotency_key=None, signed=False) -> CashTransaction:
    """Low-level ledger write. ``amount_cents`` is positive unless ``signed`` (corrections)."""
    if not signed:
        if amount_cents <= 0:
            raise DomainError("Amount must be positive", code="invalid_amount", status_code=400)
        amount_cents = -amount_cents if kind in T.OUTFLOWS else amount_cents
    elif amount_cents == 0:
        raise DomainError("Correction amount cannot be zero", code="invalid_amount", status_code=400)
    return CashTransaction.objects.create(
        session=session, kind=kind, amount_cents=amount_cents, reason=reason[:200], order=order, payment=payment,
        refund=refund, user=user, authorized_by=authorized_by, reference=reference[:100],
        expense_category=expense_category[:50], counterpart_session=counterpart, post_close=post_close,
        idempotency_key=idempotency_key)


MANUAL_KINDS = {T.DROP, T.ADDITION, T.PAID_OUT, T.PETTY_EXPENSE, T.CORRECTION}


def add_transaction(session: CashSession, *, kind: str, amount_cents: int, user, reason: str = "",
                    manager_code: str | None = None, reference: str = "", expense_category: str = "",
                    idempotency_key: str | None = None) -> CashTransaction:
    """Manual drawer transaction from the POS. Corrections may be negative and require manager
    authorization; they are the only transaction allowed on a finalized session."""
    if kind not in MANUAL_KINDS:
        raise DomainError(f"'{kind}' cannot be recorded manually", code="invalid_kind", status_code=400)
    if idempotency_key:
        existing = CashTransaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.session_id != session.pk or existing.kind != kind or abs(existing.amount_cents) != abs(
                    amount_cents):
                raise ConflictError("Idempotency key was reused for a different transaction",
                                    code="idempotency_key_reused")
            return existing
    if kind != T.DROP and not reason:
        raise DomainError("A reason is required", code="reason_required", status_code=400)
    manager = None
    if kind == T.CORRECTION:
        manager = authorize_manager(session.store, manager_code, "cash.correct", user)
        if manager == user and session.employee_id == user.pk and not user.has_permission("cash.review"):
            raise DomainError("Corrections to your own drawer need another manager", code="segregation_of_duties",
                              status_code=403)
    elif kind == T.DROP:
        if user.pk != session.employee_id and not user.has_permission("cash.review"):
            raise DomainError("Only the drawer's employee or a manager can record a drop", code="forbidden",
                              status_code=403)
    elif not user.has_permission("cash.paid_in_out"):
        manager = authorize_manager(session.store, manager_code, "cash.paid_in_out", user)
    with transaction.atomic():
        locked = CashSession.objects.select_for_update().get(pk=session.pk)
        post_close = locked.is_finalized
        if post_close and kind != T.CORRECTION:
            raise ConflictError("Session is closed; only manager-authorized corrections can be recorded",
                                code="session_closed")
        if kind in (T.DROP, T.PAID_OUT, T.PETTY_EXPENSE) and not post_close:
            available = expected_cash(locked)
            if abs(amount_cents) > available:
                raise DomainError(f"Drawer only holds {available} cents", code="insufficient_cash")
        try:
            txn = record_ledger(locked, kind, amount_cents, user=user, reason=reason, authorized_by=manager,
                                reference=reference, expense_category=expense_category, post_close=post_close,
                                idempotency_key=idempotency_key, signed=(kind == T.CORRECTION))
        except IntegrityError:
            existing = CashTransaction.objects.get(idempotency_key=idempotency_key)
            return existing
        audit(f"cash.{kind}", txn, entity_type="CashTransaction", actor=user, authorized_by=manager,
              store=locked.store, data={"session": str(locked.pk), "amount_cents": txn.amount_cents, "reason": reason,
                                        "post_close": post_close})
        _push(locked, "cash.transaction")
    return txn


def transfer(source: CashSession, target: CashSession, *, amount_cents: int, user, reason: str,
             manager_code: str | None = None) -> tuple[CashTransaction, CashTransaction]:
    if source.pk == target.pk or source.store_id != target.store_id:
        raise DomainError("Transfers are only allowed between different registers of the same store",
                          code="invalid_transfer", status_code=400)
    manager = authorize_manager(source.store, manager_code, "cash.paid_in_out", user)
    with transaction.atomic():
        # Lock both sessions in a deterministic order to avoid deadlocks.
        locked = {s.pk: s for s in CashSession.objects.select_for_update().filter(
            pk__in=[source.pk, target.pk]).order_by("pk")}
        src, dst = locked[source.pk], locked[target.pk]
        if src.is_finalized or dst.is_finalized:
            raise ConflictError("Both register sessions must be open", code="session_closed")
        if amount_cents <= 0 or amount_cents > expected_cash(src):
            raise DomainError("Transfer amount exceeds cash in the source drawer", code="insufficient_cash")
        out = record_ledger(src, T.TRANSFER_OUT, amount_cents, user=user, reason=reason, authorized_by=manager,
                            counterpart=dst)
        inn = record_ledger(dst, T.TRANSFER_IN, amount_cents, user=user, reason=reason, authorized_by=manager,
                            counterpart=src)
        audit("cash.transfer", out, entity_type="CashTransaction", actor=user, authorized_by=manager,
              store=src.store, data={"from": str(src.pk), "to": str(dst.pk), "amount_cents": amount_cents})
        _push(src, "cash.transaction")
    return out, inn


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------
def breakdown(session: CashSession, include_post_close: bool = False) -> dict:
    qs = session.transactions.all() if include_post_close else session.transactions.filter(post_close=False)
    sums = {k: v or 0 for k, v in qs.values("kind").annotate(s=Sum("amount_cents")).values_list("kind", "s")}
    b = {
        "opening_float_cents": session.opening_float_cents,
        "cash_sales_cents": sums.get(T.SALE, 0),
        "cash_refunds_cents": -sums.get(T.REFUND, 0),
        "drops_cents": -sums.get(T.DROP, 0),
        "additions_cents": sums.get(T.ADDITION, 0),
        "paid_outs_cents": -sums.get(T.PAID_OUT, 0),
        "petty_expenses_cents": -sums.get(T.PETTY_EXPENSE, 0),
        "corrections_cents": sums.get(T.CORRECTION, 0),
        "transfers_in_cents": sums.get(T.TRANSFER_IN, 0),
        "transfers_out_cents": -sums.get(T.TRANSFER_OUT, 0),
    }
    b["expected_cents"] = session.opening_float_cents + sum(sums.values())
    return b


def expected_cash(session: CashSession) -> int:
    return breakdown(session)["expected_cents"]


def close_session(session: CashSession, user, counted_cash_cents: int, notes: str = "", *, explanation: str = "",
                  denominations: dict | None = None) -> CashSession:
    """Blind-count close: the employee enters the counted amount; the server computes expected cash
    and the variance. Variances beyond the threshold require an explanation and manager approval."""
    if counted_cash_cents < 0:
        raise DomainError("Counted cash cannot be negative", code="invalid_amount", status_code=400)
    if user.pk != session.employee_id and not (user.has_permission("cash.review") or user.pk == session.opened_by_id):
        raise DomainError("Only the drawer's employee or a manager can close this register", code="forbidden",
                          status_code=403)
    with transaction.atomic():
        s = CashSession.objects.select_for_update(of=("self",)).select_related("store__franchisee__headquarters", "register",
                                                                   "employee").get(pk=session.pk)
        if s.status != CashSession.STATUS_OPEN:
            raise ConflictError("Cash session already closed", code="session_closed")
        if denominations:
            total = sum(int(k) * int(v) for k, v in denominations.items())
            if total != counted_cash_cents:
                raise DomainError("Denomination count does not add up to the counted amount",
                                  code="denomination_mismatch", status_code=400)
        b = breakdown(s)
        variance = counted_cash_cents - b["expected_cents"]
        threshold = variance_threshold(s.store)
        exceeds = abs(variance) > threshold
        if exceeds and not explanation.strip():
            raise DomainError(
                f"Counted cash differs from expected by {variance} cents (threshold {threshold}). "
                "An explanation is required.", code="explanation_required",
                errors=[{"code": "explanation_required", "variance_cents": variance, "threshold_cents": threshold,
                         "message": "Explain the shortage/overage to close the register"}])
        needs_approval = exceeds or close_requires_approval(s.store)
        now = timezone.now()
        s.expected_cash_cents = b["expected_cents"]
        s.counted_cash_cents = counted_cash_cents
        s.variance_cents = variance
        s.closed_by = user
        s.closed_at = now
        s.notes = notes
        s.status = CashSession.STATUS_PENDING_APPROVAL if needs_approval else CashSession.STATUS_CLOSED
        s.save()
        CashReconciliation.objects.create(
            session=s, counted_cents=counted_cash_cents, variance_cents=variance, threshold_cents=threshold,
            denominations=denominations or {}, explanation=explanation, created_by=user,
            **{k: v for k, v in b.items()})
        if variance:
            CashDiscrepancy.objects.create(
                session=s, store=s.store, employee=s.employee, amount_cents=variance,
                kind="shortage" if variance < 0 else "overage", exceeds_threshold=exceeds, explanation=explanation,
                status=CashDiscrepancy.STATUS_OPEN if exceeds else CashDiscrepancy.STATUS_ACCEPTED)
        build_shift_closing(s, user, requires_approval=needs_approval)
        audit("cash_session.close", s, entity_type="CashSession", actor=user, store=s.store,
              data={"expected": b["expected_cents"], "counted": counted_cash_cents, "variance": variance,
                    "threshold": threshold, "requires_approval": needs_approval})
        _push(s, "cash.session_closed")
    return s


def approve_session(session: CashSession, user, notes: str = "") -> CashSession:
    if not user.has_permission("cash.approve"):
        raise DomainError("Missing permission: cash.approve", code="forbidden", status_code=403)
    with transaction.atomic():
        s = CashSession.objects.select_for_update().get(pk=session.pk)
        if s.status != CashSession.STATUS_PENDING_APPROVAL:
            raise ConflictError("Session is not awaiting approval", code="not_pending")
        if s.employee_id == user.pk:
            raise DomainError("You cannot approve your own register session", code="segregation_of_duties",
                              status_code=403)
        now = timezone.now()
        s.status, s.approved_by, s.approved_at = CashSession.STATUS_CLOSED, user, now
        s.save()
        s.discrepancies.filter(status=CashDiscrepancy.STATUS_OPEN).update(
            status=CashDiscrepancy.STATUS_APPROVED, reviewed_by=user, reviewed_at=now, review_notes=notes)
        ShiftClosing.objects.filter(session=s).update(status=ShiftClosing.STATUS_APPROVED, approved_by=user,
                                                      approved_at=now)
        audit("cash_session.approve", s, entity_type="CashSession", actor=user, store=s.store,
              data={"variance": s.variance_cents, "notes": notes})
        _push(s, "cash.session_approved")
    return s


def adjusted_figures(session: CashSession) -> dict:
    post = session.transactions.filter(post_close=True).aggregate(s=Sum("amount_cents"))["s"] or 0
    if session.expected_cash_cents is None:
        return {"post_close_corrections_cents": post}
    adj_expected = session.expected_cash_cents + post
    return {"post_close_corrections_cents": post, "adjusted_expected_cents": adj_expected,
            "adjusted_variance_cents": (session.counted_cash_cents or 0) - adj_expected}


# ---------------------------------------------------------------------------
# Shift closing
# ---------------------------------------------------------------------------
def shift_summary(store, employee, start: datetime, end: datetime, session: CashSession | None = None) -> dict:
    from apps.orders.models import Order, OrderDiscount, OrderStatus

    orders = Order.objects.filter(store=store, created_by=employee, placed_at__gte=start, placed_at__lte=end)
    valid = orders.exclude(status__in=[OrderStatus.CANCELLED, OrderStatus.REJECTED])
    agg = valid.aggregate(sales=Sum("total_cents"), net=Sum("subtotal_cents"), tax=Sum("tax_cents"),
                          disc=Sum("discount_cents"), tips=Sum("tip_cents"), n=Count("id"))
    payments = Payment.objects.filter(store=store, created_by=employee, created_at__gte=start, created_at__lte=end,
                                      status__in=Payment.SETTLED_STATUSES)
    by_method = list(payments.values("method").annotate(amount_cents=Sum("amount_cents"), count=Count("id"),
                                                         tips_cents=Sum("tip_cents")).order_by("method"))
    refunds = Refund.objects.filter(payment__store=store, requested_by=employee, created_at__gte=start,
                                    created_at__lte=end, status=Refund.STATUS_SUCCEEDED)
    voids = orders.filter(status=OrderStatus.CANCELLED)
    discounts = OrderDiscount.objects.filter(order__store=store, applied_by=employee, created_at__gte=start,
                                             created_at__lte=end)
    summary = {
        "employee": {"id": employee.pk, "username": employee.username, "name": employee.get_full_name()},
        "store": {"id": store.pk, "number": store.number, "name": store.name},
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "sales": {"order_count": agg["n"], "gross_sales_cents": agg["sales"] or 0, "subtotal_cents": agg["net"] or 0,
                  "tax_cents": agg["tax"] or 0, "discount_cents": agg["disc"] or 0, "tip_cents": agg["tips"] or 0,
                  "average_order_cents": int((agg["sales"] or 0) / agg["n"]) if agg["n"] else 0},
        "payment_methods": by_method,
        "refunds": {"count": refunds.count(), "amount_cents": refunds.aggregate(s=Sum("amount_cents"))["s"] or 0,
                    "items": [{"id": str(r.pk), "amount_cents": r.amount_cents, "reason": r.reason,
                               "method": r.payment.method} for r in refunds.select_related("payment")[:100]]},
        "voids": {"count": voids.count(), "amount_cents": voids.aggregate(s=Sum("total_cents"))["s"] or 0,
                  "items": [{"order_number": o.order_number, "total_cents": o.total_cents,
                             "reason": o.cancellation_reason} for o in voids[:100]]},
        "discounts": {"count": discounts.count(), "amount_cents": discounts.aggregate(s=Sum("amount_cents"))["s"] or 0,
                      "by_name": list(discounts.values("name").annotate(count=Count("id"),
                                                                        amount_cents=Sum("amount_cents")))},
    }
    if session is not None:
        rec = getattr(session, "reconciliation", None)
        summary["cash"] = {
            "session_id": str(session.pk), "register": session.register.code if session.register else None,
            **(breakdown(session) if rec is None else {
                k: getattr(rec, k) for k in ("opening_float_cents", "cash_sales_cents", "cash_refunds_cents",
                                             "drops_cents", "additions_cents", "paid_outs_cents",
                                             "petty_expenses_cents", "corrections_cents", "transfers_in_cents",
                                             "transfers_out_cents", "expected_cents")}),
            "counted_cents": session.counted_cash_cents, "variance_cents": session.variance_cents,
            "threshold_cents": rec.threshold_cents if rec else None, "explanation": rec.explanation if rec else "",
        }
    return summary


def build_shift_closing(session: CashSession, user, requires_approval: bool) -> ShiftClosing:
    end = session.closed_at or timezone.now()
    summary = shift_summary(session.store, session.employee, session.opened_at, end, session)
    return ShiftClosing.objects.create(
        store=session.store, employee=session.employee, session=session, period_start=session.opened_at,
        period_end=end, summary=summary, requires_approval=requires_approval,
        status=ShiftClosing.STATUS_PENDING if requires_approval else ShiftClosing.STATUS_FINAL)


def shift_report_text(sc: ShiftClosing) -> str:
    from apps.orders.receipts import WIDTH, _row, money

    s = sc.summary
    L = ["SHIFT CLOSING REPORT".center(WIDTH), f"{s['store']['number']} {s['store']['name']}".center(WIDTH), ""]
    L += [_row("Employee", s["employee"]["username"]), _row("From", s["period"]["start"][:16].replace("T", " ")),
          _row("To", s["period"]["end"][:16].replace("T", " ")), _row("Status", sc.status), "-" * WIDTH]
    sales = s["sales"]
    L += [_row("Orders", str(sales["order_count"])), _row("Gross sales", money(sales["gross_sales_cents"])),
          _row("Tax", money(sales["tax_cents"])), _row("Discounts", money(-sales["discount_cents"])),
          _row("Tips", money(sales["tip_cents"])), _row("Average order", money(sales["average_order_cents"])),
          "-" * WIDTH, "PAYMENTS"]
    for p in s["payment_methods"]:
        L.append(_row(f"  {p['method']} ({p['count']})", money(p["amount_cents"])))
    L += [_row("Refunds", f"{s['refunds']['count']} / {money(s['refunds']['amount_cents'])}"),
          _row("Voids", f"{s['voids']['count']} / {money(s['voids']['amount_cents'])}"),
          _row("Discounts given", f"{s['discounts']['count']} / {money(s['discounts']['amount_cents'])}")]
    c = s.get("cash")
    if c:
        L += ["-" * WIDTH, f"CASH DRAWER {c.get('register') or ''}"]
        for label, key, sign in (("Opening float", "opening_float_cents", 1), ("Cash sales", "cash_sales_cents", 1),
                                 ("Cash refunds", "cash_refunds_cents", -1), ("Drops", "drops_cents", -1),
                                 ("Additions", "additions_cents", 1), ("Paid-outs", "paid_outs_cents", -1),
                                 ("Petty expenses", "petty_expenses_cents", -1),
                                 ("Corrections", "corrections_cents", 1), ("Transfers in", "transfers_in_cents", 1),
                                 ("Transfers out", "transfers_out_cents", -1)):
            if c.get(key):
                L.append(_row(f"  {label}", money(sign * c[key])))
        L += [_row("Expected", money(c.get("expected_cents") or 0)), _row("Counted", money(c.get("counted_cents") or 0)),
              _row("Over / (short)", money(c.get("variance_cents") or 0))]
        if c.get("explanation"):
            L.append(f"Note: {c['explanation']}")
    if sc.approved_by_id:
        L.append(_row("Approved by", sc.approved_by.username))
    return "\n".join(L)


def shift_report_csv(sc: ShiftClosing) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    s = sc.summary
    w.writerow(["section", "item", "value"])
    w.writerow(["shift", "employee", s["employee"]["username"]])
    w.writerow(["shift", "store", s["store"]["number"]])
    w.writerow(["shift", "start", s["period"]["start"]])
    w.writerow(["shift", "end", s["period"]["end"]])
    w.writerow(["shift", "status", sc.status])
    for k, v in s["sales"].items():
        w.writerow(["sales", k, v])
    for p in s["payment_methods"]:
        w.writerow(["payments", p["method"], p["amount_cents"]])
    w.writerow(["refunds", "count", s["refunds"]["count"]])
    w.writerow(["refunds", "amount_cents", s["refunds"]["amount_cents"]])
    w.writerow(["voids", "count", s["voids"]["count"]])
    w.writerow(["voids", "amount_cents", s["voids"]["amount_cents"]])
    w.writerow(["discounts", "count", s["discounts"]["count"]])
    w.writerow(["discounts", "amount_cents", s["discounts"]["amount_cents"]])
    for k, v in (s.get("cash") or {}).items():
        w.writerow(["cash", k, v])
    return buf.getvalue()


def cash_report(sessions_qs) -> dict:
    """Consolidated cash management report for any scoped set of sessions."""
    closed = sessions_qs.exclude(status=CashSession.STATUS_OPEN)
    agg = closed.aggregate(sessions=Count("id"), expected=Sum("expected_cash_cents"), counted=Sum("counted_cash_cents"),
                           variance=Sum("variance_cents"),
                           shortages=Count("id", filter=Q(variance_cents__lt=0)),
                           overages=Count("id", filter=Q(variance_cents__gt=0)))
    ledger = CashTransaction.objects.filter(session__in=sessions_qs).values("kind").annotate(
        amount_cents=Sum("amount_cents"), count=Count("id"))
    by_store = list(closed.values("store_id", "store__number", "store__franchisee__code").annotate(
        sessions=Count("id"), net_variance_cents=Sum("variance_cents"),
        shortages=Count("id", filter=Q(variance_cents__lt=0))).order_by("store__number"))
    by_employee = list(closed.values("employee__username").annotate(
        sessions=Count("id"), net_variance_cents=Sum("variance_cents"),
        shortages=Count("id", filter=Q(variance_cents__lt=0))).order_by("employee__username"))
    open_disc = CashDiscrepancy.objects.filter(session__in=sessions_qs, status=CashDiscrepancy.STATUS_OPEN)
    return {
        "totals": {"closed_sessions": agg["sessions"], "open_sessions": sessions_qs.filter(
            status=CashSession.STATUS_OPEN).count(), "pending_approval": sessions_qs.filter(
            status=CashSession.STATUS_PENDING_APPROVAL).count(), "expected_cents": agg["expected"] or 0,
            "counted_cents": agg["counted"] or 0, "net_variance_cents": agg["variance"] or 0,
            "shortage_sessions": agg["shortages"], "overage_sessions": agg["overages"]},
        "ledger_by_kind": list(ledger),
        "by_store": by_store,
        "by_employee": by_employee,
        "open_discrepancies": [{"id": d.id, "session": str(d.session_id), "store": d.store.number,
                                "employee": d.employee.username if d.employee else None, "amount_cents": d.amount_cents,
                                "kind": d.kind, "explanation": d.explanation, "created_at": d.created_at}
                               for d in open_disc.select_related("store", "employee")[:200]],
    }
