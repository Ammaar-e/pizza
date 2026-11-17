// Client-side price estimate mirroring the server engine (backend/apps/catalog/engine.py).
// The server quote is always authoritative; this only gives instant feedback in the customizer.
import type { CartLine, LineMod } from './types';

export function estimateUnit(line: CartLine): number {
  const p = line.product;
  const size = p.sizes.find((s) => s.code === line.size);
  let price = size ? size.price_cents : p.price_cents ?? 0;
  const crust = p.crusts.find((c) => c.code === (line.crust || p.default_crust));
  price += crust?.price_cents ?? 0;
  const mult = (size?.modifier_multiplier_percent ?? 100) / 100;
  const defaults = new Map(p.default_modifiers.map((d) => [d.code, d.quantity]));
  for (const g of p.modifier_groups) {
    const chosen = line.modifiers
      .map((m) => ({ m, def: g.modifiers.find((x) => x.code === m.code) }))
      .filter((x) => x.def) as { m: LineMod; def: { price_cents: number; code: string } }[];
    const priced = chosen.map(({ m, def }) => {
      const isDefault = defaults.has(m.code) && !line.removed.includes(m.code);
      const units = Math.max(0, m.quantity - (isDefault && m.placement === "whole" ? defaults.get(m.code)! : 0));
      let unit = def.price_cents * (g.scale_with_size ? mult : 1);
      if (m.placement !== "whole") unit = (unit * g.half_price_percent) / 100;
      return { isDefault, value: Math.round(unit * units) };
    });
    let free = g.free_selections;
    priced.sort((a, b) => b.value - a.value);
    for (const x of priced) {
      if (free > 0 && !x.isDefault && x.value > 0) {
        x.value = 0;
        free--;
      }
      price += x.value;
    }
  }
  return price;
}

export function lineToApi(l: CartLine) {
  return {
    product: l.product.sku,
    size: l.size || null,
    crust: l.crust || null,
    quantity: l.quantity,
    modifiers: l.modifiers.map((m) => ({ code: m.code, placement: m.placement, quantity: m.quantity })),
    removed: l.removed,
    special_instructions: l.special_instructions,
  };
}

export function describeLine(l: CartLine): string[] {
  const out: string[] = [];
  const p = l.product;
  const crust = p.crusts.find((c) => c.code === l.crust);
  if (crust) out.push(crust.name + " crust");
  const names = new Map(p.modifier_groups.flatMap((g) => g.modifiers.map((m) => [m.code, m.name] as const)));
  for (const m of l.modifiers) {
    const place = m.placement === "whole" ? "" : ` (${m.placement === "left" ? "L½" : "R½"})`;
    out.push(`${m.quantity > 1 ? "Extra " : "+ "}${names.get(m.code) ?? m.code}${place}`);
  }
  for (const r of l.removed) out.push(`No ${p.default_modifiers.find((d) => d.code === r)?.name ?? r}`);
  if (l.special_instructions) out.push(`“${l.special_instructions}”`);
  return out;
}
