"use client";
// HQ menu setup: the reference data products need (categories, tax categories & rates, kitchen prep categories,
// crusts, topping/option groups). Prices are entered in dollars and tax rates in percent.
import { Fragment, useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Input, Notice, Select, Table, Toggle, cx } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { money } from "@/lib/format";

type Kind = "text" | "dollars" | "int" | "bool" | "percent" | "select";
interface Col { key: string; label: string; kind: Kind; options?: [string | number, string][]; placeholder?: string; width?: string }

const PROVINCES: [string, string][] = [["*", "All other regions (default)"], ["AB", "Alberta"], ["BC", "British Columbia"], ["MB", "Manitoba"],
  ["NB", "New Brunswick"], ["NL", "Newfoundland & Labrador"], ["NS", "Nova Scotia"], ["NT", "Northwest Territories"], ["NU", "Nunavut"],
  ["ON", "Ontario"], ["PE", "Prince Edward Island"], ["QC", "Quebec"], ["SK", "Saskatchewan"], ["YT", "Yukon"]];

const asDollars = (cents: any) => (cents === null || cents === undefined || cents === "" ? "" : (Number(cents) / 100).toFixed(2));
const toCents = (v: string) => Math.round(Number(String(v).replace(",", ".")) * 100);

function toForm(cols: Col[], row: any) {
  const f: any = {};
  for (const c of cols) {
    const v = row?.[c.key];
    f[c.key] = c.kind === "dollars" ? asDollars(v ?? 0) : c.kind === "percent" ? (v === undefined ? "" : String(Number(v) / 100)) : c.kind === "bool" ? (v === undefined ? c.key === "is_active" : !!v) : v ?? "";
  }
  return f;
}
function toBody(cols: Col[], form: any) {
  const b: any = {};
  for (const c of cols) {
    const v = form[c.key];
    if (c.kind === "dollars") b[c.key] = toCents(v || "0");
    else if (c.kind === "percent") b[c.key] = Math.round(Number(String(v).replace(",", ".")) * 100);
    else if (c.kind === "int") b[c.key] = Number(v || 0);
    else if (c.kind === "select") b[c.key] = typeof c.options?.[0]?.[0] === "number" ? Number(v) : v;
    else b[c.key] = v;
  }
  return b;
}

