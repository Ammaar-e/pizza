"""Pizza customization and pricing engine.

The engine is the single source of truth for what an item costs at a given store
on a given channel. Every order source (POS, call centre, external API, delivery
provider) prices and validates lines through ``price_line``.

Line input format (used by the normalized internal order format):

    {
      "product": "PZ-PEP",                # product SKU (or numeric id)
      "size": "L",                         # size code (required if product has sizes)
      "crust": "REG",                      # crust code (pizza only; default crust if omitted)
      "quantity": 1,
      "modifiers": [                       # additions / extras
        {"code": "TOP-MUSH", "placement": "whole", "quantity": 1},
        {"code": "TOP-PEP",  "placement": "whole", "quantity": 2},   # 2 = extra (default already 1)
        {"code": "TOP-ONION", "placement": "left", "quantity": 1}    # half-and-half
      ],
      "removed": ["TOP-CHEESE"],           # remove default ingredients
      "special_instructions": "well done"
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Prefetch

from .models import (
    AdditionalCharge,
    Crust,
    Modifier,
    Product,
    ProductDefaultModifier,
    ProductModifierGroup,
    ProductSize,
    StoreModifierAvailability,
    StoreProduct,
    StoreTaxOverride,
    TaxRate,
)

PLACEMENTS = {"whole", "left", "right"}


@dataclass
class LineIssue:
    code: str
    message: str
    ref: str | None = None

    def as_dict(self):
        return {"code": self.code, "message": self.message, "ref": self.ref}


class LineValidationError(Exception):
    def __init__(self, issues: list[LineIssue]):
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues))


@dataclass
class PricedModifier:
    modifier: Modifier
    placement: str
    quantity: int
    is_default: bool
    unit_price_cents: int  # charged per item unit

    def as_dict(self):
        return {
            "code": self.modifier.code,
            "name": self.modifier.name,
            "group": self.modifier.group.code,
            "placement": self.placement,
            "quantity": self.quantity,
            "is_default": self.is_default,
            "price_cents": self.unit_price_cents,
        }


@dataclass
class PricedLine:
    product: Product
    size: ProductSize | None
    crust: Crust | None
    quantity: int
    base_price_cents: int
    crust_price_cents: int
    modifiers: list[PricedModifier] = field(default_factory=list)
    removed: list[Modifier] = field(default_factory=list)
    special_instructions: str = ""
    tax_cents: int = 0
    tax_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def modifiers_price_cents(self) -> int:
        return sum(m.unit_price_cents for m in self.modifiers)

    @property
    def unit_price_cents(self) -> int:
        return self.base_price_cents + self.crust_price_cents + self.modifiers_price_cents

    @property
    def line_total_cents(self) -> int:
        return self.unit_price_cents * self.quantity


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def _get_product(ref) -> Product | None:
    qs = Product.objects.select_related("category", "tax_category", "prep_category", "default_crust").prefetch_related(
        "sizes",
        "crusts",
        Prefetch("productmodifiergroup_set", queryset=ProductModifierGroup.objects.select_related("group")),
        Prefetch("default_modifiers", queryset=ProductDefaultModifier.objects.select_related("modifier__group")),
    )
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        p = qs.filter(pk=int(ref)).first()
        if p:
            return p
    return qs.filter(sku=str(ref)).first()


def store_product_price(store_product: StoreProduct | None, product: Product, size: ProductSize | None,
                        channel: str | None) -> int:
    key = size.code if size else "_"
    if store_product is not None:
        if channel and store_product.channel_prices.get(channel, {}).get(key) is not None:
            return int(store_product.channel_prices[channel][key])
        if size and store_product.size_prices.get(size.code) is not None:
            return int(store_product.size_prices[size.code])
        if not size and store_product.price_cents is not None:
            return store_product.price_cents
    return size.price_cents if size else product.base_price_cents


def unavailable_modifier_ids(store) -> set[int]:
    return set(
        StoreModifierAvailability.objects.filter(store=store, is_available=False).values_list("modifier_id", flat=True)
    )


def tax_rates_for(store, tax_category_id: int) -> list[tuple[str, int]]:
    overrides = list(StoreTaxOverride.objects.filter(store=store, tax_category_id=tax_category_id))
    if overrides:
        return [(o.name, o.rate_bps) for o in overrides]
    regional = list(TaxRate.objects.filter(tax_category_id=tax_category_id, region=store.region))
    if not regional:
        regional = list(TaxRate.objects.filter(tax_category_id=tax_category_id, region="*"))
    return [(r.name, r.rate_bps) for r in regional]


def compute_tax(amount_cents: int, rates: list[tuple[str, int]]) -> tuple[int, dict[str, int]]:
    breakdown: dict[str, int] = {}
    for name, bps in rates:
        t = (Decimal(amount_cents) * Decimal(bps) / Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        breakdown[name] = breakdown.get(name, 0) + int(t)
    return sum(breakdown.values()), breakdown


# ---------------------------------------------------------------------------
# Line pricing
# ---------------------------------------------------------------------------
def price_line(store, line: dict, channel: str | None = None, *, check_availability: bool = True,
               _unavailable_mods: set[int] | None = None) -> PricedLine:
    issues: list[LineIssue] = []
    ref = line.get("product")
    product = _get_product(ref) if ref is not None else None
    if product is None:
        raise LineValidationError([LineIssue("unknown_product", f"Unknown product '{ref}'", str(ref))])
    if product.status != Product.STATUS_APPROVED:
        raise LineValidationError([LineIssue("product_not_approved", f"{product.name} is not approved", product.sku)])

    sp = StoreProduct.objects.filter(store=store, product=product).first()
    if sp is None or not sp.enabled:
        raise LineValidationError([LineIssue("product_not_enabled", f"{product.name} is not on this store's menu",
                                             product.sku)])
    if check_availability and not sp.is_available:
        issues.append(LineIssue("product_unavailable", f"{product.name} is currently unavailable", product.sku))

    qty = int(line.get("quantity") or 1)
    if qty < 1 or qty > 99:
        issues.append(LineIssue("invalid_quantity", "Quantity must be between 1 and 99", product.sku))

    # Size
    sizes = [s for s in product.sizes.all() if s.is_active]
    size = None
    if sizes:
        code = line.get("size")
        size = next((s for s in sizes if s.code == code), None)
        if size is None:
            issues.append(LineIssue("invalid_size", f"Size '{code}' is not valid for {product.name}", product.sku))
    elif line.get("size"):
        issues.append(LineIssue("invalid_size", f"{product.name} has no sizes", product.sku))

    # Crust
    crust = None
    crusts = [c for c in product.crusts.all() if c.is_active]
    if crusts:
        ccode = line.get("crust")
        if ccode:
            crust = next((c for c in crusts if c.code == ccode), None)
            if crust is None:
                issues.append(LineIssue("invalid_crust", f"Crust '{ccode}' not available for {product.name}", ccode))
        else:
            crust = product.default_crust or crusts[0]
    elif line.get("crust"):
        issues.append(LineIssue("invalid_crust", f"{product.name} does not take a crust", product.sku))

    # Modifiers
    groups = {pmg.group_id: pmg for pmg in product.productmodifiergroup_set.all()}
    defaults = {dm.modifier_id: dm for dm in product.default_modifiers.all()}
    unavailable = _unavailable_mods if _unavailable_mods is not None else unavailable_modifier_ids(store)
    multiplier = Decimal(size.modifier_multiplier_percent if size else 100) / Decimal(100)

    removed_codes = set(line.get("removed") or [])
    removed_mods: list[Modifier] = []
    for dm in defaults.values():
        if dm.modifier.code in removed_codes:
            if not dm.removable:
                issues.append(LineIssue("not_removable", f"{dm.modifier.name} cannot be removed", dm.modifier.code))
            removed_mods.append(dm.modifier)
    unknown_removed = removed_codes - {m.code for m in removed_mods}
    for code in unknown_removed:
        issues.append(LineIssue("invalid_removal", f"'{code}' is not a default ingredient of {product.name}", code))

    req_mods = line.get("modifiers") or []
    codes = [m.get("code") for m in req_mods]
    mod_objs = {m.code: m for m in Modifier.objects.select_related("group").filter(code__in=codes)}

    priced: list[PricedModifier] = []
    seen: set[tuple[str, str]] = set()
    for rm in req_mods:
        code = rm.get("code")
        mod = mod_objs.get(code)
        placement = rm.get("placement") or "whole"
        mqty = int(rm.get("quantity") or 1)
        if mod is None or not mod.is_active:
            issues.append(LineIssue("unknown_modifier", f"Unknown modifier '{code}'", code))
            continue
        pmg = groups.get(mod.group_id)
        if pmg is None:
            issues.append(LineIssue("modifier_not_allowed", f"{mod.name} cannot be added to {product.name}", code))
            continue
        if placement not in PLACEMENTS:
            issues.append(LineIssue("invalid_placement", f"Invalid placement '{placement}'", code))
            continue
        if placement != "whole" and not (product.allow_half_and_half and mod.group.allow_half):
            issues.append(LineIssue("half_not_allowed", f"{mod.name} cannot be placed on half of {product.name}", code))
            continue
        if (code, placement) in seen:
            issues.append(LineIssue("duplicate_modifier", f"{mod.name} listed twice", code))
            continue
        seen.add((code, placement))
        max_q = mod.group.max_quantity_per_option or 1
        if mqty < 1 or mqty > max_q:
            issues.append(LineIssue("invalid_modifier_quantity", f"{mod.name}: quantity must be 1-{max_q}", code))
            continue
        if check_availability and mod.id in unavailable:
            issues.append(LineIssue("modifier_unavailable", f"{mod.name} is currently unavailable", code))
        is_default = mod.id in defaults and mod.code not in removed_codes
        chargeable_units = mqty - (defaults[mod.id].quantity if is_default and placement == "whole" else 0)
        chargeable_units = max(chargeable_units, 0)
        unit = Decimal(mod.price_cents)
        if mod.group.scale_with_size:
            unit = unit * multiplier
        if placement != "whole":
            unit = unit * Decimal(mod.group.half_price_percent) / Decimal(100)
        price = int((unit * chargeable_units).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        priced.append(PricedModifier(mod, placement, mqty, is_default, price))

    # Include untouched defaults for kitchen display (no charge)
    explicit_ids = {p.modifier.id for p in priced}
    for dm in defaults.values():
        if dm.modifier.code in removed_codes or dm.modifier_id in explicit_ids:
            continue
        if check_availability and dm.modifier_id in unavailable:
            issues.append(LineIssue("modifier_unavailable", f"{dm.modifier.name} is currently unavailable",
                                    dm.modifier.code))
        priced.append(PricedModifier(dm.modifier, "whole", dm.quantity, True, 0))

    # Group limits & free selections
    for gid, pmg in groups.items():
        group = pmg.group
        in_group = [p for p in priced if p.modifier.group_id == gid]
        n = len({p.modifier.code for p in in_group})
        min_s = pmg.min_select_override if pmg.min_select_override is not None else group.min_select
        max_s = pmg.max_select_override if pmg.max_select_override is not None else group.max_select
        if n < min_s:
            issues.append(LineIssue("min_selections", f"Select at least {min_s} from {group.name}", group.code))
        if max_s and n > max_s:
            issues.append(LineIssue("max_selections", f"Select at most {max_s} from {group.name}", group.code))
        if group.free_selections:
            free_left = group.free_selections
            for p in sorted(in_group, key=lambda x: -x.unit_price_cents):
                if free_left <= 0:
                    break
                if not p.is_default and p.unit_price_cents > 0:
                    p.unit_price_cents = 0
                    free_left -= 1

    if product.max_toppings:
        n_top = len({p.modifier.code for p in priced if p.modifier.is_topping})
        if n_top > product.max_toppings:
            issues.append(LineIssue("max_toppings", f"{product.name} allows at most {product.max_toppings} toppings",
                                    product.sku))

    if issues:
        raise LineValidationError(issues)

    base = store_product_price(sp, product, size, channel)
    pl = PricedLine(
        product=product, size=size, crust=crust, quantity=qty, base_price_cents=base,
        crust_price_cents=crust.price_cents if crust else 0, modifiers=priced, removed=removed_mods,
        special_instructions=(line.get("special_instructions") or "")[:500],
    )
    pl.tax_cents, pl.tax_breakdown = compute_tax(pl.line_total_cents, tax_rates_for(store, product.tax_category_id))
    return pl


def applicable_charges(store, order_type: str) -> list[dict]:
    out = []
    for ch in AdditionalCharge.objects.filter(is_active=True):
        if ch.applies_to == "all" or ch.applies_to == order_type:
            out.append({"code": ch.code, "name": ch.name, "amount_cents": ch.amount_cents,
                        "tax_category_id": ch.tax_category_id})
    if order_type == "delivery":
        fee = int((store.delivery_config or {}).get("delivery_fee_cents") or 0)
        if fee and not any(c["code"] == "DELIVERY" for c in out):
            out.append({"code": "DELIVERY", "name": "Delivery fee", "amount_cents": fee, "tax_category_id": None})
    return out


# ---------------------------------------------------------------------------
# Menu building
# ---------------------------------------------------------------------------
def build_store_menu(store, channel: str | None = None, include_unavailable: bool = True) -> dict:
    """Full menu for a store and channel: categories → products → sizes/crusts/modifier groups."""
    unavailable = unavailable_modifier_ids(store)
    sps = (
        StoreProduct.objects.filter(store=store, enabled=True, product__status=Product.STATUS_APPROVED)
        .select_related("product__category", "product__default_crust", "product__tax_category",
                        "product__prep_category")
        .prefetch_related(
            "product__sizes", "product__crusts",
            Prefetch("product__productmodifiergroup_set",
                     queryset=ProductModifierGroup.objects.select_related("group").prefetch_related(
                         "group__modifiers")),
            Prefetch("product__default_modifiers", queryset=ProductDefaultModifier.objects.select_related("modifier")),
        )
    )
    categories: dict[int, dict] = {}
    for sp in sps:
        p = sp.product
        if not include_unavailable and not sp.is_available:
            continue
        cat = categories.setdefault(p.category_id, {
            "code": p.category.code, "name": p.category.name, "sort_order": p.category.sort_order, "products": []})
        sizes = [s for s in p.sizes.all() if s.is_active]
        cat["products"].append({
            "id": p.id,
            "sku": p.sku,
            "name": p.name,
            "description": p.description,
            "image_url": p.image.url if p.image else p.image_url or None,
            "is_pizza": p.is_pizza,
            "is_available": sp.is_available,
            "is_alcohol": p.is_alcohol,
            "allow_half_and_half": p.allow_half_and_half,
            "max_toppings": p.max_toppings,
            "prep_category": p.prep_category.code,
            "price_cents": None if sizes else store_product_price(sp, p, None, channel),
            "sizes": [
                {"code": s.code, "name": s.name, "price_cents": store_product_price(sp, p, s, channel),
                 "modifier_multiplier_percent": s.modifier_multiplier_percent}
                for s in sizes
            ],
            "crusts": [{"code": c.code, "name": c.name, "price_cents": c.price_cents}
                       for c in p.crusts.all() if c.is_active],
            "default_crust": p.default_crust.code if p.default_crust else None,
            "default_modifiers": [
                {"code": d.modifier.code, "name": d.modifier.name, "quantity": d.quantity, "removable": d.removable}
                for d in p.default_modifiers.all()
            ],
            "modifier_groups": [
                {
                    "code": pmg.group.code,
                    "name": pmg.group.name,
                    "min_select": pmg.min_select_override if pmg.min_select_override is not None
                    else pmg.group.min_select,
                    "max_select": pmg.max_select_override if pmg.max_select_override is not None
                    else pmg.group.max_select,
                    "max_quantity_per_option": pmg.group.max_quantity_per_option,
                    "free_selections": pmg.group.free_selections,
                    "allow_half": pmg.group.allow_half,
                    "half_price_percent": pmg.group.half_price_percent,
                    "scale_with_size": pmg.group.scale_with_size,
                    "modifiers": [
                        {"code": m.code, "name": m.name, "price_cents": m.price_cents, "is_topping": m.is_topping,
                         "is_available": m.id not in unavailable}
                        for m in pmg.group.modifiers.all() if m.is_active
                    ],
                }
                for pmg in p.productmodifiergroup_set.all()
            ],
        })
    cats = sorted(categories.values(), key=lambda c: c["sort_order"])
    for c in cats:
        c.pop("sort_order", None)
    return {
        "store": {"id": store.id, "number": store.number, "name": store.name},
        "channel": channel or "default",
        "currency": store.franchisee.headquarters.currency,
        "categories": cats,
    }
