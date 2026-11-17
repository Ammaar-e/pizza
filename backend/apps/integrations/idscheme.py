"""Identifier scheme used in menus we publish to providers.

Because this platform publishes the menus, each provider item/option carries our
own identifier (DoorDash ``merchant_supplied_id``, Uber Eats ``external_data``).
Incoming orders echo these ids back, which makes product/modifier mapping
deterministic. Explicit ``ProductMapping`` rows override for ids not created here.

    item   -> "<SKU>"                     e.g. "PZ-PEP"
    option -> "SIZE:<code>"               e.g. "SIZE:L"
              "CRUST:<code>"              e.g. "CRUST:THIN"
              "MOD:<code>"                e.g. "MOD:TOP-MUSH"
              "PLACE:<whole|left|right>"  nested under a MOD option (half & half)
              "NO:<code>"                 remove a default ingredient
    groups -> "GRP:SIZE", "GRP:CRUST", "GRP:<group code>", "GRP:PLACE", "GRP:NO"
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.orders.intake import NormalizedItem, NormalizedModifier


@dataclass
class RawOption:
    external_id: str
    name: str = ""
    quantity: int = 1
    price_cents: int | None = None
    children: list["RawOption"] = field(default_factory=list)


def resolve(external_id: str | None, mappings: dict[str, str]) -> str | None:
    if not external_id:
        return None
    return mappings.get(external_id, external_id)


def build_item(*, external_id: str | None, name: str, quantity: int, unit_price_cents: int | None,
               options: list[RawOption], special_instructions: str, mappings: dict[str, str],
               known_skus: set[str]) -> NormalizedItem:
    sku = resolve(external_id, mappings)
    item = NormalizedItem(product=sku if sku in known_skus else None, external_id=external_id, name=name,
                          quantity=max(1, int(quantity or 1)), unit_price_cents=unit_price_cents,
                          special_instructions=special_instructions or "")
    for opt in options:
        _apply_option(item, opt, mappings)
    return item


def _apply_option(item: NormalizedItem, opt: RawOption, mappings: dict[str, str]):
    ref = resolve(opt.external_id, mappings) or ""
    kind, _, code = ref.partition(":")
    if kind == "SIZE":
        item.size = code
    elif kind == "CRUST":
        item.crust = code
    elif kind == "NO":
        item.removed.append(code)
    elif kind == "MOD":
        placement = "whole"
        for child in opt.children:
            cref = resolve(child.external_id, mappings) or ""
            ck, _, cc = cref.partition(":")
            if ck == "PLACE" and cc in ("whole", "left", "right"):
                placement = cc
        item.modifiers.append(NormalizedModifier(code=code, external_id=opt.external_id, name=opt.name,
                                                 placement=placement, quantity=max(1, int(opt.quantity or 1)),
                                                 price_cents=opt.price_cents))
        return
    else:
        # Unknown option id: keep it so the order is flagged as unmapped, never silently dropped.
        item.modifiers.append(NormalizedModifier(code=None, external_id=opt.external_id, name=opt.name,
                                                 quantity=max(1, int(opt.quantity or 1)),
                                                 price_cents=opt.price_cents))
    for child in opt.children:
        _apply_option(item, child, mappings)
