"""Applying promotions to orders, recalculating totals/taxes, redemption bookkeeping, reporting."""
from __future__ import annotations

from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.accounts.services import authorize_manager
from apps.catalog.engine import compute_tax, tax_rates_for
from apps.core.audit import audit
from apps.core.exceptions import DomainError

from . import engine
from .models import Promotion, PromotionRedemption


# ---------------------------------------------------------------------------
# Engine inputs
# ---------------------------------------------------------------------------
def lines_from_priced(priced: list) -> list[engine.EngineLine]:
    """From catalogue PricedLine objects (quotes / intake before persistence)."""
    out = []
    for idx, pl in enumerate(priced):
        if pl is None:
            continue
        out.append(engine.EngineLine(
            index=idx, product_id=pl.product.id, category_id=pl.product.category_id, sku=pl.product.sku,
            name=pl.product.name, size_code=pl.size.code if pl.size else "", unit_price_cents=pl.unit_price_cents,
            quantity=pl.quantity,
            modifiers=[engine.EngineModifier(m.modifier.code, m.modifier.id, m.modifier.group_id, m.unit_price_cents)
                       for m in pl.modifiers]))
    return out


def lines_from_order(order) -> list[engine.EngineLine]:
    out = []
    for it in order.items.select_related("product").prefetch_related("modifiers__modifier"):
        if it.product_id is None:
            continue
        out.append(engine.EngineLine(
            index=it.id, product_id=it.product_id, category_id=it.product.category_id, sku=it.sku, name=it.name,
            size_code=it.size_code, unit_price_cents=it.unit_price_cents, quantity=it.quantity,
            modifiers=[engine.EngineModifier(m.code, m.modifier_id, m.modifier.group_id if m.modifier else None,
                                             m.price_cents) for m in it.modifiers.all()]))
    return out


def context_for(store, *, channel, order_type, lines, customer: dict | None = None, exclude_order_id=None):
    c = customer or {}
    return engine.Context(store=store, channel=channel, order_type=order_type, lines=lines,
                          customer_phone=c.get("phone", "") or "", customer_email=c.get("email", "") or "",
                          customer_id=c.get("id"), exclude_order_id=exclude_order_id)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------
def recalculate_totals(order) -> None:
    """Recompute line taxes on discounted amounts and the order totals (internal orders only)."""
    from apps.orders.models import Order

    if order.is_third_party:
        return
    subtotal = discount = tax = 0
    breakdown: dict[str, int] = {}
    for it in order.items.select_related("product"):
        subtotal += it.line_total_cents
        discount += it.discount_cents
        if it.product_id:
            t, bd = compute_tax(max(0, it.line_total_cents - it.discount_cents),
                                tax_rates_for(order.store, it.product.tax_category_id))
        else:
            t, bd = it.tax_cents, {}
        if t != it.tax_cents or bd != it.tax_breakdown:
            it.tax_cents, it.tax_breakdown = t, bd
            it.save(update_fields=["tax_cents", "tax_breakdown"])
        tax += t
        for k, v in bd.items():
            breakdown[k] = breakdown.get(k, 0) + v
    for ch in order.charges or []:
        tax += int(ch.get("tax_cents") or 0)
        for k, v in (ch.get("tax_breakdown") or {}).items():
            breakdown[k] = breakdown.get(k, 0) + int(v)
    order.subtotal_cents = subtotal
    order.discount_cents = discount
    order.tax_cents = tax
    order.tax_breakdown = breakdown
    order.total_cents = subtotal - discount + order.charges_cents + tax + order.tip_cents
    Order.objects.filter(pk=order.pk).update(subtotal_cents=subtotal, discount_cents=discount, tax_cents=tax,
                                             tax_breakdown=breakdown, total_cents=order.total_cents)