function Cell({ col, value, onChange }: { col: Col; value: any; onChange: (v: any) => void }) {
  if (col.kind === "bool") return <Toggle checked={!!value} onChange={onChange} />;
  if (col.kind === "select")
    return <Select value={value} onChange={(e) => onChange(e.target.value)}>{col.options!.map(([v, l]) => <option key={String(v)} value={v}>{l}</option>)}</Select>;
  return (
    <div className="relative">
      {col.kind === "dollars" && <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-sm text-slate-400">$</span>}
      <Input className={cx(col.kind === "dollars" && "pl-5", col.kind === "percent" && "pr-6")} value={value}
        inputMode={col.kind === "text" ? undefined : "decimal"} placeholder={col.placeholder}
        onChange={(e) => onChange(col.kind === "text" && col.key === "code" ? e.target.value.toUpperCase().replace(/\s+/g, "-") : e.target.value)} />
      {col.kind === "percent" && <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-sm text-slate-400">%</span>}
    </div>
  );
}

function show(col: Col, v: any) {
  if (col.kind === "dollars") return money(v);
  if (col.kind === "percent") return `${Number(v) / 100}%`;
  if (col.kind === "bool") return v ? "Yes" : "—";
  if (col.kind === "select") return col.options?.find(([o]) => String(o) === String(v))?.[1] ?? v;
  return v === "" || v === null || v === undefined ? "—" : String(v);
}

/** Editable list for one reference endpoint. */
function RefTable({ endpoint, cols, rows, fixed, onChanged, addLabel, emptyText }: {
  endpoint: string; cols: Col[]; rows: any[]; fixed?: Record<string, any>; onChanged: () => void; addLabel: string; emptyText: string;
}) {
  const [adding, setAdding] = useState<any | null>(null);
  const [editing, setEditing] = useState<{ id: number; form: any } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<any>, done: () => void) => {
    setError(null);
    try { await fn(); done(); onChanged(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };
  return (
    <div className="space-y-2">
      <ErrorBanner error={error} />
      <Table head={[...cols.map((c) => c.label), ""]} empty={!rows.length && !adding}>
        {rows.map((r) => editing && editing.id === r.id ? (
          <tr key={r.id} className="bg-brand-50/40">
            {cols.map((c) => <td key={c.key} className="px-2 py-1" style={{ width: c.width }}><Cell col={c} value={editing.form[c.key]} onChange={(v) => setEditing((cur) => cur && { ...cur, form: { ...cur.form, [c.key]: v } })} /></td>)}
            <td className="whitespace-nowrap px-2">
              <Button size="sm" onClick={() => run(() => portalApi.patch(`${endpoint}${r.id}/`, { ...toBody(cols, editing.form), ...fixed }), () => setEditing(null))}>Save</Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            </td>
          </tr>
        ) : (
          <tr key={r.id}>
            {cols.map((c) => <td key={c.key} className={cx("px-3 py-2", c.key === "code" && "font-mono text-xs")}>{show(c, r[c.key])}</td>)}
            <td className="whitespace-nowrap px-2 text-right">
              <Button size="sm" variant="ghost" onClick={() => setEditing({ id: r.id, form: toForm(cols, r) })}>Edit</Button>
              <Button size="sm" variant="ghost" onClick={() => confirm(`Delete ${r.name ?? r.code}?`) && run(() => portalApi.del(`${endpoint}${r.id}/`), () => undefined)}>Delete</Button>
            </td>
          </tr>
        ))}
        {adding && (
          <tr className="bg-emerald-50/60">
            {cols.map((c) => <td key={c.key} className="px-2 py-1" style={{ width: c.width }}><Cell col={c} value={adding[c.key]} onChange={(v) => setAdding({ ...adding, [c.key]: v })} /></td>)}
            <td className="whitespace-nowrap px-2">
              <Button size="sm" onClick={() => run(() => portalApi.post(endpoint, { ...toBody(cols, adding), ...fixed }), () => setAdding(null))}>Add</Button>
              <Button size="sm" variant="ghost" onClick={() => setAdding(null)}>Cancel</Button>
            </td>
          </tr>
        )}
      </Table>
      {!rows.length && !adding && <p className="text-sm text-slate-500">{emptyText}</p>}
      {!adding && <Button size="sm" variant="secondary" onClick={() => setAdding(toForm(cols, { ...Object.fromEntries(cols.filter((c) => c.kind === "select").map((c) => [c.key, c.options?.[0]?.[0]])) }))}>+ {addLabel}</Button>}
    </div>
  );
}

type Tab = "categories" | "taxes" | "prep" | "crusts" | "options";

export default function CatalogSetup({ onChanged }: { onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>("categories");
  const [data, setData] = useState<any>({ categories: [], taxes: [], rates: [], preps: [], crusts: [], groups: [] });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = () =>
    Promise.all(["/categories/", "/tax-categories/", "/tax-rates/", "/prep-categories/", "/crusts/", "/modifier-groups/"].map((p) => portalApi.get(p)))
      .then(([categories, taxes, rates, preps, crusts, groups]) => setData({ categories, taxes, rates, preps, crusts, groups }))
      .catch((e) => setError(e instanceof ApiError ? e.describe() : String(e)));
  useEffect(() => { load(); }, []);
  const changed = () => { load(); onChanged(); };

  async function starter() {
    setError(null);
    setNotice(null);
    try {
      const r = await portalApi.post("/catalog/starter-setup/", {});
      const made = Object.entries(r.created).filter(([, n]) => Number(n) > 0).map(([k, n]) => `${n} ${k.replace("_", " ")}`);
      setNotice(made.length ? `Created ${made.join(", ")}. Review the tax rates for your provinces.` : "Everything from the starter setup already exists.");
      changed();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }

  const missing = [!data.categories.length && "categories", !data.taxes.length && "tax categories", !data.preps.length && "kitchen prep categories"].filter(Boolean);
  const tabs: [Tab, string, number][] = [
    ["categories", "Menu categories", data.categories.length], ["taxes", "Tax categories & rates", data.taxes.length],
    ["prep", "Kitchen prep categories", data.preps.length], ["crusts", "Crusts", data.crusts.length], ["options", "Toppings & options", data.groups.length],
  ];

  return (
    <Card title="Menu setup" actions={<Button variant="secondary" onClick={starter}>Load starter setup</Button>}>
      <div className="space-y-3">
        {missing.length > 0 && (
          <Notice tone="warn">
            Products need {missing.join(", ")}. Add them below, or click <b>Load starter setup</b> to create the standard pizza-shop set
            (Pizzas/Sides/Drinks, prepared-food tax with GST/HST rates, oven/fryer/cold prep, crusts, toppings, sauces and dips).
          </Notice>
        )}
        <ErrorBanner error={error} />
        {notice && <Notice tone="ok">{notice}</Notice>}
        <div className="flex flex-wrap gap-1 border-b">
          {tabs.map(([k, label, n]) => (
            <button key={k} onClick={() => setTab(k)} className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-semibold", tab === k ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800")}>
              {label} <span className="ml-1 rounded-full bg-slate-100 px-1.5 text-xs text-slate-600">{n}</span>
            </button>
          ))}
        </div>

        {tab === "categories" && (
          <RefTable endpoint="/categories/" rows={data.categories} onChanged={changed} addLabel="Add category" emptyText="No menu categories yet (e.g. Pizzas, Sides, Drinks)."
            cols={[{ key: "code", label: "Code", kind: "text", placeholder: "PIZZA", width: "140px" }, { key: "name", label: "Name", kind: "text", placeholder: "Pizzas" },
              { key: "sort_order", label: "Order on menu", kind: "int", width: "120px" }, { key: "is_active", label: "Active", kind: "bool", width: "80px" }]} />
        )}

        {tab === "taxes" && (
          <div className="space-y-5">
            <p className="text-sm text-slate-500">
              Each product has a tax category. Rates are per province (the store's <b>region</b>, e.g. AB, ON); stores in a province without
              its own rates use the <b>All other regions</b> rates. Several rates in one province add up (e.g. QC: GST + QST).
            </p>
            <RefTable endpoint="/tax-categories/" rows={data.taxes} onChanged={changed} addLabel="Add tax category" emptyText="No tax categories yet (e.g. FOOD — Prepared food)."
              cols={[{ key: "code", label: "Code", kind: "text", placeholder: "FOOD", width: "160px" }, { key: "name", label: "Name", kind: "text", placeholder: "Prepared food" }]} />
            {data.taxes.map((t: any) => (
              <div key={t.id} className="rounded-xl border p-3">
                <div className="mb-2 font-semibold">Rates for {t.name} <span className="font-mono text-xs text-slate-400">{t.code}</span></div>
                <RefTable endpoint="/tax-rates/" rows={data.rates.filter((r: any) => r.tax_category === t.id)} fixed={{ tax_category: t.id }} onChanged={changed}
                  addLabel="Add rate" emptyText="No rates: products in this tax category are not taxed."
                  cols={[{ key: "region", label: "Province / region", kind: "select", options: PROVINCES }, { key: "name", label: "Tax name", kind: "text", placeholder: "GST" },
                    { key: "rate_bps", label: "Rate", kind: "percent", placeholder: "5", width: "120px" }]} />
              </div>
            ))}
          </div>
        )}

        {tab === "prep" && (
          <>
            <p className="text-sm text-slate-500">Tells the kitchen display where an item is made (oven, fryer, cold station). Stations can be set per store.</p>
            <RefTable endpoint="/prep-categories/" rows={data.preps} onChanged={changed} addLabel="Add prep category" emptyText="No kitchen prep categories yet (e.g. OVEN — Pizza oven)."
              cols={[{ key: "code", label: "Code", kind: "text", placeholder: "OVEN", width: "160px" }, { key: "name", label: "Name", kind: "text", placeholder: "Pizza oven" }]} />
          </>
        )}

        {tab === "crusts" && (
          <RefTable endpoint="/crusts/" rows={data.crusts} onChanged={changed} addLabel="Add crust" emptyText="No crusts yet."
            cols={[{ key: "code", label: "Code", kind: "text", placeholder: "THIN", width: "120px" }, { key: "name", label: "Name", kind: "text", placeholder: "Thin" },
              { key: "price_cents", label: "Extra price", kind: "dollars", width: "120px" }, { key: "sort_order", label: "Order", kind: "int", width: "90px" },
              { key: "is_active", label: "Active", kind: "bool", width: "80px" }]} />
        )}

        {tab === "options" && (
          <div className="space-y-5">
            <p className="text-sm text-slate-500">Option groups (toppings, sauce, dips…) with their choices. Attach groups to products in the product editor.</p>
            <RefTable endpoint="/modifier-groups/" rows={data.groups} onChanged={changed} addLabel="Add option group" emptyText="No option groups yet (e.g. TOPPINGS)."
              cols={[{ key: "code", label: "Code", kind: "text", placeholder: "TOPPINGS", width: "130px" }, { key: "name", label: "Name", kind: "text", placeholder: "Toppings" },
                { key: "min_select", label: "Min", kind: "int", width: "70px" }, { key: "max_select", label: "Max (0=∞)", kind: "int", width: "90px" },
                { key: "max_quantity_per_option", label: "Max each", kind: "int", width: "85px" }, { key: "free_selections", label: "Free", kind: "int", width: "70px" },
                { key: "allow_half", label: "Halves", kind: "bool", width: "70px" }, { key: "half_price_percent", label: "Half price %", kind: "int", width: "95px" },
                { key: "scale_with_size", label: "Price × size", kind: "bool", width: "90px" }]} />
            {data.groups.map((g: any) => (
              <Fragment key={g.id}>
                <div className="rounded-xl border p-3">
                  <div className="mb-2 flex items-center gap-2 font-semibold">{g.name} choices <Badge>{g.modifiers.length}</Badge></div>
                  <RefTable endpoint="/modifiers/" rows={g.modifiers} fixed={{ group: g.id }} onChanged={changed} addLabel={`Add ${g.name.toLowerCase()} choice`} emptyText="No choices yet."
                    cols={[{ key: "code", label: "Code", kind: "text", placeholder: "TOP-PEP", width: "140px" }, { key: "name", label: "Name", kind: "text", placeholder: "Pepperoni" },
                      { key: "price_cents", label: "Price", kind: "dollars", width: "110px" }, { key: "is_topping", label: "Topping", kind: "bool", width: "80px" },
                      { key: "is_active", label: "Active", kind: "bool", width: "80px" }, { key: "sort_order", label: "Order", kind: "int", width: "80px" }]} />
                </div>
              </Fragment>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
