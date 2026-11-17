"use client";
import { useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Notice, Table, Toggle } from "@/components/ui";
import { ApiError, Store } from "@/lib/api";
import { DAYS, money } from "@/lib/format";
import { useStores } from "./useStores";

export default function StoreSettings() {
  const { stores, reload } = useStores();
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    if (!selected && stores.length) setSelected(stores[0].id);
  }, [stores, selected]);
  const store = stores.find((s) => s.id === selected);
  return (
    <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
      <Card title="Stores">
        <ul className="space-y-1">
          {stores.map((s) => (
            <li key={s.id}>
              <button onClick={() => setSelected(s.id)} className={`w-full rounded-lg px-3 py-2 text-left text-sm ${s.id === selected ? "bg-brand-50 font-semibold text-brand-700" : "hover:bg-slate-50"}`}>
                <div>{s.number} · {s.name}</div>
                <div className="text-xs text-slate-500">{s.is_open_now ? "Open" : "Closed"} · {s.city}</div>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {store && <StoreEditor key={store.id} store={store} onSaved={reload} />}
    </div>
  );
}

function StoreEditor({ store, onSaved }: { store: Store; onSaved: () => void }) {
  const [s, setS] = useState<Store>(store);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (k: string, v: any) => setS({ ...s, [k]: v });

  async function save() {
    setError(null);
    setMsg(null);
    try {
      const body = {
        name: s.name, phone: s.phone, address_line1: s.address_line1, city: s.city, region: s.region, postal_code: s.postal_code,
        timezone: s.timezone, is_temporarily_closed: s.is_temporarily_closed, temporary_closure_reason: s.temporary_closure_reason,
        delivery_config: s.delivery_config, pickup_config: s.pickup_config, ordering_channels: s.ordering_channels,
        payment_config: s.payment_config, hours: s.hours, receipt_header: s.receipt_header, receipt_footer: s.receipt_footer,
      };
      await portalApi.patch(`/stores/${s.id}/`, body);
      setMsg("Saved");
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }

  return (
    <div className="space-y-6">
      <ErrorBanner error={error} />
      {msg && <Notice tone="ok">{msg}</Notice>}
      <Card title={`Store ${s.number}`} actions={<Button onClick={save}>Save changes</Button>}>
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="Name"><Input value={s.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Telephone"><Input value={s.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
          <Field label="Time zone"><Input value={s.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
          <Field label="Address"><Input value={s.address_line1} onChange={(e) => set("address_line1", e.target.value)} /></Field>
          <Field label="City"><Input value={s.city} onChange={(e) => set("city", e.target.value)} /></Field>
          <Field label="Province / postal code">
            <div className="flex gap-2"><Input value={s.region} onChange={(e) => set("region", e.target.value)} /><Input value={s.postal_code} onChange={(e) => set("postal_code", e.target.value)} /></div>
          </Field>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Toggle checked={s.is_temporarily_closed} onChange={(v) => set("is_temporarily_closed", v)} label="Temporarily closed" />
          <span className="text-sm">Temporarily closed (third-party orders are auto-rejected)</span>
          {s.is_temporarily_closed && <Input className="max-w-xs" placeholder="Reason" value={s.temporary_closure_reason || ""} onChange={(e) => set("temporary_closure_reason", e.target.value)} />}
        </div>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Operating hours">
          <div className="space-y-2">
            {DAYS.map((d, i) => {
              const h = s.hours.find((x) => x.day_of_week === i);
              return (
                <div key={d} className="flex items-center gap-2 text-sm">
                  <span className="w-10 font-medium">{d}</span>
                  <Toggle checked={!!h} onChange={(on) => set("hours", on ? [...s.hours, { day_of_week: i, opens_at: "11:00", closes_at: "23:00" }] : s.hours.filter((x) => x.day_of_week !== i))} />
                  {h ? (
                    <>
                      <Input type="time" className="w-28" value={h.opens_at.slice(0, 5)} onChange={(e) => set("hours", s.hours.map((x) => (x === h ? { ...x, opens_at: e.target.value } : x)))} />
                      <span>–</span>
                      <Input type="time" className="w-28" value={h.closes_at.slice(0, 5)} onChange={(e) => set("hours", s.hours.map((x) => (x === h ? { ...x, closes_at: e.target.value } : x)))} />
                    </>
                  ) : <span className="text-slate-400">Closed</span>}
                </div>
              );
            })}
          </div>
        </Card>
        <Card title="Ordering channels">
          <div className="space-y-2">
            {Object.entries({ pos: "POS", phone: "Telephone", call_centre: "Call centre", external_api: "Website / external API", android: "Android app", ios: "iOS app" }).map(([k, label]) => (
              <div key={k} className="flex items-center justify-between text-sm">
                <span>{label}</span>
                <Toggle checked={!!s.ordering_channels?.[k]} onChange={(v) => set("ordering_channels", { ...s.ordering_channels, [k]: v })} />
              </div>
            ))}
          </div>
        </Card>
        <Card title="Delivery">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 flex items-center gap-2 text-sm"><Toggle checked={!!s.delivery_config?.enabled} onChange={(v) => set("delivery_config", { ...s.delivery_config, enabled: v })} /> Store delivery enabled</div>
            {[["radius_km", "Radius (km)"], ["minimum_order_cents", "Minimum order (¢)"], ["delivery_fee_cents", "Delivery fee (¢)"], ["estimated_minutes", "Estimate (min)"]].map(([k, l]) => (
              <Field key={k} label={l}><Input type="number" value={s.delivery_config?.[k] ?? ""} onChange={(e) => set("delivery_config", { ...s.delivery_config, [k]: Number(e.target.value) })} /></Field>
            ))}
          </div>
        </Card>
        <Card title="Pickup & payments">
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm"><Toggle checked={s.pickup_config?.enabled !== false} onChange={(v) => set("pickup_config", { ...s.pickup_config, enabled: v })} /> Pickup enabled</div>
            <Field label="Pickup estimate (min)"><Input type="number" value={s.pickup_config?.estimated_minutes ?? 15} onChange={(e) => set("pickup_config", { ...s.pickup_config, estimated_minutes: Number(e.target.value) })} /></Field>
            <Field label="Card processor" hint="Card-present payments via Stripe Terminal. Secret keys are stored encrypted and never displayed.">
              <div className="flex gap-2">
                <Input placeholder="Stripe reader id (tmr_…)" value={s.payment_config?.stripe_reader_id || ""} onChange={(e) => set("payment_config", { ...s.payment_config, card_processor: "stripe", stripe_reader_id: e.target.value })} />
              </div>
            </Field>
            <PaymentSecret store={s} />
          </div>
        </Card>
      </div>
      <MenuConfig store={s} />
      <Devices store={s} />
    </div>
  );
}

function PaymentSecret({ store }: { store: Store }) {
  const [val, setVal] = useState("");
  const [state, setState] = useState<string[]>(store.payment_secrets_configured || []);
  return (
    <div className="flex items-end gap-2">
      <Field label={`Stripe secret key ${state.includes("stripe_secret_key") ? "(configured)" : "(not set)"}`}>
        <Input type="password" autoComplete="off" value={val} onChange={(e) => setVal(e.target.value)} placeholder="sk_live_…" />
      </Field>
      <Button variant="secondary" disabled={!val} onClick={async () => {
        const r = await portalApi.post(`/stores/${store.id}/payment-secrets/`, { stripe_secret_key: val });
        setState(r.configured);
        setVal("");
      }}>Save</Button>
    </div>
  );
}

function MenuConfig({ store }: { store: Store }) {
  const [data, setData] = useState<any>(null);
  const [dirty, setDirty] = useState<Record<number, any>>({});
  const [error, setError] = useState<string | null>(null);
  const load = () => portalApi.get(`/stores/${store.id}/menu-config/`).then(setData);
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.id]);
  if (!data) return null;
  const edit = (pid: number, patch: any) => setDirty({ ...dirty, [pid]: { ...(dirty[pid] || {}), product: pid, ...patch } });

  return (
    <Card title="Menu & permitted pricing" actions={
      <>
        <Button variant="secondary" onClick={() => portalApi.post(`/stores/${store.id}/menu-versions/`, { note: "Published from portal" }).then((r) => alert(`Published menu version ${r.version}`))}>Publish version</Button>
        <Button disabled={!Object.keys(dirty).length} onClick={async () => {
          setError(null);
          try {
            await portalApi.patch(`/stores/${store.id}/menu-config/`, { products: Object.values(dirty) });
            setDirty({});
            load();
          } catch (e) {
            setError(e instanceof ApiError ? e.describe() : String(e));
          }
        }}>Save menu</Button>
      </>
    }>
      <ErrorBanner error={error} />
      <p className="mb-3 text-sm text-slate-500">Only headquarters-approved products are listed. Prices must stay within the HQ-permitted range.</p>
      <Table head={["On menu", "Available", "Product", "Category", "Price", "HQ range"]}>
        {data.products.map((p: any) => {
          const d = { ...p, ...(dirty[p.product] || {}) };
          return (
            <tr key={p.product}>
              <td className="px-3 py-2"><Toggle checked={d.enabled} onChange={(v) => edit(p.product, { enabled: v })} /></td>
              <td className="px-3"><Toggle checked={d.is_available} onChange={(v) => edit(p.product, { is_available: v })} /></td>
              <td className="px-3"><div className="font-medium">{p.name}</div><div className="text-xs text-slate-500">{p.sku}</div></td>
              <td className="px-3">{p.category}</td>
              <td className="px-3">
                {p.default_sizes.length ? (
                  <div className="flex flex-wrap gap-1">
                    {p.default_sizes.map((sz: any) => (
                      <label key={sz.code} className="text-xs">{sz.code}
                        <input className="ml-1 w-20 rounded border px-1 py-0.5" type="number" placeholder={String(sz.price_cents)} value={d.size_prices?.[sz.code] ?? ""}
                          onChange={(e) => {
                            const sp = { ...(d.size_prices || {}) };
                            if (e.target.value === "") delete sp[sz.code]; else sp[sz.code] = Number(e.target.value);
                            edit(p.product, { size_prices: sp });
                          }} />
                      </label>
                    ))}
                  </div>
                ) : (
                  <input className="w-24 rounded border px-1 py-0.5 text-sm" type="number" placeholder="HQ price" value={d.price_cents ?? ""} onChange={(e) => edit(p.product, { price_cents: e.target.value === "" ? null : Number(e.target.value) })} />
                )}
              </td>
              <td className="px-3 text-xs text-slate-500">{money(p.min_price_cents)} – {money(p.max_price_cents)}</td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}

function Devices({ store }: { store: Store }) {
  const [devices, setDevices] = useState<any[]>([]);
  const [stations, setStations] = useState<any[]>([]);
  const load = () => {
    portalApi.get(`/devices/?store=${store.id}`).then((d) => setDevices(d.results ?? d));
    portalApi.get(`/kitchen/stations/?store=${store.id}`).then(setStations);
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.id]);
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <Card title="POS & KDS devices">
        <p className="mb-2 text-sm text-slate-500">Devices are registered from the POS activation screen by a manager. Revoke lost or retired devices here.</p>
        <Table head={["Name", "Type", "Last seen", ""]} empty={!devices.length}>
          {devices.map((d) => (
            <tr key={d.id}>
              <td className="px-3 py-2">{d.name}</td><td className="px-3">{d.device_type}</td>
              <td className="px-3 text-xs">{d.last_seen_at ? new Date(d.last_seen_at).toLocaleString() : "never"}</td>
              <td className="px-3">{d.is_active ? <Button size="sm" variant="danger" onClick={() => portalApi.del(`/devices/${d.id}/`).then(load)}>Revoke</Button> : <Badge tone="disabled">revoked</Badge>}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Kitchen stations">
        <Table head={["Code", "Name", "Routes", "Expo"]} empty={!stations.length}>
          {stations.map((s) => (
            <tr key={s.id}><td className="px-3 py-2">{s.code}</td><td className="px-3">{s.name}</td><td className="px-3 text-xs">{s.prep_categories.join(", ") || "all items"}</td><td className="px-3">{s.is_expo ? "yes" : ""}</td></tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
