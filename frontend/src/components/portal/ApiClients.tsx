"use client";
import { useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Notice, Select, Table } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { dt } from "@/lib/format";
import { useStores } from "./useStores";

const SCOPES = ["stores:read", "menu:read", "orders:write", "orders:read", "orders:cancel"];

export default function ApiClients() {
  const s = usePortalSession();
  const { stores } = useStores();
  const [rows, setRows] = useState<any[]>([]);
  const [form, setForm] = useState<any | null>(null);
  const [issued, setIssued] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => portalApi.get("/api-clients/").then((d) => setRows(results(d)));
  useEffect(() => {
    load();
  }, []);
  return (
    <Card title="External applications (API clients)" actions={<Button onClick={() => setForm({ name: "", kind: "call_centre", scopes: [...SCOPES], stores: [], webhook_url: "" })}>New API client</Button>}>
      <p className="mb-3 text-sm text-slate-500">Call-centre systems, website back-ends and future mobile apps use these keys with the versioned REST API (<a className="underline" href="/api/v1/docs/">documentation</a>). Order status updates are POSTed to the webhook URL, signed with HMAC-SHA256.</p>
      <Table head={["Name", "Type", "Key prefix", "Scopes", "Stores", "Last used", ""]} empty={!rows.length}>
        {rows.map((c) => (
          <tr key={c.id}>
            <td className="px-3 py-2 font-medium">{c.name}</td><td className="px-3">{c.kind}</td><td className="px-3 font-mono text-xs">{c.key_prefix}</td>
            <td className="px-3 text-xs">{c.scopes.join(", ")}</td><td className="px-3 text-xs">{c.stores.length ? c.stores.length : "all in scope"}</td>
            <td className="px-3 text-xs">{dt(c.last_used_at)}</td>
            <td className="px-3">{c.is_active ? (
              <div className="flex gap-1">
                <Button size="sm" variant="secondary" onClick={() => portalApi.post(`/api-clients/${c.id}/rotate-key/`).then(setIssued)}>Rotate key</Button>
                <Button size="sm" variant="danger" onClick={() => portalApi.del(`/api-clients/${c.id}/`).then(load)}>Revoke</Button>
              </div>) : <Badge tone="disabled">revoked</Badge>}
            </td>
          </tr>
        ))}
      </Table>
      {form && (
        <Modal open title="New API client" onClose={() => setForm(null)}>
          <div className="space-y-3">
            <ErrorBanner error={error} />
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Type"><Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="call_centre">Call centre</option><option value="external_api">Website / external API</option><option value="android">Android app</option><option value="ios">iOS app</option><option value="other">Other</option>
            </Select></Field>
            <Field label="Scopes"><div className="flex flex-wrap gap-2">{SCOPES.map((sc) => (
              <label key={sc} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={form.scopes.includes(sc)} onChange={(e) => setForm({ ...form, scopes: e.target.checked ? [...form.scopes, sc] : form.scopes.filter((x: string) => x !== sc) })} />{sc}</label>
            ))}</div></Field>
            <Field label="Restrict to stores (none = all stores in scope)"><div className="flex flex-wrap gap-2">{stores.map((st) => (
              <label key={st.id} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={form.stores.includes(st.id)} onChange={(e) => setForm({ ...form, stores: e.target.checked ? [...form.stores, st.id] : form.stores.filter((x: number) => x !== st.id) })} />{st.number}</label>
            ))}</div></Field>
            <Field label="Status webhook URL (https)"><Input value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} /></Field>
            <Button onClick={async () => {
              try {
                const r = await portalApi.post("/api-clients/", { ...form, franchisee: s.user.role_level === "hq" ? null : s.user.franchisee_id });
                setIssued(r);
                setForm(null);
                load();
              } catch (e) {
                setError(e instanceof ApiError ? e.describe() : String(e));
              }
            }}>Create</Button>
          </div>
        </Modal>
      )}
      {issued && (
        <Modal open title="Credentials issued — copy now" onClose={() => setIssued(null)}>
          <Notice tone="warn">These values are shown only once.</Notice>
          <Field label="API key (header: Authorization: Api-Key …)"><Input readOnly value={issued.api_key} onFocus={(e) => e.target.select()} /></Field>
          {issued.webhook_signing_secret && <Field label="Webhook signing secret"><Input readOnly value={issued.webhook_signing_secret} onFocus={(e) => e.target.select()} /></Field>}
        </Modal>
      )}
    </Card>
  );
}
