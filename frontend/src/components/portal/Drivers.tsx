"use client";
import { useCallback, useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import DispatchBoard from "@/components/pos/DispatchBoard";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Select, Table, Toggle } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { money } from "@/lib/format";
import { useStores } from "./useStores";

export default function Drivers() {
  const s = usePortalSession();
  const can = (p: string) => s.user.permissions.includes(p);
  const { stores } = useStores();
  const [store, setStore] = useState<number | null>(null);
  const [drivers, setDrivers] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [zones, setZones] = useState<any[]>([]);
  const [perf, setPerf] = useState<any[]>([]);
  const [editZone, setEditZone] = useState<any>(null);
  const [showBoard, setShowBoard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!store && stores.length) setStore(stores[0].id); }, [stores, store]);
  const load = useCallback(() => {
    if (!store) return;
    portalApi.get(`/drivers/?store=${store}&page_size=200`).then((d) => setDrivers(results(d))).catch((e) => setError(String(e)));
    portalApi.get(`/users/?store=${store}&page_size=200`).then((d) => setUsers(results(d).filter((u: any) => u.role === "delivery")));
    portalApi.get(`/delivery-zones/?store=${store}`).then(setZones);
    portalApi.get(`/reports/drivers/?store=${store}`).then((r) => setPerf(r.drivers));
  }, [store]);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<any>) => {
    setError(null);
    try { await fn(); load(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };
  const withoutProfile = users.filter((u) => !drivers.some((d) => d.user === u.id));
  const current = stores.find((x) => x.id === store);
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium">Store</span>
        <Select className="w-64" value={store ?? ""} onChange={(e) => setStore(Number(e.target.value))}>{stores.map((st) => <option key={st.id} value={st.id}>{st.number} · {st.name}</option>)}</Select>
        {can("dispatch.view") && <Button variant="secondary" onClick={() => setShowBoard(!showBoard)}>{showBoard ? "Hide" : "Show"} live dispatch board</Button>}
      </div>
      <ErrorBanner error={error} />
      {showBoard && store && <div className="flex h-[640px] flex-col overflow-hidden rounded-xl border bg-white"><DispatchBoard storeId={store} ns="portal" canManage={can("dispatch.manage")} /></div>}
      <Card title="Drivers" actions={can("drivers.manage") && withoutProfile.length > 0 && (
        <Select className="w-64" value="" onChange={(e) => e.target.value && act(() => portalApi.post("/drivers/", { user: Number(e.target.value), max_active_deliveries: 3 }))}>
          <option value="">Create profile for driver employee…</option>{withoutProfile.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}</Select>
      )}>
        <Table head={["Driver", "Phone", "Vehicle", "Stores", "Status", "Active", "Max", "Active profile"]} empty={!drivers.length}>
          {drivers.map((d) => (
            <tr key={d.id}>
              <td className="px-3 py-2 font-medium">{d.name}<div className="text-xs text-slate-500">{d.username}</div></td>
              <td className="px-3"><Input className="w-36" defaultValue={d.phone} onBlur={(e) => e.target.value !== d.phone && act(() => portalApi.patch(`/drivers/${d.id}/`, { phone: e.target.value }))} /></td>
              <td className="px-3"><Input className="w-36" defaultValue={d.vehicle} onBlur={(e) => e.target.value !== d.vehicle && act(() => portalApi.patch(`/drivers/${d.id}/`, { vehicle: e.target.value }))} /></td>
              <td className="px-3 text-xs">{d.stores.map((id: number) => stores.find((x) => x.id === id)?.number ?? id).join(", ")}</td>
              <td className="px-3"><Badge tone={d.status === "available" ? "connected" : "pending"}>{d.status.replace("_", " ")}</Badge></td>
              <td className="px-3">{d.active_deliveries}</td>
              <td className="px-3"><Input className="w-16" type="number" defaultValue={d.max_active_deliveries} onBlur={(e) => act(() => portalApi.patch(`/drivers/${d.id}/`, { max_active_deliveries: Number(e.target.value) }))} /></td>
              <td className="px-3"><Toggle checked={d.is_active} disabled={!can("drivers.manage")} onChange={(v) => act(() => portalApi.patch(`/drivers/${d.id}/`, { is_active: v }))} /></td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Driver performance (30 days)">
        <Table head={["Driver", "Assigned", "Delivered", "Failed", "Avg trip", "Avg order→door", "On time"]} empty={!perf.length}>
          {perf.map((p) => <tr key={p.driver_id}><td className="px-3 py-1.5">{p.username}</td><td className="px-3">{p.assigned}</td><td className="px-3">{p.delivered}</td><td className="px-3">{p.failed}</td><td className="px-3">{p.avg_trip_minutes ?? "—"} min</td><td className="px-3">{p.avg_order_to_door_minutes ?? "—"} min</td><td className="px-3">{p.on_time_rate != null ? `${Math.round(p.on_time_rate * 100)}%` : "—"}</td></tr>)}
        </Table>
      </Card>
      <Card title="Delivery zones & fees" actions={can("org.configure_store") && <Button variant="secondary" onClick={() => setEditZone({ store, name: "", min_distance_km: 0, max_distance_km: 5, postal_prefixes: [], fee_cents: 399, min_order_cents: 1500, estimated_minutes: 40, is_active: true, sort_order: zones.length })}>Add zone</Button>}>
        <p className="mb-2 text-sm text-slate-500">
          Without zones, the store&apos;s delivery settings apply ({current ? `${current.delivery_config?.radius_km ?? "?"} km, fee ${money(current.delivery_config?.delivery_fee_cents)}, minimum ${money(current.delivery_config?.minimum_order_cents)}` : ""}).
          Distance bands require address coordinates from the ordering system (no geocoding service is configured); postal prefixes work without coordinates.
        </p>
        <Table head={["Zone", "Distance", "Postal prefixes", "Fee", "Minimum", "ETA", "Active", ""]} empty={!zones.length}>
          {zones.map((z) => (
            <tr key={z.id}><td className="px-3 py-1.5">{z.name}</td><td className="px-3">{z.min_distance_km}–{z.max_distance_km ?? "∞"} km</td><td className="px-3 text-xs">{z.postal_prefixes.join(", ")}</td><td className="px-3">{money(z.fee_cents)}</td><td className="px-3">{money(z.min_order_cents)}</td><td className="px-3">{z.estimated_minutes} min</td><td className="px-3">{z.is_active ? "yes" : "no"}</td>
              <td className="px-3">{can("org.configure_store") && <><Button size="sm" variant="ghost" onClick={() => setEditZone(z)}>Edit</Button><Button size="sm" variant="ghost" onClick={() => act(() => portalApi.del(`/delivery-zones/${z.id}/`))}>Delete</Button></>}</td></tr>
          ))}
        </Table>
      </Card>
      {editZone && (
        <Modal open title={editZone.id ? "Edit zone" : "New zone"} onClose={() => setEditZone(null)}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name"><Input value={editZone.name} onChange={(e) => setEditZone({ ...editZone, name: e.target.value })} /></Field>
            <Field label="Postal prefixes (comma separated)"><Input value={editZone.postal_prefixes.join(", ")} onChange={(e) => setEditZone({ ...editZone, postal_prefixes: e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) })} /></Field>
            {[["min_distance_km", "From km"], ["max_distance_km", "To km"], ["fee_cents", "Fee (cents)"], ["min_order_cents", "Minimum order (cents)"], ["estimated_minutes", "ETA (minutes)"]].map(([k, l]) => (
              <Field key={k} label={l}><Input type="number" value={editZone[k] ?? ""} onChange={(e) => setEditZone({ ...editZone, [k]: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
            ))}
            <label className="flex items-center gap-2 pt-6 text-sm"><Toggle checked={editZone.is_active} onChange={(v) => setEditZone({ ...editZone, is_active: v })} />Active</label>
          </div>
          <Button className="mt-4" onClick={() => act(() => editZone.id ? portalApi.patch(`/delivery-zones/${editZone.id}/`, editZone) : portalApi.post("/delivery-zones/", editZone)).then(() => setEditZone(null))}>Save zone</Button>
        </Modal>
      )}
    </div>
  );
}
