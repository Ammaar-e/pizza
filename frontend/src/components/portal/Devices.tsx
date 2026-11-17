"use client";
import { useCallback, useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Notice, Select, Stat, Table, cx } from "@/components/ui";
import { ApiError, results, wsUrl } from "@/lib/api";
import { dt } from "@/lib/format";
import { useStores } from "./useStores";

const TYPES = [["pos", "POS terminal"], ["register", "Cash register"], ["kds", "Kitchen display"], ["printer", "Receipt printer"], ["manager_workstation", "Manager workstation"], ["gateway", "Store gateway"], ["other", "Other"]];

export default function Devices() {
  const s = usePortalSession();
  const can = (p: string) => s.user.permissions.includes(p);
  const { stores } = useStores();
  const [devices, setDevices] = useState<any[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [profiles, setProfiles] = useState<any[]>([]);
  const [filters, setFilters] = useState({ store: "", device_type: "", status: "", online: "", q: "" });
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState<any>(null);
  const [enroll, setEnroll] = useState<any>(null);
  const [activity, setActivity] = useState<any>(null);
  const [profileEdit, setProfileEdit] = useState<any>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(() => {
    const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]);
    portalApi.get(`/devices/?page_size=200&${q}`).then((d) => setDevices(results(d))).catch((e) => setError(String(e)));
    portalApi.get("/devices/summary/").then(setSummary).catch(() => undefined);
    portalApi.get("/device-alerts/").then(setAlerts).catch(() => undefined);
    portalApi.get("/device-profiles/").then(setProfiles).catch(() => undefined);
  }, [filters]);
  useEffect(load, [load]);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let t: any;
    const connect = () => {
      ws = new WebSocket(wsUrl("/ws/devices/", "portal"));
      ws.onopen = () => setLive(true);
      ws.onmessage = () => load();
      ws.onclose = () => { setLive(false); if (!stop) t = setTimeout(connect, 5000); };
    };
    connect();
    return () => { stop = true; clearTimeout(t); ws?.close(); };
  }, [load]);

  const act = async (fn: () => Promise<any>) => {
    setError(null);
    try { const r = await fn(); load(); return r; } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };

  return (
    <div className="space-y-6">
      <ErrorBanner error={error} />
      {summary && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
          <Stat label="Devices" value={summary.total} />
          <Stat label="Online" value={summary.online} sub={live ? "live" : "reconnecting"} />
          <Stat label="Offline" value={summary.offline} />
          <Stat label="Pending approval" value={summary.pending} />
          <Stat label="Deactivated" value={summary.deactivated} />
          <Stat label="Open alerts" value={summary.open_alerts} />
        </div>
      )}
      {alerts.length > 0 && (
        <Card title="Alerts">
          <Table head={["When", "Store", "Device", "Alert", ""]}>
            {alerts.map((a) => (
              <tr key={a.id}>
                <td className="px-3 py-1.5 text-xs">{dt(a.created_at)}</td><td className="px-3">{a.store_number}</td><td className="px-3">{a.device_name}</td>
                <td className="px-3"><Badge tone={a.severity === "critical" ? "error" : a.severity === "warning" ? "pending" : undefined}>{a.kind.replace("_", " ")}</Badge> <span className="text-xs">{a.message}</span></td>
                <td className="px-3">{!a.acknowledged_at && <Button size="sm" variant="ghost" onClick={() => act(() => portalApi.post(`/device-alerts/${a.id}/acknowledge/`))}>Acknowledge</Button>}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      <Card title="Device registry" actions={can("org.manage_devices") && can("devices.approve") && <Button onClick={() => setEnroll({ store: stores[0]?.id, device_type: "pos", name: "", valid_hours: 24 })}>Issue enrollment code</Button>}>
        <div className="mb-3 flex flex-wrap gap-2">
          <Input className="w-48" placeholder="Search name / id / store" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
          <Select className="w-36" value={filters.store} onChange={(e) => setFilters({ ...filters, store: e.target.value })}><option value="">All stores</option>{stores.map((st) => <option key={st.id} value={st.id}>{st.number}</option>)}</Select>
          <Select className="w-44" value={filters.device_type} onChange={(e) => setFilters({ ...filters, device_type: e.target.value })}><option value="">All types</option>{TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          <Select className="w-40" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}><option value="">Any status</option><option value="active">Active</option><option value="pending">Pending</option><option value="deactivated">Deactivated</option></Select>
          <Select className="w-32" value={filters.online} onChange={(e) => setFilters({ ...filters, online: e.target.value })}><option value="">On/offline</option><option value="1">Online</option><option value="0">Offline</option></Select>
        </div>
        <Table head={["Device", "Franchisee / store", "Type", "Status", "Connectivity", "Version", "Session", "Config", ""]} empty={!devices.length}>
          {devices.map((d) => (
            <tr key={d.id} className={cx(d.open_alerts.length > 0 && "bg-amber-50/50")}>
              <td className="px-3 py-2"><div className="font-medium">{d.name}</div><div className="font-mono text-[10px] text-slate-400">{d.uid}</div></td>
              <td className="px-3 text-xs">{d.franchisee_code} · {d.store_number}</td>
              <td className="px-3 text-xs">{d.device_type}</td>
              <td className="px-3"><Badge tone={d.status === "active" ? "connected" : d.status === "pending" ? "pending" : "disabled"}>{d.status}</Badge></td>
              <td className="px-3 text-xs"><span className={cx("mr-1 inline-block h-2 w-2 rounded-full", d.is_online ? "bg-emerald-500" : "bg-slate-300")} />{d.is_online ? "online" : "offline"}<div className="text-slate-500">hb {dt(d.last_heartbeat_at)}</div>{d.pending_offline_count > 0 && <div className="text-amber-700">{d.pending_offline_count} queued</div>}</td>
              <td className="px-3 text-xs">{d.software_version || "—"} {["outdated", "unapproved"].includes(d.version_status) && <Badge tone="error">{d.version_status}</Badge>}</td>
              <td className="px-3 text-xs">{d.current_user_name || "—"}{d.active_register_session && <div>register: {d.active_register_session.employee}</div>}</td>
              <td className="px-3 text-xs">{d.profile_name || "—"} v{d.config_version}{d.config_stale && <Badge tone="pending">sync pending</Badge>}</td>
              <td className="px-3"><div className="flex flex-wrap gap-1">
                {d.status === "pending" && can("devices.approve") && <><Button size="sm" variant="success" onClick={() => act(() => portalApi.post(`/devices/${d.id}/approve/`))}>Approve</Button>
                  <Button size="sm" variant="danger" onClick={() => act(() => portalApi.post(`/devices/${d.id}/reject/`, { note: prompt("Reason") || "" }))}>Reject</Button></>}
                {can("devices.administer") && d.status === "active" && <>
                  <Button size="sm" variant="ghost" onClick={() => { const n = prompt("New name", d.name); if (n) act(() => portalApi.patch(`/devices/${d.id}/`, { name: n })); }}>Rename</Button>
                  <Select className="w-28 py-1 text-xs" value="" onChange={(e) => e.target.value && act(() => portalApi.post(`/devices/${d.id}/reassign/`, { store: Number(e.target.value) }))}>
                    <option value="">Move to…</option>{stores.filter((st) => st.id !== d.store).map((st) => <option key={st.id} value={st.id}>{st.number}</option>)}</Select>
                  <Button size="sm" variant="ghost" onClick={async () => { const r = await act(() => portalApi.post(`/devices/${d.id}/revoke-credentials/`, { reason: prompt("Reason for revoking") || "revoked" })); if (r) setCode({ ...r, store_number: d.store_number }); }}>Revoke credentials</Button>
                  <Button size="sm" variant="danger" onClick={() => { const r = prompt("Reason for deactivation"); if (r) act(() => portalApi.post(`/devices/${d.id}/deactivate/`, { reason: r })); }}>Deactivate</Button></>}
                {can("devices.administer") && d.status === "deactivated" && <Button size="sm" variant="secondary" onClick={async () => { const r = await act(() => portalApi.post(`/devices/${d.id}/reactivate/`)); if (r) setCode({ ...r, store_number: d.store_number }); }}>Reactivate</Button>}
                {can("devices.configure_profiles") && <Select className="w-32 py-1 text-xs" value={d.config_profile ?? ""} onChange={(e) => act(() => portalApi.patch(`/devices/${d.id}/`, { config_profile: e.target.value ? Number(e.target.value) : null }))}>
                  <option value="">No profile</option>{profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>}
                <Button size="sm" variant="ghost" onClick={() => portalApi.get(`/devices/${d.id}/activity/`).then((a) => setActivity({ device: d, ...a }))}>Activity</Button>
              </div></td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Configuration profiles" actions={can("devices.configure_profiles") && <Button variant="secondary" onClick={() => setProfileEdit({ name: "", description: "", device_types: [], local_overridable: [], approved_versions: [], minimum_version: "", update_policy: "auto", is_default: false, settings: { features: { cash_drawer: true, refunds: true, discounts: true, coupon_entry: true, offline_queue: true, dispatch: true, price_override: false }, heartbeat_interval_seconds: 60, receipt_copies: 1, idle_lock_minutes: 10 } })}>New profile</Button>}>
        <Table head={["Profile", "Applies to", "Features", "Approved versions", "Update policy", "Version", "Devices", ""]} empty={!profiles.length}>
          {profiles.map((p) => (
            <tr key={p.id}>
              <td className="px-3 py-2 font-medium">{p.name}{p.is_default && <Badge>default</Badge>}</td>
              <td className="px-3 text-xs">{p.device_types.join(", ") || "all"}</td>
              <td className="px-3 text-xs">{Object.entries(p.settings.features || {}).filter(([, v]) => !v).map(([k]) => `no ${k.replace("_", " ")}`).join(", ") || "all enabled"}</td>
              <td className="px-3 text-xs">{p.approved_versions.join(", ")}{p.minimum_version && ` (min ${p.minimum_version})`}</td>
              <td className="px-3 text-xs">{p.update_policy}</td><td className="px-3">v{p.version}</td><td className="px-3">{p.device_count}</td>
              <td className="px-3">{can("devices.configure_profiles") && <Button size="sm" variant="ghost" onClick={() => setProfileEdit(p)}>Edit</Button>}</td>
            </tr>
          ))}
        </Table>
      </Card>
      {enroll && (
        <Modal open title="Issue a one-time enrollment code" onClose={() => setEnroll(null)}>
          <div className="space-y-3">
            <Field label="Store"><Select value={enroll.store} onChange={(e) => setEnroll({ ...enroll, store: Number(e.target.value) })}>{stores.map((st) => <option key={st.id} value={st.id}>{st.number} · {st.name}</option>)}</Select></Field>
            <Field label="Device type"><Select value={enroll.device_type} onChange={(e) => setEnroll({ ...enroll, device_type: e.target.value })}>{TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
            <Field label="Device name"><Input value={enroll.name} onChange={(e) => setEnroll({ ...enroll, name: e.target.value })} /></Field>
            <Field label="Valid for (hours)"><Input type="number" value={enroll.valid_hours} onChange={(e) => setEnroll({ ...enroll, valid_hours: Number(e.target.value) })} /></Field>
            <Button disabled={!enroll.name} onClick={async () => { const r = await act(() => portalApi.post("/device-registrations/", enroll)); if (r) { setCode(r); setEnroll(null); } }}>Issue code</Button>
          </div>
        </Modal>
      )}
      {code && (
        <Modal open title="Enrollment code" onClose={() => setCode(null)}>
          <Notice tone="warn">Single use; shown only once. Enter it on the device with the store number ({code.store_number}). Expires {dt(code.expires_at)}.</Notice>
          <div className="mt-4 text-center font-mono text-3xl font-bold tracking-widest">{code.enrollment_code}</div>
          {code.revoked !== undefined && <p className="mt-2 text-sm text-slate-500">{code.revoked} credential(s) revoked. The device cannot connect until re-enrolled with this code.</p>}
        </Modal>
      )}
      {activity && (
        <Modal open wide title={`Activity — ${activity.device.name}`} onClose={() => setActivity(null)}>
          <h4 className="mb-1 font-semibold">Audit history</h4>
          <Table head={["When", "Action", "By", "Details"]} empty={!activity.audit.length}>
            {activity.audit.map((a: any, i: number) => <tr key={i}><td className="px-3 py-1 text-xs">{dt(a.at)}</td><td className="px-3 font-mono text-xs">{a.action}</td><td className="px-3 text-xs">{a.actor}</td><td className="max-w-xs truncate px-3 text-xs" title={JSON.stringify(a.data)}>{JSON.stringify(a.data)}</td></tr>)}
          </Table>
          <h4 className="mb-1 mt-4 font-semibold">Credentials</h4>
          <Table head={["Key", "Issued", "Expires", "Last used", "Revoked"]}>
            {activity.credentials.map((c: any) => <tr key={c.key_id}><td className="px-3 py-1 font-mono text-xs">{c.key_id}</td><td className="px-3 text-xs">{dt(c.issued_at)}</td><td className="px-3 text-xs">{dt(c.expires_at)}</td><td className="px-3 text-xs">{dt(c.last_used_at)}</td><td className="px-3 text-xs">{c.revoked_at ? `${dt(c.revoked_at)} (${c.revoked_reason})` : "—"}</td></tr>)}
          </Table>
          <h4 className="mb-1 mt-4 font-semibold">Recent heartbeats</h4>
          <Table head={["When", "IP", "Version", "App", "Sync", "Queued", "Config"]} empty={!activity.heartbeats.length}>
            {activity.heartbeats.slice(0, 20).map((h: any, i: number) => <tr key={i}><td className="px-3 py-1 text-xs">{dt(h.at)}</td><td className="px-3 text-xs">{h.ip}</td><td className="px-3 text-xs">{h.version}</td><td className="px-3 text-xs">{h.app_status}</td><td className="px-3 text-xs">{h.sync_status}</td><td className="px-3 text-xs">{h.pending}</td><td className="px-3 text-xs">v{h.config_version}</td></tr>)}
          </Table>
          <h4 className="mb-1 mt-4 font-semibold">Effective configuration (v{activity.effective_config.config_version})</h4>
          <pre className="rounded bg-slate-50 p-2 text-xs">{JSON.stringify(activity.effective_config.settings, null, 2)}</pre>
        </Modal>
      )}
      {profileEdit && <ProfileEditor profile={profileEdit} onClose={() => { setProfileEdit(null); load(); }} />}
    </div>
  );
}

function ProfileEditor({ profile, onClose }: { profile: any; onClose: () => void }) {
  const [p, setP] = useState(profile);
  const [json, setJson] = useState(JSON.stringify(profile.settings, null, 2));
  const [error, setError] = useState<string | null>(null);
  const list = (k: string) => (e: any) => setP({ ...p, [k]: e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean) });
  async function save() {
    try {
      const body = { ...p, settings: JSON.parse(json) };
      if (p.id) await portalApi.patch(`/device-profiles/${p.id}/`, body);
      else await portalApi.post("/device-profiles/", body);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }
  return (
    <Modal open wide title={p.id ? `Edit profile ${p.name} (saving creates v${p.version + 1})` : "New configuration profile"} onClose={onClose}>
      <div className="space-y-3">
        <ErrorBanner error={error} />
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name"><Input value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></Field>
          <Field label="Description"><Input value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} /></Field>
          <Field label="Device types (comma separated, empty = all)"><Input value={p.device_types.join(", ")} onChange={list("device_types")} /></Field>
          <Field label="Locally overridable settings"><Input value={p.local_overridable.join(", ")} onChange={list("local_overridable")} /></Field>
          <Field label="Approved versions"><Input value={p.approved_versions.join(", ")} onChange={list("approved_versions")} /></Field>
          <Field label="Minimum version"><Input value={p.minimum_version} onChange={(e) => setP({ ...p, minimum_version: e.target.value })} /></Field>
          <Field label="Update policy"><Select value={p.update_policy} onChange={(e) => setP({ ...p, update_policy: e.target.value })}><option value="auto">Auto-update</option><option value="manual">Manual</option><option value="pinned">Pinned</option></Select></Field>
          <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={p.is_default} onChange={(e) => setP({ ...p, is_default: e.target.checked })} />Default for new devices of these types</label>
        </div>
        <Field label="Settings (features, heartbeat interval, receipts…)"><textarea className="h-56 w-full rounded-lg border p-2 font-mono text-xs" value={json} onChange={(e) => setJson(e.target.value)} /></Field>
        <Button onClick={save}>Save profile</Button>
      </div>
    </Modal>
  );
}
