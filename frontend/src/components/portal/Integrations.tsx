"use client";
import { useCallback, useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Notice, Select, Table, Toggle } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { dt } from "@/lib/format";
import { useStores } from "./useStores";

const ACCOUNT_FIELDS: Record<string, { key: string; label: string; secret?: boolean }[]> = {
  ubereats: [
    { key: "client_id", label: "Client ID" },
    { key: "client_secret", label: "Client secret (also verifies webhook signatures)", secret: true },
    { key: "scopes", label: "Scopes (optional)" },
  ],
  doordash: [
    { key: "developer_id", label: "Developer ID" },
    { key: "key_id", label: "Key ID" },
    { key: "signing_secret", label: "Signing secret", secret: true },
    { key: "webhook_auth_token", label: "Webhook auth token", secret: true },
    { key: "provider_type", label: "Provider type" },
  ],
  skip: [],
};

const STORE_ID_LABEL: Record<string, string> = {
  ubereats: "Uber Eats store ID (UUID)",
  doordash: "DoorDash merchant_supplied_id (defaults to store number)",
  skip: "Skip restaurant ID",
};

export default function Integrations() {
  const { stores } = useStores();
  const [storeId, setStoreId] = useState<number | null>(null);
  const [rows, setRows] = useState<any[]>([]);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [accountModal, setAccountModal] = useState<string | null>(null);

  useEffect(() => {
    if (!storeId && stores.length) setStoreId(stores[0].id);
  }, [stores, storeId]);
  const load = useCallback(() => {
    if (!storeId) return;
    portalApi.get(`/stores/${storeId}/integrations/`).then(setRows).catch((e) => setError(String(e)));
    portalApi.get("/provider-accounts/?page_size=100").then((d) => setAccounts(results(d)));
  }, [storeId]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Store</span>
        <Select className="w-64" value={storeId ?? ""} onChange={(e) => setStoreId(Number(e.target.value))}>
          {stores.map((s) => <option key={s.id} value={s.id}>{s.number} · {s.name}</option>)}
        </Select>
      </div>
      <ErrorBanner error={error} />
      <div className="grid gap-6 xl:grid-cols-3">
        {rows.map((r) => (
          <ProviderPanel key={r.provider} storeId={storeId!} row={r} accounts={accounts.filter((a) => a.provider === r.provider && a.is_active)}
            onChange={load} onNewAccount={() => setAccountModal(r.provider)} />
        ))}
      </div>
      {accountModal && <AccountModal provider={accountModal} onClose={() => { setAccountModal(null); load(); }} />}
    </div>
  );
}

