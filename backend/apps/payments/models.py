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


class CashSession(TimeStampedModel):
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey("organization.Store", on_delete=models.PROTECT, related_name="cash_sessions")
    device = models.ForeignKey("organization.Device", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, default=STATUS_OPEN, db_index=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float_cents = models.IntegerField(default=0)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    counted_cash_cents = models.IntegerField(null=True, blank=True)
    expected_cash_cents = models.IntegerField(null=True, blank=True)
    variance_cents = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["store", "device"], condition=models.Q(status="open"),
                                    name="one_open_cash_session_per_device"),
        ]


class CashMovement(TimeStampedModel):
    KIND_PAID_IN = "paid_in"
    KIND_PAID_OUT = "paid_out"
    session = models.ForeignKey(CashSession, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=10, choices=[(KIND_PAID_IN, "Paid in"), (KIND_PAID_OUT, "Paid out")])
    amount_cents = models.PositiveIntegerField()
    reason = models.CharField(max_length=200)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")


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
