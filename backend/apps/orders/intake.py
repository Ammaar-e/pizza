"""Central Order Intake Engine.

Every order — POS, call centre, external API, mobile apps, Uber Eats, DoorDash,
Skip — is expressed as a ``NormalizedOrder`` and passed to ``OrderIntakeService``.
Provider adapters only translate formats; they contain no order business logic.

Pipeline:
    1. Idempotency / duplicate detection (provider order id, idempotency key)
    2. Store routing & channel/integration checks
    3. Product + modifier mapping (SKU / provider merchant-supplied ids / mapping table)
    4. Availability, customization and price validation via the catalogue engine
    5. Acceptance decision (auto-accept / reject / route to staff) per policy
    6. Persist order, items, modifiers, events in one transaction
    7. After commit: kitchen tickets, real-time broadcast, provider acknowledgement
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.engine import LineIssue, LineValidationError, applicable_charges, compute_tax, price_line, \
    tax_rates_for
from apps.core.exceptions import ConflictError, DomainError
from apps.organization.models import Store, StoreOrderSequence

from .models import (
    THIRD_PARTY_SOURCES,
    Customer,
    Order,
    OrderItem,
    OrderItemModifier,
    OrderSource,
    OrderStatus,
    OrderType,
    PaymentStatus,
    ProviderSyncStatus,
)
from .state import log_event

logger = logging.getLogger("orders.intake")


# ---------------------------------------------------------------------------
# Normalized internal order format
# ---------------------------------------------------------------------------
@dataclass
class NormalizedModifier:
    code: str | None = None                # internal modifier code (after mapping)
    external_id: str | None = None         # provider option id
    name: str = ""
    placement: str = "whole"
    quantity: int = 1
    price_cents: int | None = None         # provider-reported price


@dataclass
class NormalizedItem:
    product: str | None = None             # internal SKU (after mapping)
    external_id: str | None = None         # provider item id
    name: str = ""
    size: str | None = None
    crust: str | None = None
    quantity: int = 1
    modifiers: list[NormalizedModifier] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    special_instructions: str = ""
    unit_price_cents: int | None = None    # provider-reported price


@dataclass
class NormalizedCustomer:
    name: str = ""
    phone: str = ""
    email: str = ""
    external_id: str = ""


@dataclass
class NormalizedPayment:
    """Payment info declared at intake. Provider orders are paid to the provider."""

    status: str = "unpaid"                 # unpaid | provider_paid | prepaid
    method: str = ""                       # e.g. ubereats, doordash, online
    amount_cents: int = 0
    reference: str = ""                    # provider/payment gateway reference (never card data)


@dataclass
class NormalizedOrder:
    source: str
    order_type: str
    items: list[NormalizedItem]
    store_id: int | None = None
    store_number: str | None = None
    external_order_id: str | None = None
    external_display_id: str = ""
    customer: NormalizedCustomer = field(default_factory=NormalizedCustomer)
    delivery_address: dict = field(default_factory=dict)
    special_instructions: str = ""
    scheduled_for: datetime | None = None
    tip_cents: int = 0
    payment: NormalizedPayment = field(default_factory=NormalizedPayment)
    provider_totals: dict = field(default_factory=dict)
    discounts: list[dict] = field(default_factory=list)
    expected_total_cents: int | None = None   # optional client-side total for price validation
    provider_ack_deadline: datetime | None = None
    is_offline_origin: bool = False
    entry_channel: str = ""
    offline_client_ref: str = ""
    raw: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        data = asdict(self)
        data.pop("raw", None)
        data.pop("provider_ack_deadline", None)
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class IntakeResult:
    order: Order
    created: bool
    decision: str                           # accepted | pending_review | rejected | duplicate
    issues: list[dict] = field(default_factory=list)


@dataclass
class AcceptancePolicy:
    """Business rules for automatic acceptance (configurable per store integration)."""

    auto_accept: bool = True
    reject_unavailable_items: bool = True
    reject_when_closed: bool = True
    reject_unmapped_items: bool = False      # False = route to staff review
    price_tolerance_cents: int = 50

    @classmethod
    def from_dict(cls, data: dict | None) -> "AcceptancePolicy":
        p = cls()
        for k, v in (data or {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


INTERNAL_POLICY = AcceptancePolicy(auto_accept=True, reject_unavailable_items=True, reject_when_closed=False,
                                   reject_unmapped_items=True, price_tolerance_cents=0)


class OrderIntakeService:
    def __init__(self, *, actor=None, api_client=None, device=None, store_integration=None,
                 policy: AcceptancePolicy | None = None):
        self.actor = actor
        self.api_client = api_client
        self.device = device
        self.store_integration = store_integration
        self.policy = policy

    # -- public -------------------------------------------------------------
    def submit(self, norm: NormalizedOrder, idempotency_key: str | None = None) -> IntakeResult:
        store = self._route_store(norm)
        fingerprint = norm.fingerprint()

        existing = self._find_existing(store, norm, idempotency_key)
        if existing is not None:
            if idempotency_key and existing.idempotency_key == idempotency_key and \
                    existing.request_fingerprint and existing.request_fingerprint != fingerprint:
                raise ConflictError("Idempotency key was reused with a different request body",
                                    code="idempotency_key_reused")
            log_event(existing, "duplicate_submission", "Duplicate submission ignored",
                      {"idempotency_key": idempotency_key, "external_order_id": norm.external_order_id})
            return IntakeResult(existing, False, "duplicate", existing.validation_issues)

        self._check_channel(store, norm)
        is_provider = norm.source in THIRD_PARTY_SOURCES
        policy = self.policy or (AcceptancePolicy() if is_provider else INTERNAL_POLICY)

        priced_lines, issues = self._price_items(store, norm, provider=is_provider)
        if issues and not is_provider:
            # Internal channels (POS, call centre, API) get a synchronous validation error; nothing is persisted.
            raise DomainError("Order validation failed", code="order_invalid", errors=issues)

        try:
            with transaction.atomic():
                order = self._persist(store, norm, idempotency_key, fingerprint, priced_lines, issues)
        except IntegrityError:
            # Concurrent duplicate (webhook retry racing the first delivery): return the winner.
            existing = self._find_existing(store, norm, idempotency_key)
            if existing is None:
                raise
            return IntakeResult(existing, False, "duplicate", existing.validation_issues)

        decision, reason = self._decide(store, norm, order, issues, policy)
        self._apply_decision(order, decision, reason, issues, is_provider)
        return IntakeResult(order, True, decision, issues)

    # -- routing ----------------------------------------------------------------
    def _route_store(self, norm: NormalizedOrder) -> Store:
        qs = Store.objects.select_related("franchisee__headquarters")
        store = None
        if norm.store_id:
            store = qs.filter(pk=norm.store_id).first()
        elif norm.store_number:
            store = qs.filter(number=norm.store_number).first()
        if store is None:
            raise DomainError("Order could not be routed to a store", code="store_not_found", status_code=404)
        if self.api_client is not None and not self.api_client.can_access_store(store):
            raise DomainError("API client is not authorized for this store", code="store_forbidden", status_code=403)
        if not store.is_active or not store.franchisee.is_active:
            raise DomainError("Store is not active", code="store_inactive")
        return store

    def _check_channel(self, store: Store, norm: NormalizedOrder):
        channel_key = {
            OrderSource.POS: "pos", OrderSource.CALL_CENTRE: "call_centre", OrderSource.EXTERNAL_API: "external_api",
            OrderSource.ANDROID: "android", OrderSource.IOS: "ios",
        }.get(norm.source)
        if channel_key and not (store.ordering_channels or {}).get(channel_key, False):
            raise DomainError(f"Ordering channel '{channel_key}' is disabled for this store", code="channel_disabled")
        if norm.order_type == OrderType.DELIVERY and not (store.delivery_config or {}).get("enabled"):
            raise DomainError("Store does not offer delivery", code="delivery_disabled")
        if norm.order_type == OrderType.PICKUP and not (store.pickup_config or {}).get("enabled", True):
            raise DomainError("Store does not offer pickup", code="pickup_disabled")
        if norm.order_type not in OrderType.values:
            raise DomainError(f"Unknown order type '{norm.order_type}'", code="invalid_order_type", status_code=400)
        if not norm.items:
            raise DomainError("Order has no items", code="empty_order", status_code=400)

    def _find_existing(self, store: Store, norm: NormalizedOrder, idempotency_key: str | None) -> Order | None:
        if norm.external_order_id:
            o = Order.objects.filter(source=norm.source, external_order_id=norm.external_order_id).first()
            if o:
                return o
        if idempotency_key:
            return Order.objects.filter(store=store, source=norm.source, idempotency_key=idempotency_key).first()
        return None

    # -- pricing ----------------------------------------------------------------
    def _price_items(self, store, norm: NormalizedOrder, provider: bool):
        from apps.catalog.engine import unavailable_modifier_ids

        unavailable = unavailable_modifier_ids(store)
        channel = norm.source if provider else None
        results = []
        issues: list[dict] = []
        for idx, item in enumerate(norm.items):
            if not item.product or any(m.code is None for m in item.modifiers):
                issues.append({"code": "unmapped_item", "message": f"Item '{item.name or item.external_id}' "
                               "could not be mapped to the catalogue", "ref": item.external_id, "line": idx})
                results.append((item, None, []))
                continue
            line = {
                "product": item.product, "size": item.size, "crust": item.crust, "quantity": item.quantity,
                "modifiers": [{"code": m.code, "placement": m.placement, "quantity": m.quantity} for m in item.modifiers],
                "removed": item.removed, "special_instructions": item.special_instructions,
            }
            try:
                pl = price_line(store, line, channel=channel, _unavailable_mods=unavailable)
                line_issues: list[LineIssue] = []
            except LineValidationError as e:
                line_issues = e.issues
                try:  # price without availability so staff can still review the order
                    pl = price_line(store, line, channel=channel, check_availability=False, _unavailable_mods=set())
                except LineValidationError:
                    pl = None
            for li in line_issues:
                d = li.as_dict()
                d["line"] = idx
                issues.append(d)
            results.append((item, pl, line_issues))
        return results, issues

    # -- persistence ------------------------------------------------------------
    def _next_number(self, store: Store, business_date) -> int:
        seq, _ = StoreOrderSequence.objects.get_or_create(store=store, business_date=business_date)
        seq = StoreOrderSequence.objects.select_for_update().get(pk=seq.pk)
        seq.last_number += 1
        seq.save(update_fields=["last_number"])
        return seq.last_number

    def _customer(self, store: Store, norm: NormalizedOrder) -> Customer | None:
        c = norm.customer
        if not (c.phone or c.email or c.name):
            return None
        if c.phone and norm.source not in THIRD_PARTY_SOURCES:
            cust, _ = Customer.objects.get_or_create(franchisee=store.franchisee, phone=c.phone,
                                                     defaults={"name": c.name, "email": c.email})
            if norm.delivery_address and norm.delivery_address not in cust.addresses:
                cust.addresses = (cust.addresses or [])[-4:] + [norm.delivery_address]
                cust.save(update_fields=["addresses", "updated_at"])
            return cust
        return None

    def _persist(self, store, norm, idempotency_key, fingerprint, priced_lines, issues) -> Order:
        now = timezone.now()
        business_date = store.local_now(now).date()
        is_provider = norm.source in THIRD_PARTY_SOURCES

        subtotal = 0
        tax_total = 0
        tax_breakdown: dict[str, int] = {}
        for _item, pl, _ in priced_lines:
            if pl is not None:
                subtotal += pl.line_total_cents
                tax_total += pl.tax_cents
                for k, v in pl.tax_breakdown.items():
                    tax_breakdown[k] = tax_breakdown.get(k, 0) + v

        charges = [] if is_provider else applicable_charges(store, norm.order_type)
        charges_total = 0
        for ch in charges:
            charges_total += ch["amount_cents"]
            if ch.get("tax_category_id"):
                t, bd = compute_tax(ch["amount_cents"], tax_rates_for(store, ch["tax_category_id"]))
                tax_total += t
                for k, v in bd.items():
                    tax_breakdown[k] = tax_breakdown.get(k, 0) + v
            ch.pop("tax_category_id", None)

        total = subtotal + charges_total + tax_total + norm.tip_cents
        if is_provider and norm.provider_totals:
            # Third-party orders: the provider is the merchant of record for the customer charge.
            # We keep our computed values but record the provider's numbers for reconciliation.
            pt = norm.provider_totals
            subtotal_p = pt.get("subtotal_cents")
            if subtotal_p is not None and abs(int(subtotal_p) - subtotal) > (self.policy or AcceptancePolicy()).price_tolerance_cents:
                issues.append({"code": "price_mismatch", "message": f"Provider subtotal {subtotal_p} differs from "
                               f"menu price {subtotal}", "ref": None})
            subtotal = int(pt.get("subtotal_cents", subtotal))
            tax_total = int(pt.get("tax_cents", tax_total))
            charges_total = int(pt.get("fees_cents", 0))
            total = int(pt.get("total_cents", subtotal + tax_total + norm.tip_cents))

        if norm.expected_total_cents is not None and not is_provider and norm.expected_total_cents != total:
            # Raised inside the persistence transaction, so nothing is written.
            raise DomainError("Submitted total does not match the calculated price", code="price_mismatch",
                              errors=[{"code": "price_mismatch", "message": f"Submitted total "
                                       f"{norm.expected_total_cents} does not match calculated total {total}",
                                       "ref": None, "calculated_total_cents": total}])

        payment_status = PaymentStatus.UNPAID
        if norm.payment.status == "provider_paid" or is_provider:
            payment_status = PaymentStatus.PROVIDER_PAID

        order = Order.objects.create(
            order_number=self._next_number(store, business_date),
            business_date=business_date,
            franchisee=store.franchisee,
            store=store,
            source=norm.source,
            api_client=self.api_client,
            external_order_id=norm.external_order_id or None,
            external_display_id=norm.external_display_id or "",
            store_integration=self.store_integration,
            idempotency_key=idempotency_key or None,
            request_fingerprint=fingerprint,
            order_type=norm.order_type,
            entry_channel=norm.entry_channel or "",
            status=OrderStatus.RECEIVED,
            payment_status=payment_status,
            provider_sync_status=ProviderSyncStatus.PENDING if is_provider else ProviderSyncStatus.NOT_APPLICABLE,
            customer=self._customer(store, norm),
            customer_snapshot={k: v for k, v in asdict(norm.customer).items() if v},
            delivery_address=norm.delivery_address or {},
            special_instructions=(norm.special_instructions or "")[:2000],
            scheduled_for=norm.scheduled_for,
            currency=store.franchisee.headquarters.currency,
            subtotal_cents=subtotal,
            charges_cents=charges_total,
            charges=charges,
            tax_cents=tax_total,
            tax_breakdown=tax_breakdown,
            tip_cents=norm.tip_cents,
            total_cents=total,
            provider_totals=norm.provider_totals,
            validation_issues=issues,
            is_offline_origin=norm.is_offline_origin,
            offline_client_ref=norm.offline_client_ref,
            created_by=self.actor,
            device=self.device,
            placed_at=now,
            provider_ack_deadline=norm.provider_ack_deadline,
            raw_payload=norm.raw or {},
        )
        for pos, (item, pl, _line_issues) in enumerate(priced_lines):
            if pl is None:
                oi = OrderItem.objects.create(
                    order=order, position=pos, name=item.name or "Unmapped item", quantity=item.quantity,
                    unit_price_cents=item.unit_price_cents or 0,
                    line_total_cents=(item.unit_price_cents or 0) * item.quantity,
                    special_instructions=item.special_instructions, external_item_id=item.external_id or "",
                    is_mapped=False,
                )
                for m in item.modifiers:
                    OrderItemModifier.objects.create(item=oi, name=m.name or (m.external_id or "?"), code=m.code or "",
                                                     placement=m.placement, quantity=m.quantity,
                                                     price_cents=m.price_cents or 0, external_id=m.external_id or "")
                continue
            oi = OrderItem.objects.create(
                order=order, position=pos, product=pl.product, sku=pl.product.sku, name=pl.product.name,
                size_code=pl.size.code if pl.size else "", size_name=pl.size.name if pl.size else "",
                crust_code=pl.crust.code if pl.crust else "", crust_name=pl.crust.name if pl.crust else "",
                quantity=pl.quantity, unit_price_cents=pl.unit_price_cents, line_total_cents=pl.line_total_cents,
                tax_cents=pl.tax_cents, removed=[{"code": m.code, "name": m.name} for m in pl.removed],
                special_instructions=pl.special_instructions, prep_category=pl.product.prep_category.code,
                external_item_id=item.external_id or "",
            )
            ext_by_code = {m.code: m.external_id for m in item.modifiers}
            OrderItemModifier.objects.bulk_create([
                OrderItemModifier(item=oi, modifier=m.modifier, code=m.modifier.code, name=m.modifier.name,
                                  group_code=m.modifier.group.code, placement=m.placement, quantity=m.quantity,
                                  price_cents=m.unit_price_cents, is_default=m.is_default,
                                  external_id=ext_by_code.get(m.modifier.code) or "")
                for m in pl.modifiers
            ])
        log_event(order, "order_received", f"Order received from {order.get_source_display()}",
                  {"issues": issues, "external_order_id": norm.external_order_id}, self.actor,
                  to_status=OrderStatus.RECEIVED)
        return order

    # -- decision ---------------------------------------------------------------
    def _decide(self, store, norm, order, issues, policy: AcceptancePolicy) -> tuple[str, str]:
        codes = {i["code"] for i in issues}
        unavailable = {"product_unavailable", "modifier_unavailable"} & codes
        unmapped = "unmapped_item" in codes
        structural = codes - {"product_unavailable", "modifier_unavailable", "unmapped_item", "price_mismatch"}
        is_provider = norm.source in THIRD_PARTY_SOURCES

        if is_provider:
            if policy.reject_when_closed and not store.is_open() and not norm.scheduled_for:
                return "rejected", "STORE_CLOSED"
            if unavailable and policy.reject_unavailable_items:
                return "rejected", "ITEM_UNAVAILABLE"
            if unmapped and policy.reject_unmapped_items:
                return "rejected", "ITEM_MAPPING_FAILED"
            if unmapped or structural or "price_mismatch" in codes or unavailable:
                return "pending_review", "; ".join(sorted(codes))
            if not policy.auto_accept:
                return "pending_review", "Manual acceptance required by store policy"
            return "accepted", ""

        # Internal sources (POS, call centre, API): invalid input is a client error.
        if issues:
            raise DomainError("Order validation failed", code="order_invalid", errors=issues)
        return "accepted", ""

    def _apply_decision(self, order: Order, decision: str, reason: str, issues: list[dict], is_provider: bool):
        from .state import transition

        if decision == "accepted":
            transition(order, OrderStatus.ACCEPTED, actor=self.actor, reason="Automatically accepted")
        elif decision == "pending_review":
            Order.objects.filter(pk=order.pk).update(requires_attention=True, attention_reason=reason[:300])
            transition(order, OrderStatus.PENDING_REVIEW, actor=self.actor, reason=reason)
        elif decision == "rejected":
            Order.objects.filter(pk=order.pk).update(requires_attention=False)
            transition(order, OrderStatus.REJECTED, actor=self.actor, reason=reason)
        order.refresh_from_db()


def deadline_from_now(seconds: int) -> datetime:
    return timezone.now() + timedelta(seconds=seconds)