def _allocate_manual(items, manual_discounts) -> dict[int, int]:
    """Spread manual (non-promotion) discounts over remaining line value."""
    remaining = {it.id: it.line_total_cents - it.discount_cents for it in items}
    alloc: dict[int, int] = {}
    for d in manual_discounts:
        pool = sum(v for v in remaining.values() if v > 0)
        amount = min(d.amount_cents, pool)
        left = amount
        keys = [k for k, v in remaining.items() if v > 0]
        for i, k in enumerate(keys):
            share = left if i == len(keys) - 1 else min(remaining[k], amount * remaining[k] // pool if pool else 0)
            share = min(share, remaining[k], left)
            alloc[k] = alloc.get(k, 0) + share
            remaining[k] -= share
            left -= share
        if d.amount_cents != amount:
            type(d).objects.filter(pk=d.pk).update(amount_cents=amount)
    return alloc


# ---------------------------------------------------------------------------
# Applying to an order
# ---------------------------------------------------------------------------
def apply_promotions(order, *, codes=None, promotion_ids=None, user=None, manager_code=None,
                     keep_existing=True) -> engine.Evaluation:
    """(Re)evaluate all promotions on an internal order inside the caller's transaction.

    Previously applied codes/promotions are re-validated (orders are revalidated whenever they
    change). Invalid explicitly requested codes raise 422 so nothing is saved. Redemption limits
    are enforced with row locks on the limited promotions to stay correct under concurrency."""
    from apps.orders.models import OrderDiscount, OrderItem

    if order.is_third_party:
        return engine.Evaluation()
    codes = list(codes or [])
    promotion_ids = list(promotion_ids or [])
    existing = list(order.promotion_redemptions.filter(status=PromotionRedemption.STATUS_APPLIED)
                    .select_related("code", "promotion"))
    if keep_existing:
        for r in existing:
            if r.code:
                codes.append(r.code.code)
            elif not r.promotion.auto_apply:
                promotion_ids.append(r.promotion_id)
    candidates = list(engine.store_candidates(order.store))
    limited = [p.id for p in candidates if p.max_redemptions is not None or p.max_per_customer is not None
               or any(c.max_uses is not None for c in p.codes.all())]
    if limited:
        list(Promotion.objects.select_for_update().filter(id__in=limited).order_by("id"))
    ctx = context_for(order.store, channel=order.source, order_type=order.order_type, lines=lines_from_order(order),
                      customer={**(order.customer_snapshot or {}), "id": order.customer_id},
                      exclude_order_id=order.pk)
    ev = engine.evaluate(ctx, codes=codes, promotion_ids=promotion_ids, candidates=candidates)
    new_codes = {c.strip().upper() for c in (codes or [])} - {r.code.code for r in existing if r.code}
    bad = [r for r in ev.rejected if (r.get("code") in new_codes) or (r.get("promotion_id") in promotion_ids
                                                                       and not r.get("code"))]
    if bad:
        raise DomainError("Promotion could not be applied", code="promotion_invalid",
                          errors=[{"code": "promotion_invalid", "message": f"{b.get('code') or b.get('name')}: "
                                   f"{b['reason']}", "ref": b.get("code") or b.get("promotion_id")} for b in bad])
    manager = None
    if any(a.manual and a.promotion.requires_manager for a in ev.applied) and user is not None:
        manager = authorize_manager(order.store, manager_code, "orders.apply_discount", user)

    # Replace previous promotion state for this order.
    order.promotion_redemptions.filter(status=PromotionRedemption.STATUS_APPLIED).update(
        status=PromotionRedemption.STATUS_VOIDED)
    OrderDiscount.objects.filter(order=order, promotion__isnull=False).delete()
    items = list(order.items.all())
    line_disc = ev.line_discounts()
    for a in ev.applied:
        r = PromotionRedemption.objects.create(
            promotion=a.promotion, code=a.code, order=order, store=order.store, customer_key=ctx.customer_key,
            channel=order.source, amount_cents=a.amount_cents, applied_by=user, authorized_by=manager)
        OrderDiscount.objects.create(order=order, promotion=a.promotion, redemption=r,
                                     code=a.code.code if a.code else "", name=a.promotion.name,
                                     amount_cents=a.amount_cents, applied_by=user, authorized_by=manager,
                                     allocation={str(k): v for k, v in a.line_allocations.items()})
    for it in items:
        it.discount_cents = line_disc.get(it.id, 0)
    manual = list(OrderDiscount.objects.filter(order=order, promotion__isnull=True).order_by("id"))
    for k, v in _allocate_manual(items, manual).items():
        for it in items:
            if it.id == k:
                it.discount_cents += v
    OrderItem.objects.bulk_update(items, ["discount_cents"])
    recalculate_totals(order)
    if ev.applied:
        audit("promotion.applied", order, entity_type="Order", actor=user, authorized_by=manager,
              data={"promotions": [a.as_dict() for a in ev.applied]})
    return ev


def void_redemptions(order) -> int:
    return order.promotion_redemptions.filter(status=PromotionRedemption.STATUS_APPLIED).update(
        status=PromotionRedemption.STATUS_VOIDED, updated_at=timezone.now())


# ---------------------------------------------------------------------------
# Status maintenance (Celery beat)
# ---------------------------------------------------------------------------
def refresh_statuses() -> list[tuple[int, str, str]]:
    changed = []
    now = timezone.now()
    for p in Promotion.objects.all():
        new = p.derive_status(now)
        if new != p.status:
            changed.append((p.id, p.status, new))
            Promotion.objects.filter(pk=p.pk).update(status=new)
            audit(f"promotion.{new}", p, entity_type="Promotion", actor_label="system",
                  data={"from": p.status, "to": new})
    return changed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def performance(promotions_qs, orders_qs) -> dict:
    """Promotion KPIs within a scoped set of orders (same store scope and period)."""
    from apps.orders.models import OrderDiscount, OrderItem, OrderStatus

    valid_orders = orders_qs.exclude(status__in=[OrderStatus.CANCELLED, OrderStatus.REJECTED])
    total_orders = valid_orders.count()
    overall_aov = valid_orders.aggregate(a=Avg("total_cents"))["a"] or 0
    red = PromotionRedemption.objects.filter(status=PromotionRedemption.STATUS_APPLIED, promotion__in=promotions_qs,
                                             order__in=valid_orders)
    rows = []
    for p in promotions_qs:
        pr = red.filter(promotion=p)
        orders = valid_orders.filter(promotion_redemptions__in=pr).distinct()
        agg = orders.aggregate(rev=Sum("total_cents"), aov=Avg("total_cents"), n=Count("id"))
        rows.append({
            "promotion_id": p.id, "name": p.name, "campaign": p.campaign, "type": p.promo_type, "status": p.status,
            "scope": p.scope, "redemptions": pr.count(),
            "discount_cents": pr.aggregate(s=Sum("amount_cents"))["s"] or 0,
            "revenue_cents": agg["rev"] or 0, "average_order_cents": int(agg["aov"] or 0),
            "redemption_rate": round((agg["n"] or 0) / total_orders, 4) if total_orders else 0.0,
            "by_store": list(pr.values("store__number").annotate(redemptions=Count("id"),
                                                                 discount_cents=Sum("amount_cents"))
                             .order_by("-discount_cents")),
        })
    # Product-level discount attribution from per-line allocations.
    item_names = {}
    by_product: dict[str, dict] = {}
    for od in OrderDiscount.objects.filter(redemption__in=red).only("allocation"):
        for item_id, cents in (od.allocation or {}).items():
            if item_id not in item_names:
                it = OrderItem.objects.filter(pk=item_id).only("sku", "name").first()
                item_names[item_id] = (it.sku, it.name) if it else ("?", "?")
            sku, name = item_names[item_id]
            row = by_product.setdefault(sku, {"sku": sku, "name": name, "discount_cents": 0, "lines": 0})
            row["discount_cents"] += int(cents)
            row["lines"] += 1
    campaigns: dict[str, dict] = {}
    for r in rows:
        c = campaigns.setdefault(r["campaign"] or "(none)", {"campaign": r["campaign"] or "(none)", "redemptions": 0,
                                                             "discount_cents": 0, "revenue_cents": 0})
        c["redemptions"] += r["redemptions"]
        c["discount_cents"] += r["discount_cents"]
        c["revenue_cents"] += r["revenue_cents"]
    promo_orders = valid_orders.filter(promotion_redemptions__in=red).distinct()
    return {
        "totals": {"orders": total_orders, "orders_with_promotions": promo_orders.count(),
                   "redemptions": red.count(), "discount_cents": red.aggregate(s=Sum("amount_cents"))["s"] or 0,
                   "revenue_with_promotions_cents": promo_orders.aggregate(s=Sum("total_cents"))["s"] or 0,
                   "average_order_cents": int(overall_aov),
                   "average_order_with_promotion_cents": int(promo_orders.aggregate(a=Avg("total_cents"))["a"] or 0),
                   "redemption_rate": round(promo_orders.count() / total_orders, 4) if total_orders else 0.0},
        "promotions": sorted(rows, key=lambda r: -r["discount_cents"]),
        "by_product": sorted(by_product.values(), key=lambda r: -r["discount_cents"]),
        "by_campaign": sorted(campaigns.values(), key=lambda r: -r["discount_cents"]),
    }


def can_manage(user, promotion_or_scope: dict) -> tuple[bool, str]:
    """Authorization for creating/editing a promotion with the given owner scope."""
    from .models import FranchisePromotionPolicy

    franchisee_id = promotion_or_scope.get("franchisee_id")
    store = promotion_or_scope.get("store")
    if user.has_permission("promotions.manage_network") and (user.is_hq or user.is_superuser):
        return True, ""
    if franchisee_id is None and store is None:
        return False, "Only headquarters can manage network-wide promotions"
    fid = store.franchisee_id if store is not None else franchisee_id
    pol = FranchisePromotionPolicy.objects.filter(franchisee_id=fid).first()
    if pol is None or not pol.can_create:
        return False, "Headquarters has not permitted this franchisee to create promotions"
    if user.is_franchise_level and user.franchisee_id == fid and user.has_permission("promotions.manage_franchise"):
        return True, ""
    if store is not None and user.has_permission("promotions.manage_store"):
        from apps.accounts.services import user_can_operate_store

        if not (pol.hq_allows_store_managers and pol.store_managers_can_manage):
            return False, "Your franchisee has not authorized store managers to manage promotions"
        if user_can_operate_store(user, store):
            return True, ""
    return False, "Not permitted to manage promotions for this scope"


def check_policy(promo: Promotion) -> None:
    from .models import FranchisePromotionPolicy

    if promo.scope == Promotion.SCOPE_HQ:
        return
    fid = promo.store.franchisee_id if promo.store_id else promo.franchisee_id
    pol = FranchisePromotionPolicy.objects.filter(franchisee_id=fid).first()
    if pol is None or promo.promo_type not in (pol.allowed_types or []):
        raise DomainError(f"Headquarters does not allow franchise '{promo.promo_type}' promotions",
                          code="promotion_type_not_allowed", status_code=403)
    if promo.promo_type == "percent" and promo.value > pol.max_percent_bps:
        raise DomainError(f"Percentage exceeds the HQ maximum of {pol.max_percent_bps / 100:.0f}%",
                          code="promotion_limit", status_code=403)
    if promo.promo_type == "fixed" and promo.value > pol.max_fixed_cents:
        raise DomainError(f"Amount exceeds the HQ maximum of {pol.max_fixed_cents} cents", code="promotion_limit",
                          status_code=403)


@transaction.atomic
def save_promotion(promo: Promotion, user, *, items=None, targets=None, rules=None, codes=None, is_new=False):
    from apps.organization.models import Store

    from .models import PromotionCode, PromotionProduct, PromotionRule, PromotionStore

    check_policy(promo)
    if promo.promo_type in ("percent",) and promo.value > 10000:
        raise DomainError("Percentage cannot exceed 100%", code="invalid_value", status_code=400)
    if promo.requires_code and promo.auto_apply:
        promo.auto_apply = False
    promo.status = promo.derive_status()
    if is_new:
        promo.created_by = user
    promo.save()
    if items is not None:
        promo.items.all().delete()
        PromotionProduct.objects.bulk_create([PromotionProduct(promotion=promo, **i) for i in items])
    if targets is not None:
        if promo.scope != Promotion.SCOPE_HQ:
            fid = promo.store.franchisee_id if promo.store_id else promo.franchisee_id
            for t in targets:
                st = t.get("store")
                if t.get("franchisee") and t["franchisee"].id != fid:
                    raise DomainError("Franchise promotions can only target the franchisee's own stores",
                                      code="invalid_target", status_code=403)
                if st and (st.franchisee_id if isinstance(st, Store) else None) != fid:
                    raise DomainError("Franchise promotions can only target the franchisee's own stores",
                                      code="invalid_target", status_code=403)
        promo.targets.all().delete()
        PromotionStore.objects.bulk_create([PromotionStore(promotion=promo, **t) for t in targets])
    if rules is not None:
        promo.rules.all().delete()
        PromotionRule.objects.bulk_create([PromotionRule(promotion=promo, **r) for r in rules])
    for c in codes or []:
        code = c["code"].strip().upper()
        existing = PromotionCode.objects.filter(code=code).first()
        if existing and existing.promotion_id != promo.id:
            raise DomainError(f"Code {code} is already used by another promotion", code="duplicate_code",
                              status_code=409)
        PromotionCode.objects.update_or_create(code=code, defaults={"promotion": promo,
                                                                    "max_uses": c.get("max_uses"),
                                                                    "is_active": c.get("is_active", True)})
    audit("promotion.create" if is_new else "promotion.update", promo, entity_type="Promotion", actor=user,
          franchisee=promo.franchisee, store=promo.store,
          data={"type": promo.promo_type, "value": promo.value, "status": promo.status})
    return promo
