"""Payments are kept separate from order state.

No raw card numbers or CVVs are ever accepted or stored: card payments are
processed by an authorized provider (tokenized / card-present terminal) and only
the provider reference, brand and last four digits are persisted.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    ONLINE = "online", "Online payment reference"
    PROVIDER = "provider", "Paid via delivery provider"


class PaymentState(models.TextChoices):
    PENDING = "pending", "Pending"
    AUTHORIZED = "authorized", "Authorized"
    CAPTURED = "captured", "Captured"
    FAILED = "failed", "Failed"
    VOIDED = "voided", "Voided"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"


class Register(TimeStampedModel):
    """A physical cash register / drawer at a store, usually attached to a POS device."""

    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="registers")
    device = models.OneToOneField("organization.Device", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="register")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("store", "code")]
        ordering = ["store", "code"]

    def __str__(self):
        return f"{self.store.number}/{self.code}"


class CashSession(TimeStampedModel):
    """A register session (a.k.a. RegisterSession): one employee, one register, one cash drawer count.

    Finalized sessions are immutable. Anything discovered later is recorded as a separate
    post-close ``CashTransaction`` of kind ``correction`` with manager authorization."""

    STATUS_OPEN = "open"
    STATUS_PENDING_APPROVAL = "pending_approval"
    STATUS_CLOSED = "closed"
    STATUSES = [(STATUS_OPEN, "Open"), (STATUS_PENDING_APPROVAL, "Closed — awaiting manager approval"),
                (STATUS_CLOSED, "Closed")]
    FINAL_FIELDS = ("opening_float_cents", "counted_cash_cents", "expected_cash_cents", "variance_cents",
                    "register_id", "employee_id", "store_id", "opened_at", "closed_at")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="cash_sessions")
    register = models.ForeignKey(Register, null=True, blank=True, on_delete=models.PROTECT, related_name="sessions")
    device = models.ForeignKey("organization.Device", null=True, blank=True, on_delete=models.SET_NULL)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+",
                                 help_text="Employee responsible for the drawer")
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_OPEN, db_index=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float_cents = models.IntegerField(default=0)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    counted_cash_cents = models.IntegerField(null=True, blank=True)
    expected_cash_cents = models.IntegerField(null=True, blank=True)
    variance_cents = models.IntegerField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["register"], condition=models.Q(status="open"),
                                    name="one_open_session_per_register"),
        ]
        indexes = [models.Index(fields=["store", "status"]), models.Index(fields=["store", "opened_at"])]

    @property
    def is_finalized(self) -> bool:
        return self.status != self.STATUS_OPEN

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            prev = CashSession.objects.filter(pk=self.pk).values("status", *self.FINAL_FIELDS).first()
            if prev and prev["status"] != self.STATUS_OPEN:
                changed = [f for f in self.FINAL_FIELDS if prev[f] != getattr(self, f)]
                if changed or self.status == self.STATUS_OPEN:
                    raise ValueError(f"Finalized cash session cannot be modified ({', '.join(changed) or 'status'}); "
                                     "record a correction instead")
        super().save(*args, **kwargs)


class CashTransaction(TimeStampedModel):
    """Cash drawer ledger. ``amount_cents`` is the signed effect on the drawer (+ in, − out)."""

    SALE = "sale"
    REFUND = "refund"
    DROP = "drop"
    ADDITION = "addition"
    PAID_OUT = "paid_out"
    PETTY_EXPENSE = "petty_expense"
    CORRECTION = "correction"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    KINDS = [(SALE, "Cash sale"), (REFUND, "Cash refund"), (DROP, "Cash drop to safe"), (ADDITION, "Cash addition"),
             (PAID_OUT, "Paid-out"), (PETTY_EXPENSE, "Petty cash expense"), (CORRECTION, "Correction"),
             (TRANSFER_IN, "Transfer in"), (TRANSFER_OUT, "Transfer out")]
    OUTFLOWS = {REFUND, DROP, PAID_OUT, PETTY_EXPENSE, TRANSFER_OUT}
    INFLOWS = {SALE, ADDITION, TRANSFER_IN}
    # Legacy aliases (pre-ledger API)
    KIND_PAID_IN = ADDITION
    KIND_PAID_OUT = PAID_OUT

    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    amount_cents = models.IntegerField()
    reason = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    expense_category = models.CharField(max_length=50, blank=True)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    payment = models.ForeignKey("Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    refund = models.ForeignKey("Refund", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    counterpart_session = models.ForeignKey(CashSession, null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    post_close = models.BooleanField(default=False, help_text="Correction recorded after the session was finalized")
    idempotency_key = models.CharField(max_length=100, null=True, blank=True, unique=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["session", "kind"])]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValueError("Cash transactions are immutable; record a correction instead")
        super().save(*args, **kwargs)


class CashReconciliation(models.Model):
    """Snapshot of the drawer calculation at close."""

    session = models.OneToOneField(CashSession, on_delete=models.PROTECT, related_name="reconciliation")
    opening_float_cents = models.IntegerField()
    cash_sales_cents = models.IntegerField()
    cash_refunds_cents = models.IntegerField()
    drops_cents = models.IntegerField()
    additions_cents = models.IntegerField()
    paid_outs_cents = models.IntegerField()
    petty_expenses_cents = models.IntegerField()
    corrections_cents = models.IntegerField()
    transfers_in_cents = models.IntegerField()
    transfers_out_cents = models.IntegerField()
    expected_cents = models.IntegerField()
    counted_cents = models.IntegerField()
    variance_cents = models.IntegerField()
    threshold_cents = models.IntegerField()
    denominations = models.JSONField(default=dict, blank=True)
    explanation = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class CashDiscrepancy(TimeStampedModel):
    STATUS_OPEN = "open"
    STATUS_APPROVED = "approved"
    STATUS_ACCEPTED = "within_threshold"

    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="discrepancies")
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="cash_discrepancies")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    amount_cents = models.IntegerField(help_text="Counted minus expected (negative = shortage)")
    kind = models.CharField(max_length=10, choices=[("shortage", "Shortage"), ("overage", "Overage")])
    exceeds_threshold = models.BooleanField(default=False)
    explanation = models.TextField(blank=True)
    status = models.CharField(max_length=20, default=STATUS_OPEN, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)


class ShiftClosing(TimeStampedModel):
    """End-of-shift report for an employee (typically produced when their register session closes)."""

    STATUS_PENDING = "pending_approval"
    STATUS_APPROVED = "approved"
    STATUS_FINAL = "final"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="shift_closings")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    session = models.OneToOneField(CashSession, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="shift_closing")
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    summary = models.JSONField()
    status = models.CharField(max_length=20, default=STATUS_FINAL, db_index=True)
    requires_approval = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_end"]
        indexes = [models.Index(fields=["store", "period_end"])]


class Payment(TimeStampedModel):
    SETTLED_STATUSES = (PaymentState.CAPTURED, PaymentState.PARTIALLY_REFUNDED, PaymentState.REFUNDED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    processor = models.CharField(max_length=30, help_text="cash, stripe, ubereats, doordash, ...")
    status = models.CharField(max_length=20, choices=PaymentState.choices, default=PaymentState.PENDING,
                              db_index=True)
    amount_cents = models.IntegerField(help_text="Amount applied to the order (excl. change)")
    tip_cents = models.IntegerField(default=0)
    refunded_cents = models.IntegerField(default=0)
    tendered_cents = models.IntegerField(null=True, blank=True)
    change_cents = models.IntegerField(null=True, blank=True)
    processor_reference = models.CharField(max_length=200, blank=True, db_index=True)
    card_brand = models.CharField(max_length=20, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    failure_reason = models.CharField(max_length=300, blank=True)
    cash_session = models.ForeignKey(CashSession, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="payments")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    idempotency_key = models.CharField(max_length=100, null=True, blank=True, unique=True)
    reconciled = models.BooleanField(default=False)
    settlement_reference = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["store", "created_at"]), models.Index(fields=["method", "status"])]

    @property
    def net_amount_cents(self) -> int:
        return self.amount_cents - self.refunded_cents


class Refund(TimeStampedModel):
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount_cents = models.PositiveIntegerField()
    reason = models.CharField(max_length=300)
    status = models.CharField(max_length=20, default=STATUS_PENDING)
    processor_reference = models.CharField(max_length=200, blank=True)
    failure_reason = models.CharField(max_length=300, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    cash_session = models.ForeignKey(CashSession, null=True, blank=True, on_delete=models.SET_NULL)
