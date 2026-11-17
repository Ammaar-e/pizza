"use client";
import { useMemo, useState } from "react";
import { Button, Modal, cx } from "@/components/ui";
import { money, uuid } from "@/lib/format";
import { CartLine, LineMod, MenuProduct, estimateUnit } from "@/lib/pricing";

type Placement = "whole" | "left" | "right";

export default function Customizer({ product, initial, onClose, onAdd }: { product: MenuProduct; initial?: CartLine; onClose: () => void; onAdd: (l: CartLine) => void }) {
  const p = product;
  const defaults = new Set(p.default_modifiers.map((d) => d.code));
  const [line, setLine] = useState<CartLine>(
    initial ?? {
      key: uuid(),
      product: p,
      size: p.sizes.find((s) => s.code === "L")?.code ?? p.sizes[0]?.code,
      crust: p.default_crust ?? p.crusts[0]?.code,
      quantity: 1,
      modifiers: [],
      removed: [],
      special_instructions: "",
    },
  );
  const unit = useMemo(() => estimateUnit(line), [line]);

  const modState = (code: string) => line.modifiers.find((m) => m.code === code);
  function setMod(code: string, next: LineMod | null) {
    const rest = line.modifiers.filter((m) => m.code !== code);
    setLine({ ...line, modifiers: next ? [...rest, next] : rest });
  }
  function toggleDefault(code: string) {
    const removed = line.removed.includes(code) ? line.removed.filter((c) => c !== code) : [...line.removed, code];
    setLine({ ...line, removed, modifiers: line.modifiers.filter((m) => m.code !== code) });
  }
  const groupCount = (g: MenuProduct["modifier_groups"][number]) =>
    g.modifiers.filter((m) => modState(m.code) || (defaults.has(m.code) && !line.removed.includes(m.code))).length;

  return (
    <Modal open wide onClose={onClose} title={<span>{p.name} <span className="ml-2 text-brand-600">{money(unit * line.quantity)}</span></span>}>
      <div className="space-y-5">
        {p.sizes.length > 0 && (
          <section>
            <h4 className="mb-2 text-sm font-semibold uppercase text-slate-500">Size</h4>
            <div className="grid grid-cols-4 gap-2">
              {p.sizes.map((s) => (
                <button key={s.code} onClick={() => setLine({ ...line, size: s.code })} className={cx("touch-target rounded-xl border-2 p-2 text-center", line.size === s.code ? "border-brand-600 bg-brand-50" : "border-slate-200")}>
                  <div className="font-semibold">{s.name}</div><div className="text-sm text-slate-500">{money(s.price_cents)}</div>
                </button>
              ))}
            </div>
          </section>
        )}
        {p.crusts.length > 0 && (
          <section>
            <h4 className="mb-2 text-sm font-semibold uppercase text-slate-500">Crust</h4>
            <div className="grid grid-cols-4 gap-2">
              {p.crusts.map((c) => (
                <button key={c.code} onClick={() => setLine({ ...line, crust: c.code })} className={cx("touch-target rounded-xl border-2 p-2", line.crust === c.code ? "border-brand-600 bg-brand-50" : "border-slate-200")}>
                  <div className="font-semibold">{c.name}</div>{c.price_cents > 0 && <div className="text-sm text-slate-500">+{money(c.price_cents)}</div>}
                </button>
              ))}
            </div>
          </section>
        )}
        {p.default_modifiers.length > 0 && (
          <section>
            <h4 className="mb-2 text-sm font-semibold uppercase text-slate-500">Included — tap to remove</h4>
            <div className="flex flex-wrap gap-2">
              {p.default_modifiers.map((d) => (
                <button key={d.code} disabled={!d.removable} onClick={() => toggleDefault(d.code)} className={cx("touch-target rounded-full border-2 px-4", line.removed.includes(d.code) ? "border-red-300 bg-red-50 text-red-700 line-through" : "border-emerald-300 bg-emerald-50")}>
                  {line.removed.includes(d.code) ? "No " : ""}{d.name}
                </button>
              ))}
            </div>
          </section>
        )}
        {p.modifier_groups.map((g) => (
          <section key={g.code}>
            <h4 className="mb-2 text-sm font-semibold uppercase text-slate-500">
              {g.name} <span className="font-normal normal-case">({groupCount(g)}{g.max_select ? ` / max ${g.max_select}` : ""}{g.min_select ? `, min ${g.min_select}` : ""})</span>
            </h4>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
              {g.modifiers.map((m) => {
                const st = modState(m.code);
                const isDefault = defaults.has(m.code) && !line.removed.includes(m.code);
                const halves = p.allow_half_and_half && g.allow_half;
                return (
                  <div key={m.code} className={cx("rounded-xl border-2 p-2", st ? "border-brand-600 bg-brand-50" : isDefault ? "border-emerald-300" : "border-slate-200", !m.is_available && "opacity-40")}>
                    <button disabled={!m.is_available} className="w-full text-left" onClick={() => setMod(m.code, st ? null : { code: m.code, placement: "whole", quantity: isDefault ? 2 : 1 })}>
                      <div className="font-medium">{m.name}{!m.is_available && " (86)"}</div>
                      <div className="text-xs text-slate-500">{isDefault ? "included" : m.price_cents ? `+${money(m.price_cents)}` : ""}</div>
                    </button>
                    {st && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {halves && (["left", "whole", "right"] as Placement[]).map((pl) => (
                          <button key={pl} onClick={() => setMod(m.code, { ...st, placement: pl })} className={cx("rounded px-2 py-1 text-xs font-semibold", st.placement === pl ? "bg-brand-600 text-white" : "bg-white")}>
                            {pl === "whole" ? "Whole" : pl === "left" ? "Left ½" : "Right ½"}
                          </button>
                        ))}
                        {g.max_quantity_per_option > 1 && (
                          <button onClick={() => setMod(m.code, { ...st, quantity: st.quantity > 1 ? 1 : 2 })} className={cx("rounded px-2 py-1 text-xs font-semibold", st.quantity > 1 ? "bg-ink-800 text-white" : "bg-white")}>Extra</button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
        <section className="flex flex-wrap items-center gap-3">
          <input className="min-w-[200px] flex-1 rounded-lg border px-3 py-3" placeholder="Special instructions (e.g. well done)" value={line.special_instructions} onChange={(e) => setLine({ ...line, special_instructions: e.target.value })} />
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="lg" onClick={() => setLine({ ...line, quantity: Math.max(1, line.quantity - 1) })}>−</Button>
            <span className="w-8 text-center text-xl font-bold">{line.quantity}</span>
            <Button variant="secondary" size="lg" onClick={() => setLine({ ...line, quantity: line.quantity + 1 })}>+</Button>
          </div>
          <Button size="xl" onClick={() => onAdd(line)}>{initial ? "Update" : "Add"} · {money(unit * line.quantity)}</Button>
        </section>
      </div>
    </Modal>
  );
}
