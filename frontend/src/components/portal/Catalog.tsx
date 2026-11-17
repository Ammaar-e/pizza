"use client";
import { useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Select, Table, Toggle } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { money } from "@/lib/format";
import CatalogSetup from "./CatalogSetup";

const dollars = (c: any) => (c === null || c === undefined || c === "" ? "" : (Number(c) / 100).toFixed(2));
const cents = (v: string) => (v.trim() === "" ? null : Math.round(Number(v.replace(",", ".")) * 100));

export default function Catalog() {
  const [products, setProducts] = useState<any[]>([]);
  const [ref, setRef] = useState<any>({ categories: [], taxes: [], preps: [], crusts: [], groups: [] });
  const [edit, setEdit] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => portalApi.get("/products/?page_size=200").then((d) => setProducts(results(d)));
  const loadRef = () =>
    Promise.all(["/categories/", "/tax-categories/", "/prep-categories/", "/crusts/", "/modifier-groups/"].map((p) => portalApi.get(p))).then(
      ([categories, taxes, preps, crusts, groups]) => setRef({ categories, taxes, preps, crusts, groups }),
    );
  useEffect(() => {
    load();
    loadRef();
  }, []);
  const missing = [!ref.categories.length && "a menu category", !ref.taxes.length && "a tax category", !ref.preps.length && "a kitchen prep category"].filter(Boolean);

  async function save() {
    setError(null);
    const priceProblem = [["Base price", edit.base_price_cents], ["Min franchise price", edit.min_price_cents], ["Max franchise price", edit.max_price_cents]]
      .find(([, v]) => v !== null && v !== undefined && !Number.isFinite(v));
    if (priceProblem) return setError(`${priceProblem[0]}: enter an amount like 12.99`);
    const body = {
      ...edit,
      crusts: edit.crusts || [],
      modifier_group_links: (edit.modifier_groups || []).map((g: any, i: number) => ({ group: g.code ?? g, sort_order: i })),
      default_modifier_links: (edit.default_modifiers || []).map((d: any) => ({ modifier: d.code, quantity: d.quantity || 1, removable: d.removable ?? true })),
    };
    delete body.modifier_groups;
    delete body.default_modifiers;
    delete body.image;
    try {
      if (edit.id) await portalApi.patch(`/products/${edit.id}/`, body);
      else await portalApi.post("/products/", body);
      setEdit(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }

  return (
    <div className="space-y-6">
    <CatalogSetup onChanged={loadRef} />
    <Card title="Master product catalogue" actions={<Button disabled={missing.length > 0} title={missing.length ? `Create ${missing.join(", ")} first (Menu setup)` : undefined} onClick={() => setEdit({ sku: "", name: "", status: "draft", category: ref.categories[0]?.id, tax_category: ref.taxes[0]?.id, prep_category: ref.preps[0]?.id, base_price_cents: 0, sizes: [], crusts: [], modifier_groups: [], default_modifiers: [], allow_price_override: true })}>New product</Button>}>
      {missing.length > 0 && <p className="mb-3 text-sm text-amber-700">To add products, first create {missing.join(", ")} in <b>Menu setup</b> above (or load the starter setup).</p>}
      <Table head={["SKU", "Product", "Category", "Status", "Prices", "Permitted range", "v", ""]} empty={!products.length}>
        {products.map((p) => (
          <tr key={p.id}>
            <td className="px-3 py-2 font-mono text-xs">{p.sku}</td>
            <td className="px-3 font-medium">{p.name}{p.is_pizza && <span className="ml-1 text-xs text-brand-600">pizza</span>}</td>
            <td className="px-3">{p.category_code}</td>
            <td className="px-3"><Badge tone={p.status === "approved" ? "connected" : p.status === "retired" ? "disabled" : "pending"}>{p.status}</Badge></td>
            <td className="px-3 text-xs">{p.sizes.length ? p.sizes.map((s: any) => `${s.code} ${money(s.price_cents)}`).join(" · ") : money(p.base_price_cents)}</td>
            <td className="px-3 text-xs">{p.min_price_cents == null && p.max_price_cents == null ? <span className="text-slate-400">any price</span> : `${p.min_price_cents == null ? "…" : money(p.min_price_cents)} – ${p.max_price_cents == null ? "…" : money(p.max_price_cents)}`}</td>
            <td className="px-3 text-xs">{p.version}</td>
            <td className="px-3"><Button size="sm" variant="ghost" onClick={() => setEdit({ ...p, crusts: p.crusts, modifier_groups: p.modifier_groups.map((g: any) => g.code) })}>Edit</Button></td>
          </tr>
        ))}
      </Table>
      {edit && (
        <Modal open wide title={edit.id ? `Edit ${edit.sku}` : "New product"} onClose={() => setEdit(null)}>
          <ErrorBanner error={error} />
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="SKU"><Input value={edit.sku} onChange={(e) => setEdit({ ...edit, sku: e.target.value })} /></Field>
            <Field label="Name"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Status"><Select value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option value="draft">Draft</option><option value="approved">Approved for franchisees</option><option value="retired">Retired</option></Select></Field>
            <Field label="Category"><Select value={edit.category} onChange={(e) => setEdit({ ...edit, category: Number(e.target.value) })}>{ref.categories.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
            <Field label="Tax category"><Select value={edit.tax_category} onChange={(e) => setEdit({ ...edit, tax_category: Number(e.target.value) })}>{ref.taxes.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
            <Field label="Kitchen prep category"><Select value={edit.prep_category} onChange={(e) => setEdit({ ...edit, prep_category: Number(e.target.value) })}>{ref.preps.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
            <Field label="Base price $ (products without sizes)"><Input inputMode="decimal" placeholder="12.99" defaultValue={dollars(edit.base_price_cents)} onChange={(e) => setEdit({ ...edit, base_price_cents: cents(e.target.value) ?? 0 })} /></Field>
            <Field label="Min franchise price $"><Input inputMode="decimal" placeholder="optional" defaultValue={dollars(edit.min_price_cents)} onChange={(e) => setEdit({ ...edit, min_price_cents: cents(e.target.value) })} /></Field>
            <Field label="Max franchise price $"><Input inputMode="decimal" placeholder="optional" defaultValue={dollars(edit.max_price_cents)} onChange={(e) => setEdit({ ...edit, max_price_cents: cents(e.target.value) })} /></Field>
            <Field label="Image URL"><Input value={edit.image_url || ""} onChange={(e) => setEdit({ ...edit, image_url: e.target.value })} /></Field>
            <Field label="Max toppings (0 = no limit)"><Input type="number" value={edit.max_toppings ?? 0} onChange={(e) => setEdit({ ...edit, max_toppings: Number(e.target.value) })} /></Field>
            <div className="space-y-2 pt-5 text-sm">
              <label className="flex items-center gap-2"><Toggle checked={!!edit.is_pizza} onChange={(v) => setEdit({ ...edit, is_pizza: v })} /> Pizza</label>
              <label className="flex items-center gap-2"><Toggle checked={!!edit.allow_half_and_half} onChange={(v) => setEdit({ ...edit, allow_half_and_half: v })} /> Half & half</label>
              <label className="flex items-center gap-2"><Toggle checked={!!edit.allow_price_override} onChange={(v) => setEdit({ ...edit, allow_price_override: v })} /> Franchise pricing</label>
            </div>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <Field label="Sizes (code:name:price $:topping price %)" hint="One per line, e.g. L:Large:17.49:125 — leave empty for a single-price item">
              <textarea className="h-28 w-full rounded-lg border p-2 font-mono text-xs" defaultValue={(edit.sizes || []).map((s: any) => `${s.code}:${s.name}:${dollars(s.price_cents)}:${s.modifier_multiplier_percent}`).join("\n")}
                onChange={(e) => setEdit({ ...edit, sizes: e.target.value.split("\n").filter(Boolean).map((l, i) => { const [code, name, price, mult] = l.split(":"); return { code, name, price_cents: cents(price || "") ?? 0, modifier_multiplier_percent: Number(mult || 100), sort_order: i }; }) })} />
            </Field>
            <Field label="Crusts">
              <div className="space-y-1">{ref.crusts.map((c: any) => (
                <label key={c.code} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={(edit.crusts || []).includes(c.code)} onChange={(e) => setEdit({ ...edit, crusts: e.target.checked ? [...(edit.crusts || []), c.code] : edit.crusts.filter((x: string) => x !== c.code) })} />{c.name}</label>
              ))}</div>
            </Field>
            <Field label="Modifier groups">
              <div className="space-y-1">{ref.groups.map((g: any) => (
                <label key={g.code} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={(edit.modifier_groups || []).includes(g.code)} onChange={(e) => setEdit({ ...edit, modifier_groups: e.target.checked ? [...(edit.modifier_groups || []), g.code] : edit.modifier_groups.filter((x: string) => x !== g.code) })} />{g.name} <span className="text-xs text-slate-400">({g.min_select}–{g.max_select || "∞"})</span></label>
              ))}</div>
            </Field>
          </div>
          <Field label="Default ingredients (removable)">
            <div className="flex flex-wrap gap-2">{ref.groups.flatMap((g: any) => g.modifiers).map((m: any) => {
              const on = (edit.default_modifiers || []).some((d: any) => d.code === m.code);
              return <label key={m.code} className="flex items-center gap-1 rounded border px-2 py-0.5 text-xs"><input type="checkbox" checked={on} onChange={(e) => setEdit({ ...edit, default_modifiers: e.target.checked ? [...(edit.default_modifiers || []), { code: m.code, quantity: 1, removable: true }] : edit.default_modifiers.filter((d: any) => d.code !== m.code) })} />{m.name}</label>;
            })}</div>
          </Field>
          <Button className="mt-4" onClick={save}>Save product (new version)</Button>
        </Modal>
      )}
    </Card>
    </div>
  );
}
