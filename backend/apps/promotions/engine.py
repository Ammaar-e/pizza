"""Promotion evaluation engine (server-authoritative).

Works on ``EngineLine`` objects so it can evaluate a quote (from catalogue-priced lines) or an
existing order (from persisted OrderItems) identically. Discounts are allocated per unit and per
line so taxes can be recomputed on the discounted amounts; a unit can never be discounted below 0.

Stacking: candidates are processed by priority (desc), then by savings (desc). A promotion that is
not ``combinable`` is never applied together with any other promotion. The first applied
promotion wins; others are reported as rejected with the reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Q
from django.utils import timezone

from .models import Promotion, PromotionCode, PromotionRedemption, PromotionType

PT = PromotionType


@dataclass
class EngineModifier:
    code: str
    modifier_id: int | None
    group_id: int | None
    price_cents: int  # per unit of the line


@dataclass
class EngineLine:
    index: int
    product_id: int | None
    category_id: int | None
    sku: str
    name: str
    size_code: str
    unit_price_cents: int
    quantity: int
    modifiers: list[EngineModifier] = field(default_factory=list)


@dataclass
class Unit:
    line: EngineLine
    n: int
    remaining: int

    @property
    def price(self) -> int:
        return self.line.unit_price_cents


@dataclass
class Applied:
    promotion: Promotion
    code: PromotionCode | None
    amount_cents: int
    line_allocations: dict[int, int]
    manual: bool = False

    def as_dict(self):
        return {"promotion_id": self.promotion.id, "name": self.promotion.name, "type": self.promotion.promo_type,
                "code": self.code.code if self.code else None, "amount_cents": self.amount_cents,
                "line_allocations": {str(k): v for k, v in self.line_allocations.items()}}


@dataclass
class Evaluation:
    applied: list[Applied] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    available: list[dict] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return sum(a.amount_cents for a in self.applied)

    def line_discounts(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for a in self.applied:
            for k, v in a.line_allocations.items():
                out[k] = out.get(k, 0) + v
        return out

    def as_dict(self):
        return {"applied": [a.as_dict() for a in self.applied], "rejected": self.rejected,
                "available": self.available, "total_discount_cents": self.total_cents,
                "line_discounts": {str(k): v for k, v in self.line_discounts().items()}}


@dataclass
class Context:
    store: object
    channel: str
    order_type: str
    lines: list[EngineLine]
    customer_phone: str = ""
    customer_email: str = ""
    customer_id: int | None = None
    now: datetime | None = None
    exclude_order_id: object = None

    @property
    def subtotal(self) -> int:
        return sum(l.unit_price_cents * l.quantity for l in self.lines)

    @property
    def customer_key(self) -> str:
        if self.customer_phone:
            return "phone:" + "".join(ch for ch in self.customer_phone if ch.isdigit() or ch == "+")
        if self.customer_email:
            return "email:" + self.customer_email.strip().lower()
        if self.customer_id:
            return f"id:{self.customer_id}"
        return ""


class Reject(Exception):
    pass


# ---------------------------------------------------------------------------
# Candidate selection & eligibility
# ---------------------------------------------------------------------------
def store_candidates(store):
    return (Promotion.objects.filter(is_enabled=True)
            .filter(Q(store=store) | Q(store__isnull=True, franchisee=store.franchisee) |
                    Q(store__isnull=True, franchisee__isnull=True))
            .prefetch_related("items", "targets", "rules", "codes"))


def applies_to_store(p: Promotion, store) -> bool:
    if p.store_id:
        return p.store_id == store.id
    if p.franchisee_id and p.franchisee_id != store.franchisee_id:
        return False
    targets = list(p.targets.all())
    if not targets:
        return True
    return any((t.store_id == store.id) or (t.franchisee_id and t.franchisee_id == store.franchisee_id and not t.store_id)
               for t in targets)


def check_eligible(p: Promotion, ctx: Context, code: PromotionCode | None = None):
    now = ctx.now or timezone.now()
    status = p.derive_status(now)
    if status == "disabled":
        raise Reject("Promotion is disabled")
    if status == "expired":
        raise Reject("Promotion has expired")
    if status == "scheduled":
        raise Reject("Promotion has not started yet")
    if not applies_to_store(p, ctx.store):
        raise Reject("Not valid at this store")
    if ctx.channel in ("ubereats", "doordash", "skip"):
        raise Reject("Not valid for delivery-marketplace orders")
    if p.channels and ctx.channel not in p.channels:
        raise Reject("Not valid for this ordering channel")
    if p.order_types and ctx.order_type not in p.order_types:
        raise Reject(f"Not valid for {ctx.order_type.replace('_', ' ')} orders")
    local = ctx.store.local_now(now)
    if p.days_of_week and local.weekday() not in p.days_of_week:
        raise Reject("Not valid today")
    if p.start_time and p.end_time:
        t = local.time()
        inside = p.start_time <= t < p.end_time if p.start_time < p.end_time else (t >= p.start_time or t < p.end_time)
        if not inside:
            raise Reject(f"Only valid {p.start_time:%H:%M}–{p.end_time:%H:%M}")
    if p.min_subtotal_cents and ctx.subtotal < p.min_subtotal_cents:
        raise Reject(f"Requires a minimum order of {p.min_subtotal_cents / 100:.2f}")
    rules = list(p.rules.all())
    if rules:
        ok = any((r.rule_type == "customer_phone" and ctx.customer_key == "phone:" + "".join(
            ch for ch in r.value if ch.isdigit() or ch == "+")) or
                 (r.rule_type == "customer_email" and ctx.customer_email.strip().lower() == r.value.strip().lower()) or
                 (r.rule_type == "customer_id" and str(ctx.customer_id) == r.value) for r in rules)
        if not ok:
            raise Reject("Not valid for this customer")
    used = PromotionRedemption.objects.filter(promotion=p, status=PromotionRedemption.STATUS_APPLIED)
    if ctx.exclude_order_id:
        used = used.exclude(order_id=ctx.exclude_order_id)
    if p.max_redemptions is not None and used.count() >= p.max_redemptions:
        raise Reject("Promotion redemption limit reached")
    if p.max_per_customer is not None:
        if not ctx.customer_key:
            raise Reject("Customer phone or email is required for this promotion")
        if used.filter(customer_key=ctx.customer_key).count() >= p.max_per_customer:
            raise Reject("Customer has already used this promotion the maximum number of times")
    if code is not None:
        if not code.is_active:
            raise Reject("Code is no longer active")
        if code.max_uses is not None and used.filter(code=code).count() >= code.max_uses:
            raise Reject("Code has already been used")


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------
def _matches(line: EngineLine, entries) -> bool:
    for e in entries:
        if e.size_code and e.size_code != line.size_code:
            continue
        if e.product_id and e.product_id == line.product_id:
            return True
        if e.category_id and e.category_id == line.category_id and not e.product_id:
            return True
    return False


def _entries(p: Promotion, role: str):
    return [i for i in p.items.all() if i.role == role]


def _eligible_units(p: Promotion, units: list[Unit], role="eligible") -> list[Unit]:
    elig = _entries(p, role)
    excl = _entries(p, "excluded")
    out = []
    for u in units:
        if u.line.product_id is None:
            continue
        if elig and not _matches(u.line, elig):
            continue
        if excl and _matches(u.line, excl):
            continue
        out.append(u)
    return out


def _pct(amount: int, bps: int) -> int:
    return int((Decimal(amount) * Decimal(bps) / Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _spread(units: list[Unit], amount: int) -> dict[int, int]:
    """Distribute ``amount`` across units proportionally to their remaining value."""
    pool = sum(u.remaining for u in units)
    amount = min(amount, pool)
    if amount <= 0:
        return {}
    alloc: dict[int, int] = {}
    left = amount
    for i, u in enumerate(units):
        share = left if i == len(units) - 1 else min(u.remaining, int(amount * u.remaining / pool))
        share = min(share, u.remaining, left)
        if share > 0:
            alloc[id(u)] = alloc.get(id(u), 0) + share
            left -= share
    i = 0
    while left > 0 and i < len(units):  # rounding remainder
        u = units[i]
        room = u.remaining - alloc.get(id(u), 0)
        take = min(room, left)
        if take > 0:
            alloc[id(u)] = alloc.get(id(u), 0) + take
            left -= take
        i += 1
    return alloc


# ---------------------------------------------------------------------------
# Type calculators -> {id(unit): cents}
# ---------------------------------------------------------------------------
def _calc(p: Promotion, units: list[Unit]) -> dict[int, int]:
    t = p.promo_type
    if t == PT.PERCENT:
        el = _eligible_units(p, units)
        return {id(u): min(u.remaining, _pct(u.price, p.value)) for u in el if u.remaining > 0}
    if t == PT.FIXED:
        return _spread([u for u in _eligible_units(p, units) if u.remaining > 0], p.value)
    if t in (PT.BOGO, PT.BUY_X_GET_Y):
        x = 1 if t == PT.BOGO else max(1, p.buy_quantity)
        y = 1 if t == PT.BOGO else max(1, p.get_quantity)
        rewards_cfg = _entries(p, "reward")
        alloc: dict[int, int] = {}
        if not rewards_cfg:
            el = sorted([u for u in _eligible_units(p, units) if u.remaining > 0], key=lambda u: -u.price)
            groups = len(el) // (x + y)
            if p.max_applications:
                groups = min(groups, p.max_applications)
            for g in range(groups):
                grp = el[g * (x + y):(g + 1) * (x + y)]
                for u in grp[x:]:  # cheapest Y of each group
                    alloc[id(u)] = min(u.remaining, _pct(u.price, p.reward_percent_bps))
            return alloc
        reward_units = sorted([u for u in _eligible_units(p, units, "reward") if u.remaining > 0],
                              key=lambda u: u.price)
        buy_units = [u for u in _eligible_units(p, units) if u not in reward_units]
        apps = len(buy_units) // x
        if p.max_applications:
            apps = min(apps, p.max_applications)
        for u in reward_units[:apps * y]:
            alloc[id(u)] = min(u.remaining, _pct(u.price, p.reward_percent_bps))
        return alloc
    if t == PT.FREE_PRODUCT:
        qualifiers = _entries(p, "eligible")
        reward_units = sorted([u for u in _eligible_units(p, units, "reward") if u.remaining > 0],
                              key=lambda u: -u.price)
        if qualifiers:
            q_units = [u for u in _eligible_units(p, units) if u not in reward_units]
            if len(q_units) < max(1, p.buy_quantity):
                return {}
        limit = max(1, p.get_quantity) * (p.max_applications or 1)
        return {id(u): min(u.remaining, _pct(u.price, p.reward_percent_bps)) for u in reward_units[:limit]}
    if t == PT.FREE_MODIFIER:
        mods = _entries(p, "modifier")
        mod_ids = {m.modifier_id for m in mods if m.modifier_id}
        group_ids = {m.modifier_group_id for m in mods if m.modifier_group_id}
        alloc = {}
        el = _eligible_units(p, units)
        if p.max_applications:
            el = el[:p.max_applications]
        for u in el:
            charged = sorted([m for m in u.line.modifiers if m.price_cents > 0 and (
                m.modifier_id in mod_ids or m.group_id in group_ids)], key=lambda m: -m.price_cents)
            free = sum(m.price_cents for m in charged[:max(1, p.get_quantity)])
            v = min(u.remaining, _pct(free, p.reward_percent_bps))
            if v > 0:
                alloc[id(u)] = v
        return alloc
    if t == PT.BUNDLE:
        slots: dict[int, list] = {}
        for e in _entries(p, "bundle"):
            slots.setdefault(e.slot, []).append(e)
        if not slots:
            return {}
        pool = sorted([u for u in units if u.remaining > 0 and u.line.product_id], key=lambda u: -u.price)
        used: set[int] = set()
        alloc = {}
        apps = 0
        while not p.max_applications or apps < p.max_applications:
            chosen: list[Unit] = []
            for slot, entries in sorted(slots.items()):
                need = max(e.quantity for e in entries)
                picks = [u for u in pool if id(u) not in used and u not in chosen and _matches(u.line, entries)][:need]
                if len(picks) < need:
                    chosen = []
                    break
                chosen += picks
            if not chosen:
                break
            value = sum(u.remaining for u in chosen)
            saving = value - p.value
            for u in chosen:
                used.add(id(u))
            if saving > 0:
                for k, v in _spread(chosen, saving).items():
                    alloc[k] = alloc.get(k, 0) + v
            apps += 1
        return alloc
    return {}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate(ctx: Context, *, codes: list[str] | None = None, promotion_ids: list[int] | None = None,
             candidates=None) -> Evaluation:
    ev = Evaluation()
    units = [Unit(l, n, l.unit_price_cents) for l in ctx.lines for n in range(l.quantity)]
    codes = [c.strip().upper() for c in (codes or []) if c and c.strip()]
    promotion_ids = [int(i) for i in (promotion_ids or [])]
    pool = list(candidates if candidates is not None else store_candidates(ctx.store))
    by_id = {p.id: p for p in pool}

    requested: list[tuple[Promotion, PromotionCode | None, bool]] = []
    for c in codes:
        pc = PromotionCode.objects.select_related("promotion").filter(code=c).first()
        if pc is None or pc.promotion_id not in by_id:
            ev.rejected.append({"code": c, "reason": "Unknown or invalid code" if pc is None
                                else "Not valid at this store"})
            continue
        requested.append((by_id[pc.promotion_id], pc, True))
    for pid in promotion_ids:
        p = by_id.get(pid)
        if p is None:
            ev.rejected.append({"promotion_id": pid, "reason": "Promotion not available at this store"})
        elif p.requires_code:
            ev.rejected.append({"promotion_id": pid, "reason": "A promotion code is required"})
        else:
            requested.append((p, None, True))
    explicit_ids = {p.id for p, _, _ in requested}
    autos = [(p, None, False) for p in pool if p.auto_apply and not p.requires_code and p.id not in explicit_ids]

    eligible: list[tuple[Promotion, PromotionCode | None, bool]] = []
    for p, code, explicit in requested + autos:
        try:
            check_eligible(p, ctx, code)
            eligible.append((p, code, explicit))
        except Reject as r:
            if explicit:
                ev.rejected.append({"code": code.code if code else None, "promotion_id": p.id, "name": p.name,
                                    "reason": str(r)})

    def savings(p):
        return sum(_calc(p, units).values())

    eligible.sort(key=lambda t: (-t[0].priority, -savings(t[0])))
    for p, code, explicit in eligible:
        blocking = [a for a in ev.applied if not a.promotion.combinable]
        if ev.applied and (not p.combinable or blocking):
            other = (blocking or ev.applied)[0].promotion.name
            if explicit:
                ev.rejected.append({"code": code.code if code else None, "promotion_id": p.id, "name": p.name,
                                    "reason": f"Cannot be combined with '{other}'"})
            continue
        alloc = _calc(p, units)
        total = sum(alloc.values())
        if p.max_discount_cents is not None and total > p.max_discount_cents:
            excess = total - p.max_discount_cents
            for k in sorted(alloc, key=lambda k: alloc[k]):
                cut = min(alloc[k], excess)
                alloc[k] -= cut
                excess -= cut
                if not excess:
                    break
            total = p.max_discount_cents
        if total <= 0:
            if explicit:
                ev.rejected.append({"code": code.code if code else None, "promotion_id": p.id, "name": p.name,
                                    "reason": "No qualifying items in the order"})
            continue
        line_alloc: dict[int, int] = {}
        for u in units:
            v = alloc.get(id(u), 0)
            if v:
                u.remaining -= v
                line_alloc[u.line.index] = line_alloc.get(u.line.index, 0) + v
        ev.applied.append(Applied(p, code, total, line_alloc, manual=explicit))

    applied_ids = {a.promotion.id for a in ev.applied}
    for p in pool:
        if p.id in applied_ids or p.auto_apply or p.requires_code:
            continue
        try:
            check_eligible(p, ctx)
        except Reject:
            continue
        ev.available.append({"promotion_id": p.id, "name": p.name, "description": p.description,
                             "type": p.promo_type, "requires_code": p.requires_code,
                             "requires_manager": p.requires_manager})
    return ev


def redemption_counts(promotion_ids):
    return dict(PromotionRedemption.objects.filter(promotion_id__in=promotion_ids, status="applied")
                .values("promotion_id").annotate(n=Count("id")).values_list("promotion_id", "n"))
