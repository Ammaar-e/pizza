"""Receipt rendering: structured JSON plus a 42-column plain-text layout for thermal printers."""
from __future__ import annotations

WIDTH = 42


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _row(left: str, right: str = "") -> str:
    left = left[: WIDTH - len(right) - 1]
    return left + " " * (WIDTH - len(left) - len(right)) + right


def render_receipt(order) -> dict:
    store = order.store
    lines: list[str] = []
    if store.receipt_header:
        lines += [h.center(WIDTH) for h in store.receipt_header.splitlines()]
    lines += [store.name.center(WIDTH), store.address_line1.center(WIDTH),
              f"{store.city}, {store.region} {store.postal_code}".center(WIDTH), store.phone.center(WIDTH), ""]
    lines.append(_row(f"Order #{order.order_number}", order.get_order_type_display()))
    lines.append(_row(order.placed_at.astimezone(store.tz).strftime("%Y-%m-%d %H:%M"), order.get_source_display()))
    if order.external_display_id:
        lines.append(_row("Provider ref", order.external_display_id))
    name = (order.customer_snapshot or {}).get("name")
    if name:
        lines.append(_row("Customer", name))
    lines.append("-" * WIDTH)
    for item in order.items.prefetch_related("modifiers"):
        title = f"{item.quantity}x {item.size_name + ' ' if item.size_name else ''}{item.name}"
        lines.append(_row(title, money(item.line_total_cents)))
        if item.crust_name:
            lines.append(f"   {item.crust_name}")
        for m in item.modifiers.all():
            if m.is_default and m.quantity == 1 and m.placement == "whole":
                continue
            place = "" if m.placement == "whole" else f" ({m.placement.upper()})"
            extra = "EXTRA " if m.quantity > 1 else ""
            lines.append(_row(f"   + {extra}{m.name}{place}", money(m.price_cents * item.quantity) if m.price_cents
                              else ""))
        for r in item.removed or []:
            lines.append(f"   - NO {r.get('name')}")
        if item.special_instructions:
            lines.append(f"   * {item.special_instructions}")
    lines.append("-" * WIDTH)
    lines.append(_row("Subtotal", money(order.subtotal_cents)))
    for d in order.discounts.all():
        lines.append(_row(f"Discount: {d.name}", money(-d.amount_cents)))
    for c in order.charges or []:
        lines.append(_row(c.get("name", "Charge"), money(c.get("amount_cents", 0))))
    for name, amt in (order.tax_breakdown or {}).items():
        lines.append(_row(name, money(amt)))
    if not order.tax_breakdown and order.tax_cents:
        lines.append(_row("Tax", money(order.tax_cents)))
    if order.tip_cents:
        lines.append(_row("Tip", money(order.tip_cents)))
    lines.append(_row("TOTAL", money(order.total_cents)))
    payments = []
    for p in order.payments.all():
        label = {"cash": "Cash", "card": f"Card {p.card_brand} ****{p.card_last4}".strip(),
                 "online": "Online payment", "provider": f"Paid via {order.get_source_display()}"}.get(p.method, p.method)
        lines.append(_row(f"{label} [{p.status}]", money(p.amount_cents)))
        if p.change_cents:
            lines.append(_row("Change", money(p.change_cents)))
        if p.refunded_cents:
            lines.append(_row("Refunded", money(-p.refunded_cents)))
        payments.append({"method": p.method, "status": p.status, "amount_cents": p.amount_cents})
    lines.append("")
    if store.receipt_footer:
        lines += [f.center(WIDTH) for f in store.receipt_footer.splitlines()]
    lines.append(f"Order ID {str(order.pk)[:8]}".center(WIDTH))
    return {"order_id": str(order.pk), "order_number": order.order_number, "width": WIDTH, "text": "\n".join(lines),
            "payments": payments, "total_cents": order.total_cents}
