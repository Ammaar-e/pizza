"use client";
import { useCallback, useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Select, Stat, Table, Toggle } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { DAYS, dt, money } from "@/lib/format";
import { useStores } from "./useStores";

const TYPE_HELP: Record<string, string> = {
  percent: "Value = percent off eligible items (or the whole order if none listed)",
  fixed: "Value = amount off (cents), spread over eligible items",
  bogo: "Buy one, get the cheaper one free (pairs of eligible items)",
  buy_x_get_y: "Buy X eligible items, get Y reward items at reward %",
  free_product: "Reward items free (optionally when an eligible item is bought)",
  free_modifier: "Up to N listed toppings/modifiers free on eligible items",
  bundle: "Value = bundle price (cents) for one item from each slot",
};
const CHANNELS = ["pos", "call_centre", "external_api", "android", "ios"];
const STATUS_TONE: Record<string, string> = { active: "connected", scheduled: "pending", expired: "disabled", disabled: "disabled", draft: "pending" };

const blank = {
  name: "", description: "", campaign: "", promo_type: "percent", value: 1000, max_discount_cents: null, min_subtotal_cents: 0,
  buy_quantity: 1, get_quantity: 1, reward_percent_bps: 10000, max_applications: 0, starts_at: null, ends_at: null,
  start_time: null, end_time: null, days_of_week: [], channels: [], order_types: [], max_redemptions: null,
  max_per_customer: null, auto_apply: true, requires_code: false, priority: 0, combinable: true, requires_manager: false,
  is_enabled: true, items: [], codes: [], targets: [], rules: [],
};

