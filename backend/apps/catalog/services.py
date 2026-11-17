from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.audit import audit
from apps.core.exceptions import DomainError

from .models import CatalogVersion, Product, ProductRequest, StoreProduct


def snapshot_product(p: Product) -> dict:
    return {
        "sku": p.sku, "name": p.name, "status": p.status, "category": p.category.code,
        "base_price_cents": p.base_price_cents, "min_price_cents": p.min_price_cents,
        "max_price_cents": p.max_price_cents, "sizes": [{"code": s.code, "price_cents": s.price_cents}
                                                        for s in p.sizes.all()],
        "crusts": [c.code for c in p.crusts.all()],
        "modifier_groups": [g.group.code for g in p.productmodifiergroup_set.select_related("group")],
        "defaults": [d.modifier.code for d in p.default_modifiers.select_related("modifier")],
    }


def record_version(kind: str, entity_id, data: dict, user=None, store=None, note: str = "") -> CatalogVersion:
    with transaction.atomic():
        last = CatalogVersion.objects.select_for_update().filter(kind=kind, entity_id=str(entity_id)).aggregate(
            m=Max("version"))["m"] or 0
        return CatalogVersion.objects.create(kind=kind, entity_id=str(entity_id), version=last + 1, data=data,
                                             changed_by=user, store=store, note=note)


def validate_store_pricing(sp: StoreProduct):
    p = sp.product
    prices = []
    if sp.price_cents is not None:
        prices.append(sp.price_cents)
    prices += [int(v) for v in (sp.size_prices or {}).values()]
    for ch in (sp.channel_prices or {}).values():
        prices += [int(v) for v in ch.values()]
    if prices and not p.allow_price_override:
        raise DomainError(f"Headquarters does not permit price changes for {p.name}", code="price_override_forbidden")
    valid_sizes = {s.code for s in p.sizes.all()}
    for code in (sp.size_prices or {}):
        if code not in valid_sizes:
            raise DomainError(f"Unknown size '{code}' for {p.name}", code="invalid_size")
    for price in prices:
        if p.min_price_cents is not None and price < p.min_price_cents:
            raise DomainError(f"{p.name}: price {price} is below the permitted minimum {p.min_price_cents}",
                              code="price_out_of_range")
        if p.max_price_cents is not None and price > p.max_price_cents:
            raise DomainError(f"{p.name}: price {price} is above the permitted maximum {p.max_price_cents}",
                              code="price_out_of_range")


def review_request(req: ProductRequest, user, approve: bool, notes: str = "", product: Product | None = None):
    if req.status != ProductRequest.STATUS_PENDING:
        raise DomainError("Request already reviewed", code="already_reviewed", status_code=409)
    req.status = ProductRequest.STATUS_APPROVED if approve else ProductRequest.STATUS_REJECTED
    req.reviewed_by = user
    req.reviewed_at = timezone.now()
    req.review_notes = notes
    req.resulting_product = product
    req.save()
    audit("product_request.review", req, data={"approved": approve, "product": product.sku if product else None})
    return req
