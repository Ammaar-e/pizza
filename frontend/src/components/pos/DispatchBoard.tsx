"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, ErrorBanner, Modal, Notice, Select, cx } from "@/components/ui";
import { ApiError, Namespace, api, wsUrl } from "@/lib/api";
import { SOURCE_LABEL, dt, money, time } from "@/lib/format";

const COLUMNS: { key: string; label: string; statuses: string[] }[] = [
  { key: "waiting", label: "Awaiting / preparing", statuses: ["awaiting_preparation", "preparing"] },
  { key: "ready", label: "Ready for dispatch", statuses: ["ready_for_dispatch"] },
  { key: "assigned", label: "Assigned", statuses: ["assigned"] },
  { key: "road", label: "Out for delivery", statuses: ["picked_up", "out_for_delivery"] },
  { key: "problem", label: "Failed", statuses: ["failed"] },
  { key: "done", label: "Completed / closed", statuses: ["delivered", "returned", "cancelled"] },
];

const DRIVER_TONE: Record<string, string> = {
  available: "bg-emerald-100 text-emerald-800", unavailable: "bg-red-100 text-red-700", on_break: "bg-amber-100 text-amber-800",
  off_shift: "bg-zinc-200 text-zinc-700",
};

function age(iso: string, now: number) {
  const m = Math.floor((now - new Date(iso).getTime()) / 60000);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${m % 60}m`;
}

export default function DispatchBoard({ storeId, ns = "pos", canManage }: { storeId: number; ns?: Namespace; canManage: boolean }) {
  const client = useMemo(() => api(ns), [ns]);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState({ driver: "", source: "", hours: "12" });
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<any>(null);
  const [now, setNow] = useState(Date.now());
  const [live, setLive] = useState(false);

  const load = useCallback(async () => {
    const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]);
    try {
      setData(await client.get(`/stores/${storeId}/dispatch/?${q}`));
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }, [client, storeId, filters]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    let ws: WebSocket | null = null;
    let stop = false;
    let timer: any;
    const connect = () => {
      ws = new WebSocket(wsUrl(`/ws/stores/${storeId}/dispatch/`, ns));
      ws.onopen = () => setLive(true);
      ws.onmessage = () => load();
      ws.onclose = () => { setLive(false); if (!stop) timer = setTimeout(connect, 4000); };
    };
    connect();
    const poll = setInterval(load, 30000);
    return () => { stop = true; clearInterval(t); clearInterval(poll); clearTimeout(timer); ws?.close(); };
  }, [storeId, ns, load]);

  async function act(fn: () => Promise<any>) {
    setError(null);
    try { await fn(); await load(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  }

  if (!data) return <div className="p-6 text-slate-500">Loading dispatch…</div>;
  const drivers: any[] = data.drivers;
  const available = drivers.filter((d) => d.status === "available");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b bg-white px-4 py-2 text-sm">
        <span className={cx("rounded-full px-2 py-0.5 text-xs font-semibold", live ? "bg-emerald-100 text-emerald-800" : "bg-amber-100")}>{live ? "LIVE" : "reconnecting"}</span>
        <Select className="w-44" value={filters.driver} onChange={(e) => setFilters({ ...filters, driver: e.target.value })}>
          <option value="">All drivers</option>
          {drivers.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
        <Select className="w-44" value={filters.source} onChange={(e) => setFilters({ ...filters, source: e.target.value })}>
          <option value="">All sources</option>
          {["pos", "call_centre", "external_api", "android", "ios"].map((s) => <option key={s} value={s}>{SOURCE_LABEL[s]}</option>)}
        </Select>
        <Select className="w-36" value={filters.hours} onChange={(e) => setFilters({ ...filters, hours: e.target.value })}>
          <option value="4">Last 4 h</option><option value="12">Last 12 h</option><option value="24">Last 24 h</option>
        </Select>
        <span className="text-xs text-slate-500">Auto-assign: {data.store.auto_assign ? "on" : "off"} · proof: {data.store.proof_of_delivery}</span>
        {canManage && selected.length > 0 && (
          <span className="ml-auto flex items-center gap-2">
            {selected.length} selected → assign to
            <Select className="w-40" defaultValue="" onChange={(e) => e.target.value && act(async () => {
              const r = await client.post(`/stores/${storeId}/dispatch/assign-many/`, { driver: Number(e.target.value), deliveries: selected });
              const failed = r.results.filter((x: any) => !x.ok);
              if (failed.length) setError(failed.map((f: any) => `#${f.id}: ${f.error}`).join("; "));
              setSelected([]);
            })}>
              <option value="">driver…</option>
              {available.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.active_deliveries}/{d.max_active_deliveries})</option>)}
            </Select>
          </span>
        )}
      </div>
      {error && <div className="px-4 pt-2"><ErrorBanner error={error} /></div>}
      <div className="flex min-h-0 flex-1">
        <section className="grid flex-1 auto-cols-[minmax(240px,1fr)] grid-flow-col gap-3 overflow-x-auto p-3">
          {COLUMNS.map((col) => {
            const items = data.deliveries.filter((d: any) => col.statuses.includes(d.status));
            return (
              <div key={col.key} className="flex min-h-0 flex-col rounded-xl bg-slate-200/60">
                <div className="flex justify-between px-3 py-2 text-sm font-semibold"><span>{col.label}</span><span>{items.length}</span></div>
                <div className="flex-1 space-y-2 overflow-y-auto px-2 pb-2">
                  {items.map((d: any) => {
                    const late = d.estimated_delivery_at && new Date(d.estimated_delivery_at).getTime() < now && !["delivered", "returned", "cancelled"].includes(d.status);
                    return (
                      <div key={d.id} className={cx("rounded-lg bg-white p-3 text-sm shadow-sm", late && "ring-2 ring-red-400", d.status === "failed" && "ring-2 ring-amber-400")}>
                        <div className="flex items-center justify-between">
                          <label className="flex items-center gap-2 font-bold">
                            {canManage && ["awaiting_preparation", "preparing", "ready_for_dispatch"].includes(d.status) &&
                              <input type="checkbox" checked={selected.includes(d.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, d.id] : selected.filter((x) => x !== d.id))} />}
                            #{d.order_number}
                          </label>
                          <span className="text-xs text-slate-500">{age(d.placed_at, now)}</span>
                        </div>
                        <div className="truncate text-xs">{d.address?.line1}{d.zone && ` · ${d.zone}`}{d.distance_km != null && ` · ${d.distance_km} km`}</div>
                        <div className="text-xs text-slate-500">{d.customer?.name} · {SOURCE_LABEL[d.source] ?? d.source} · {money(d.total_cents)} {d.payment_status === "unpaid" && <b className="text-amber-700">collect</b>}</div>
                        {d.estimated_delivery_at && <div className={cx("text-xs", late ? "font-semibold text-red-700" : "text-slate-500")}>ETA {time(d.estimated_delivery_at)}</div>}
                        {d.driver && <div className="mt-1 text-xs">Driver: <b>{d.driver.username}</b></div>}
                        {d.failure_reason && <div className="mt-1 text-xs text-amber-800">⚠ {d.failure_reason}</div>}
                        {canManage && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {["awaiting_preparation", "preparing", "ready_for_dispatch", "assigned"].includes(d.status) && (
                              <select className="rounded border px-1 py-0.5 text-xs" value="" onChange={(e) => e.target.value && act(() => client.post(`/deliveries/${d.id}/assign/`, { driver: Number(e.target.value), version: d.version }))}>
                                <option value="">{d.driver ? "Reassign…" : "Assign…"}</option>
                                {available.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.active_deliveries}/{x.max_active_deliveries})</option>)}
                              </select>
                            )}
                            {d.status === "ready_for_dispatch" && <button className="rounded bg-slate-100 px-2 text-xs" onClick={() => act(() => client.post(`/deliveries/${d.id}/auto-assign/`))}>Auto</button>}
                            {d.status === "assigned" && <button className="rounded bg-slate-100 px-2 text-xs" onClick={() => act(() => client.post(`/deliveries/${d.id}/unassign/`))}>Unassign</button>}
                            {d.status === "failed" && <>
                              <button className="rounded bg-slate-100 px-2 text-xs" onClick={() => act(() => client.post(`/deliveries/${d.id}/status/`, { status: "ready_for_dispatch" }))}>Re-dispatch</button>
                              <button className="rounded bg-red-100 px-2 text-xs" onClick={() => act(() => client.post(`/deliveries/${d.id}/status/`, { status: "returned" }))}>Returned</button>
                            </>}
                            <button className="rounded px-2 text-xs text-brand-600 underline" onClick={() => client.get(`/deliveries/${d.id}/`).then(setDetail)}>History</button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </section>
        <aside className="w-72 shrink-0 overflow-y-auto border-l bg-white p-3">
          <h3 className="mb-2 font-semibold">Drivers</h3>
          {drivers.length === 0 && <Notice>No driver profiles for this store.</Notice>}
          {drivers.map((d) => (
            <div key={d.id} className="mb-2 rounded-lg border p-2 text-sm">
              <div className="flex items-center justify-between"><b>{d.name}</b><span className={cx("rounded-full px-2 text-xs", DRIVER_TONE[d.status])}>{d.status.replace("_", " ")}</span></div>
              <div className="text-xs text-slate-500">{d.active_deliveries}/{d.max_active_deliveries} active · {d.phone}</div>
              {canManage && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {["available", "unavailable", "on_break", "off_shift"].filter((s) => s !== d.status).map((s) => (
                    <button key={s} className="rounded bg-slate-100 px-2 py-0.5 text-xs" onClick={() => act(() => client.post(`/drivers/${d.id}/availability/`, { status: s, store: storeId }))}>{s.replace("_", " ")}</button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </aside>
      </div>
      {detail && (
        <Modal open title={`Delivery #${detail.order_number}`} onClose={() => setDetail(null)}>
          <ul className="space-y-1 text-sm">
            {detail.history.map((h: any, i: number) => (
              <li key={i}>{dt(h.at)} · <b>{h.from || "—"} → {h.to}</b> · {h.actor}{h.driver && ` · driver ${h.driver}`}{h.previous_driver && ` (was ${h.previous_driver})`}{h.note && ` — ${h.note}`}</li>
            ))}
          </ul>
          {detail.proofs.length > 0 && <div className="mt-3 space-y-2">{detail.proofs.map((p: any, i: number) => (
            <div key={i} className="text-sm">Proof: {p.kind} {p.recipient_name && `· received by ${p.recipient_name}`}{p.image_url && <img src={p.image_url} alt="Proof of delivery" className="mt-1 max-h-48 rounded" />}</div>
          ))}</div>}
          <Badge>{detail.status}</Badge>
        </Modal>
      )}
    </div>
  );
}