export default function Promotions() {
  const s = usePortalSession();
  const isHq = s.user.role_level === "hq";
  const { stores } = useStores();
  const [rows, setRows] = useState<any[]>([]);
  const [types, setTypes] = useState<any[]>([]);
  const [edit, setEdit] = useState<any | null>(null);
  const [report, setReport] = useState<any>(null);
  const [policies, setPolicies] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    portalApi.get("/promotions/?page_size=200").then((d) => setRows(results(d)));
    portalApi.get("/promotions/types/").then((d) => setTypes(d.types));
    portalApi.get("/reports/promotions/").then(setReport).catch(() => undefined);
    portalApi.get("/promotion-policies/").then(setPolicies).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<any>) => {
    setError(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };
  const canCreate = s.user.permissions.some((p) => ["promotions.manage_network", "promotions.manage_franchise", "promotions.manage_store"].includes(p));

  return (
    <div className="space-y-6">
      <ErrorBanner error={error} />
      {report && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Redemptions (30 d)" value={report.totals.redemptions} />
          <Stat label="Discounts issued" value={money(report.totals.discount_cents)} />
          <Stat label="Revenue with promotions" value={money(report.totals.revenue_with_promotions_cents)} />
          <Stat label="AOV with / overall" value={money(report.totals.average_order_with_promotion_cents)} sub={`overall ${money(report.totals.average_order_cents)}`} />
          <Stat label="Redemption rate" value={`${(report.totals.redemption_rate * 100).toFixed(1)}%`} sub={`${report.totals.orders_with_promotions} of ${report.totals.orders} orders`} />
        </div>
      )}
      <Card title="Promotions" actions={canCreate && <Button onClick={() => setEdit({ ...blank, franchisee: isHq ? null : s.user.franchisee_id })}>New promotion</Button>}>
        <Table head={["Promotion", "Type", "Scope", "Schedule", "Application", "Redemptions", "Status", ""]} empty={!rows.length}>
          {rows.map((p) => (
            <tr key={p.id}>
              <td className="px-3 py-2"><div className="font-medium">{p.name}</div><div className="text-xs text-slate-500">{p.campaign}{p.code_list.length > 0 && ` · codes: ${p.code_list.map((c: any) => c.code).join(", ")}`}</div></td>
              <td className="px-3 text-xs">{p.promo_type.replace(/_/g, " ")}</td>
              <td className="px-3 text-xs">{p.scope}{p.target_list.length > 0 && ` → ${p.target_list.length} target(s)`}</td>
              <td className="px-3 text-xs">{p.starts_at ? dt(p.starts_at) : "now"} – {p.ends_at ? dt(p.ends_at) : "open"}{p.days_of_week.length > 0 && <div>{p.days_of_week.map((d: number) => DAYS[d]).join(" ")}</div>}{p.start_time && <div>{p.start_time.slice(0, 5)}–{p.end_time.slice(0, 5)}</div>}</td>
              <td className="px-3 text-xs">{p.requires_code ? "code" : p.auto_apply ? "automatic" : "staff selects"} · prio {p.priority}{!p.combinable && " · exclusive"}</td>
              <td className="px-3">{p.redemption_count}{p.max_redemptions && ` / ${p.max_redemptions}`}</td>
              <td className="px-3"><Badge tone={STATUS_TONE[p.status]}>{p.status}</Badge></td>
              <td className="px-3"><div className="flex gap-1">
                {canCreate && <Button size="sm" variant="ghost" onClick={() => setEdit({ ...p, items: p.item_list, codes: p.code_list.map((c: any) => ({ code: c.code, max_uses: c.max_uses })), targets: p.target_list, rules: p.rule_list })}>Edit</Button>}
                {canCreate && <Button size="sm" variant="secondary" onClick={() => act(() => portalApi.post(`/promotions/${p.id}/${p.is_enabled ? "disable" : "enable"}/`))}>{p.is_enabled ? "Disable" : "Enable"}</Button>}
              </div></td>
            </tr>
          ))}
        </Table>
      </Card>
      {report && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Performance by promotion">
            <Table head={["Promotion", "Uses", "Discount", "Revenue", "AOV", "Rate"]} empty={!report.promotions.length}>
              {report.promotions.map((r: any) => (
                <tr key={r.promotion_id}><td className="px-3 py-1.5">{r.name}</td><td className="px-3">{r.redemptions}</td><td className="px-3">{money(r.discount_cents)}</td><td className="px-3">{money(r.revenue_cents)}</td><td className="px-3">{money(r.average_order_cents)}</td><td className="px-3">{(r.redemption_rate * 100).toFixed(1)}%</td></tr>
              ))}
            </Table>
          </Card>
          <Card title="By campaign & product">
            <Table head={["Campaign", "Uses", "Discount", "Revenue"]} empty={!report.by_campaign.length}>
              {report.by_campaign.map((c: any) => <tr key={c.campaign}><td className="px-3 py-1.5">{c.campaign}</td><td className="px-3">{c.redemptions}</td><td className="px-3">{money(c.discount_cents)}</td><td className="px-3">{money(c.revenue_cents)}</td></tr>)}
            </Table>
            <div className="mt-4" />
            <Table head={["Product", "Discounted lines", "Discount"]} empty={!report.by_product.length}>
              {report.by_product.map((p: any) => <tr key={p.sku}><td className="px-3 py-1.5">{p.name}</td><td className="px-3">{p.lines}</td><td className="px-3">{money(p.discount_cents)}</td></tr>)}
            </Table>
          </Card>
        </div>
      )}
      {policies.length > 0 && (
        <Card title={isHq ? "Franchise promotion permissions" : "Promotion permissions from headquarters"}>
          <Table head={["Franchisee", "May create", "Allowed types", "Max %", "Max fixed", "HQ allows store managers", "Store managers enabled"]}>
            {policies.map((p) => (
              <tr key={p.franchisee}>
                <td className="px-3 py-2">{p.franchisee_code}</td>
                <td className="px-3"><Toggle checked={p.can_create} disabled={!isHq} onChange={(v) => act(() => portalApi.patch(`/promotion-policies/${p.franchisee}/`, { can_create: v }))} /></td>
                <td className="px-3 text-xs">{isHq ? (
                  <div className="flex flex-wrap gap-1">{types.map((t) => (
                    <label key={t.value} className="flex items-center gap-1"><input type="checkbox" checked={p.allowed_types.includes(t.value)} onChange={(e) => act(() => portalApi.patch(`/promotion-policies/${p.franchisee}/`, { allowed_types: e.target.checked ? [...p.allowed_types, t.value] : p.allowed_types.filter((x: string) => x !== t.value) }))} />{t.label}</label>
                  ))}</div>) : p.allowed_types.join(", ") || "none"}</td>
                <td className="px-3">{p.max_percent_bps / 100}%</td>
                <td className="px-3">{money(p.max_fixed_cents)}</td>
                <td className="px-3"><Toggle checked={p.hq_allows_store_managers} disabled={!isHq} onChange={(v) => act(() => portalApi.patch(`/promotion-policies/${p.franchisee}/`, { hq_allows_store_managers: v }))} /></td>
                <td className="px-3"><Toggle checked={p.store_managers_can_manage} disabled={!p.hq_allows_store_managers} onChange={(v) => act(() => portalApi.patch(`/promotion-policies/${p.franchisee}/`, { store_managers_can_manage: v }))} /></td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      {edit && <PromotionEditor initial={edit} types={types} stores={stores} isHq={isHq} onClose={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function PromotionEditor({ initial, types, stores, isHq, onClose }: any) {
  const [p, setP] = useState<any>(initial);
  const [error, setError] = useState<string | null>(null);
  const [newItem, setNewItem] = useState({ role: "eligible", kind: "category", ref: "", size_code: "", slot: 0 });
  const [codeInput, setCodeInput] = useState("");
  const set = (k: string, v: any) => setP({ ...p, [k]: v });
  const toggleIn = (k: string, v: any) => set(k, p[k].includes(v) ? p[k].filter((x: any) => x !== v) : [...p[k], v]);
  async function save() {
    setError(null);
    const body = { ...p };
    for (const k of ["item_list", "code_list", "target_list", "rule_list", "redemption_count", "scope", "status", "id", "created_at", "updated_at"]) delete body[k];
    try {
      if (p.id) await portalApi.patch(`/promotions/${p.id}/`, body);
      else await portalApi.post("/promotions/", body);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }
  const num = (k: string, nullable = false) => (e: any) => set(k, e.target.value === "" ? (nullable ? null : 0) : Number(e.target.value));
  return (
    <Modal open wide title={p.id ? `Edit ${p.name}` : "New promotion"} onClose={onClose}>
      <div className="space-y-4">
        <ErrorBanner error={error} />
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Name"><Input value={p.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Campaign"><Input value={p.campaign} onChange={(e) => set("campaign", e.target.value)} /></Field>
          <Field label="Type" hint={TYPE_HELP[p.promo_type]}><Select value={p.promo_type} onChange={(e) => set("promo_type", e.target.value)}>
            {types.map((t: any) => <option key={t.value} value={t.value} disabled={!t.allowed}>{t.label}{!t.allowed && " (not permitted)"}</option>)}
          </Select></Field>
          <Field label="Description"><Input value={p.description} onChange={(e) => set("description", e.target.value)} /></Field>
          <Field label={p.promo_type === "percent" ? "Value (basis points, 1000 = 10%)" : p.promo_type === "bundle" ? "Bundle price (cents)" : "Value (cents)"}><Input type="number" value={p.value} onChange={num("value")} /></Field>
          <Field label="Max discount (cents)"><Input type="number" value={p.max_discount_cents ?? ""} onChange={num("max_discount_cents", true)} /></Field>
          <Field label="Minimum order (cents)"><Input type="number" value={p.min_subtotal_cents} onChange={num("min_subtotal_cents")} /></Field>
          {["buy_x_get_y", "free_product", "free_modifier"].includes(p.promo_type) && <>
            <Field label="Buy quantity (X)"><Input type="number" value={p.buy_quantity} onChange={num("buy_quantity")} /></Field>
            <Field label="Get quantity (Y / N free)"><Input type="number" value={p.get_quantity} onChange={num("get_quantity")} /></Field>
            <Field label="Reward % (basis points)"><Input type="number" value={p.reward_percent_bps} onChange={num("reward_percent_bps")} /></Field>
          </>}
          <Field label="Max applications per order (0 = no limit)"><Input type="number" value={p.max_applications} onChange={num("max_applications")} /></Field>
          <Field label="Starts"><Input type="datetime-local" value={p.starts_at ? p.starts_at.slice(0, 16) : ""} onChange={(e) => set("starts_at", e.target.value ? new Date(e.target.value).toISOString() : null)} /></Field>
          <Field label="Ends"><Input type="datetime-local" value={p.ends_at ? p.ends_at.slice(0, 16) : ""} onChange={(e) => set("ends_at", e.target.value ? new Date(e.target.value).toISOString() : null)} /></Field>
          <Field label="Daily window (store time)"><div className="flex gap-1">
            <Input type="time" value={p.start_time?.slice(0, 5) || ""} onChange={(e) => set("start_time", e.target.value || null)} />
            <Input type="time" value={p.end_time?.slice(0, 5) || ""} onChange={(e) => set("end_time", e.target.value || null)} /></div></Field>
          <Field label="Max total redemptions"><Input type="number" value={p.max_redemptions ?? ""} onChange={num("max_redemptions", true)} /></Field>
          <Field label="Max per customer"><Input type="number" value={p.max_per_customer ?? ""} onChange={num("max_per_customer", true)} /></Field>
          <Field label="Priority (higher first)"><Input type="number" value={p.priority} onChange={num("priority")} /></Field>
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <span>Days:</span>{DAYS.map((d, i) => <label key={d} className="flex items-center gap-1"><input type="checkbox" checked={p.days_of_week.includes(i)} onChange={() => toggleIn("days_of_week", i)} />{d}</label>)}
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <span>Channels:</span>{CHANNELS.map((c) => <label key={c} className="flex items-center gap-1"><input type="checkbox" checked={p.channels.includes(c)} onChange={() => toggleIn("channels", c)} />{c}</label>)}
          <span className="ml-4">Order types:</span>{["pickup", "delivery", "dine_in"].map((c) => <label key={c} className="flex items-center gap-1"><input type="checkbox" checked={p.order_types.includes(c)} onChange={() => toggleIn("order_types", c)} />{c}</label>)}
        </div>
        <div className="flex flex-wrap gap-6 text-sm">
          {[["auto_apply", "Apply automatically"], ["requires_code", "Requires code"], ["combinable", "Combinable with others"], ["requires_manager", "Manager approval at POS"], ["is_enabled", "Enabled"]].map(([k, l]) => (
            <label key={k} className="flex items-center gap-2"><Toggle checked={!!p[k]} onChange={(v) => set(k, v)} />{l}</label>
          ))}
        </div>
        <Field label="Codes">
          <div className="flex flex-wrap items-center gap-2">
            {p.codes.map((c: any) => <Badge key={c.code}>{c.code}{c.max_uses && ` (max ${c.max_uses})`}</Badge>)}
            <Input className="w-40" value={codeInput} onChange={(e) => setCodeInput(e.target.value.toUpperCase())} placeholder="NEWCODE" />
            <Button size="sm" variant="secondary" disabled={!codeInput} onClick={() => { set("codes", [...p.codes, { code: codeInput }]); setCodeInput(""); }}>Add code</Button>
          </div>
        </Field>
        <Field label="Products, categories and modifiers">
          <div className="space-y-1">
            {p.items.map((i: any, k: number) => (
              <div key={k} className="flex items-center gap-2 text-sm"><Badge>{i.role}</Badge>{i.product || i.category || i.modifier || i.modifier_group}{i.size_code && ` (${i.size_code})`}{i.role === "bundle" && ` · slot ${i.slot}`}
                <button className="text-xs text-red-600" onClick={() => set("items", p.items.filter((_: any, j: number) => j !== k))}>remove</button></div>
            ))}
            <div className="flex flex-wrap gap-2">
              <Select className="w-32" value={newItem.role} onChange={(e) => setNewItem({ ...newItem, role: e.target.value })}>
                {["eligible", "excluded", "reward", "bundle", "modifier"].map((r) => <option key={r}>{r}</option>)}
              </Select>
              <Select className="w-36" value={newItem.kind} onChange={(e) => setNewItem({ ...newItem, kind: e.target.value })}>
                <option value="product">product SKU</option><option value="category">category code</option><option value="modifier">modifier code</option><option value="modifier_group">modifier group</option>
              </Select>
              <Input className="w-36" placeholder="e.g. PIZZA / PZ-PEP" value={newItem.ref} onChange={(e) => setNewItem({ ...newItem, ref: e.target.value })} />
              <Input className="w-20" placeholder="size" value={newItem.size_code} onChange={(e) => setNewItem({ ...newItem, size_code: e.target.value })} />
              {newItem.role === "bundle" && <Input className="w-20" type="number" placeholder="slot" value={newItem.slot} onChange={(e) => setNewItem({ ...newItem, slot: Number(e.target.value) })} />}
              <Button size="sm" variant="secondary" disabled={!newItem.ref} onClick={() => { set("items", [...p.items, { role: newItem.role, [newItem.kind]: newItem.ref, size_code: newItem.size_code, slot: newItem.slot }]); setNewItem({ ...newItem, ref: "" }); }}>Add</Button>
            </div>
          </div>
        </Field>
        <Field label={isHq ? "Publish to (none = all stores in the network)" : "Limit to stores (none = all your stores)"}>
          <div className="flex flex-wrap gap-2">{stores.map((st: any) => {
            const on = p.targets.some((t: any) => t.store === st.id);
            return <label key={st.id} className="flex items-center gap-1 rounded border px-2 py-0.5 text-sm"><input type="checkbox" checked={on} onChange={() => set("targets", on ? p.targets.filter((t: any) => t.store !== st.id) : [...p.targets, { store: st.id }])} />{st.number}</label>;
          })}</div>
        </Field>
        <Button onClick={save}>Save promotion</Button>
      </div>
    </Modal>
  );
}