function ProviderPanel({ storeId, row, accounts, onChange, onNewAccount }: { storeId: number; row: any; accounts: any[]; onChange: () => void; onNewAccount: () => void }) {
  const [externalId, setExternalId] = useState(row.external_store_id || "");
  const [policy, setPolicy] = useState(row.settings?.acceptance_policy || {});
  const [settings, setSettings] = useState(row.settings || {});
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [activity, setActivity] = useState<any>(null);

  async function put(body: any, label = "Saved") {
    setBusy("save");
    setMsg(null);
    try {
      await portalApi.put(`/stores/${storeId}/integrations/${row.provider}/`, body);
      setMsg({ tone: "ok", text: label });
      onChange();
    } catch (e) {
      setMsg({ tone: "warn", text: e instanceof ApiError ? e.describe() : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function op(name: "test" | "menu-sync") {
    setBusy(name);
    setMsg(null);
    try {
      const r = await portalApi.post(`/stores/${storeId}/integrations/${row.provider}/${name}/`);
      setMsg({ tone: r.ok === false || r.status === "failed" ? "warn" : "ok", text: r.message || `Menu sync: ${r.status}${r.detail ? ` — ${r.detail}` : ""}` });
      onChange();
    } catch (e) {
      setMsg({ tone: "warn", text: e instanceof ApiError ? e.describe() : String(e) });
    } finally {
      setBusy(null);
    }
  }

  const blocked = !row.network_enabled;
  return (
    <Card title={<span className="flex items-center gap-2">{row.provider_name} <Badge tone={row.connection_status}>{row.connection_status.replace("_", " ")}</Badge></span>}
      actions={<Toggle checked={row.enabled} disabled={blocked || busy !== null} onChange={(v) => put({ enabled: v }, v ? "Enabled" : "Disabled")} label="Enabled" />}>
      <div className="space-y-4 text-sm">
        {blocked && <Notice tone="warn">Headquarters has not permitted {row.provider_name} for the network.</Notice>}
        {!row.api_available && <Notice tone="warn"><b>Access required.</b> {row.access_requirements}</Notice>}
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <dt className="text-slate-500">Last successful communication</dt><dd>{dt(row.last_success_at)}</dd>
          <dt className="text-slate-500">Last error</dt><dd>{dt(row.last_error_at)}</dd>
          <dt className="text-slate-500">Menu sync</dt><dd>{row.menu_sync_status}{row.menu_synced_at ? ` · ${dt(row.menu_synced_at)}` : ""}</dd>
          <dt className="text-slate-500">Capabilities</dt>
          <dd>{Object.entries(row.supports || {}).filter(([, v]) => v).map(([k]) => k.replace("_", " ")).join(", ") || "none"}</dd>
        </dl>
        {row.last_error && <div className="rounded bg-red-50 p-2 text-xs text-red-800">{row.last_error}</div>}

        <Field label="Credentials (provider account)" hint="Secrets are encrypted at rest and never shown again.">
          <div className="flex gap-2">
            <Select value={row.account ?? ""} onChange={(e) => put({ account: e.target.value ? Number(e.target.value) : null }, "Account linked")} disabled={!row.api_available}>
              <option value="">— none —</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.environment}{a.franchisee ? "" : ", HQ"})</option>)}
            </Select>
            <Button variant="secondary" size="sm" onClick={onNewAccount} disabled={!row.api_available}>New</Button>
          </div>
        </Field>
        <Field label={STORE_ID_LABEL[row.provider] || "Store / merchant identifier"}>
          <div className="flex gap-2">
            <Input value={externalId} onChange={(e) => setExternalId(e.target.value)} />
            <Button variant="secondary" size="sm" onClick={() => put({ external_store_id: externalId })}>Save</Button>
          </div>
        </Field>
        {row.webhook_url && (
          <Field label="Webhook URL (configure in the provider's developer portal)">
            <Input readOnly value={row.webhook_url} onFocus={(e) => e.target.select()} />
          </Field>
        )}

        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer font-medium">Order acceptance settings</summary>
          <div className="mt-3 space-y-2">
            {[
              ["auto_accept", "Automatically accept valid orders"],
              ["reject_unavailable_items", "Reject orders containing unavailable items"],
              ["reject_when_closed", "Reject orders when the store is closed"],
              ["reject_unmapped_items", "Reject orders with unmapped items (otherwise route to staff)"],
            ].map(([k, l]) => (
              <div key={k} className="flex items-center justify-between gap-2"><span>{l}</span><Toggle checked={!!policy[k]} onChange={(v) => setPolicy({ ...policy, [k]: v })} /></div>
            ))}
            <Field label="Price tolerance (cents)"><Input type="number" value={policy.price_tolerance_cents ?? 50} onChange={(e) => setPolicy({ ...policy, price_tolerance_cents: Number(e.target.value) })} /></Field>
            <Field label="If staff have not reviewed before the provider deadline">
              <Select value={settings.on_review_timeout || "reject"} onChange={(e) => setSettings({ ...settings, on_review_timeout: e.target.value })}>
                <option value="reject">Reject with reason</option>
                <option value="accept">Accept</option>
              </Select>
            </Field>
            <div className="flex items-center justify-between"><span>Send order-ready signal</span><Toggle checked={settings.send_ready_signal !== false} onChange={(v) => setSettings({ ...settings, send_ready_signal: v })} /></div>
            <Button size="sm" onClick={() => put({ settings: { ...settings, acceptance_policy: policy } }, "Acceptance settings saved")}>Save settings</Button>
          </div>
        </details>

        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" disabled={busy !== null || !row.id} onClick={() => op("test")}>{busy === "test" ? "Testing…" : "Test connection"}</Button>
          <Button variant="secondary" size="sm" disabled={busy !== null || !row.enabled || !row.supports?.menu_push} onClick={() => op("menu-sync")}>{busy === "menu-sync" ? "Syncing…" : "Sync menu"}</Button>
          <Button variant="ghost" size="sm" disabled={!row.id} onClick={() => portalApi.get(`/stores/${storeId}/integrations/${row.provider}/activity/`).then(setActivity)}>Error history & activity</Button>
        </div>
        {!row.supports?.menu_push && row.api_available && <p className="text-xs text-slate-500">Menu push is not enabled for this provider; maintain the provider menu with ids from the catalogue or add explicit mappings.</p>}
      </div>
      {activity && (
        <Modal open title={`${row.provider_name} activity`} onClose={() => setActivity(null)} wide>
          <h4 className="mb-2 font-semibold">Webhook events</h4>
          <Table head={["Received", "Event", "Status", "Attempts", "Error"]} empty={!activity.events.length}>
            {activity.events.map((e: any) => (
              <tr key={e.id}><td className="px-3 py-1 text-xs">{dt(e.received_at)}</td><td className="px-3 text-xs">{e.type}<div className="text-slate-400">{e.event_id}</div></td><td className="px-3"><Badge tone={e.status}>{e.status}</Badge></td><td className="px-3">{e.attempts}/{e.delivery_count}</td><td className="px-3 text-xs text-red-700">{e.error}</td></tr>
            ))}
          </Table>
          <h4 className="mb-2 mt-6 font-semibold">Synchronization</h4>
          <Table head={["At", "Kind", "Ref", "Status", "Detail"]} empty={!activity.sync_records.length}>
            {activity.sync_records.map((r: any) => (
              <tr key={r.id}><td className="px-3 py-1 text-xs">{dt(r.at)}</td><td className="px-3">{r.kind}</td><td className="px-3 text-xs">{r.reference}</td><td className="px-3"><Badge tone={r.status}>{r.status}</Badge></td><td className="px-3 text-xs">{r.detail}</td></tr>
            ))}
          </Table>
          <h4 className="mb-2 mt-6 font-semibold">Integration log</h4>
          <Table head={["At", "Level", "Operation", "Message"]} empty={!activity.logs.length}>
            {activity.logs.map((l: any) => (
              <tr key={l.id}><td className="px-3 py-1 text-xs">{dt(l.at)}</td><td className="px-3"><Badge tone={l.level === "error" ? "error" : undefined}>{l.level}</Badge></td><td className="px-3 text-xs">{l.direction} {l.operation}</td><td className="px-3 text-xs">{l.message}</td></tr>
            ))}
          </Table>
        </Modal>
      )}
    </Card>
  );
}

function AccountModal({ provider, onClose }: { provider: string; onClose: () => void }) {
  const s = usePortalSession();
  const [name, setName] = useState("");
  const [env, setEnv] = useState("sandbox");
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal open title={`New ${provider} credentials`} onClose={onClose}>
      <div className="space-y-3">
        <ErrorBanner error={error} />
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Environment"><Select value={env} onChange={(e) => setEnv(e.target.value)}><option value="sandbox">Sandbox</option><option value="production">Production</option></Select></Field>
        {(ACCOUNT_FIELDS[provider] || []).map((f) => (
          <Field key={f.key} label={f.label}><Input type={f.secret ? "password" : "text"} autoComplete="off" value={creds[f.key] || ""} onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })} /></Field>
        ))}
        <Button onClick={async () => {
          try {
            await portalApi.post("/provider-accounts/", { provider, name, environment: env, credentials: creds, franchisee: s.user.role_level === "hq" ? null : s.user.franchisee_id });
            onClose();
          } catch (e) {
            setError(e instanceof ApiError ? e.describe() : String(e));
          }
        }}>Save credentials</Button>
      </div>
    </Modal>
  );
}
