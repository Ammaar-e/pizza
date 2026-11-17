"use client";
import { useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Badge, Card, Notice, Table, Toggle } from "@/components/ui";
import { results } from "@/lib/api";
import { dt } from "@/lib/format";

export default function Providers() {
  const [providers, setProviders] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const load = () => {
    portalApi.get("/integration-providers/").then(setProviders);
    portalApi.get("/webhook-events/?page_size=50").then((d) => setEvents(results(d)));
  };
  useEffect(load, []);
  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        {providers.map((p) => (
          <Card key={p.code} title={p.name} actions={<Toggle checked={p.network_enabled} onChange={(v) => portalApi.patch(`/integration-providers/${p.code}/`, { network_enabled: v }).then(load)} label="Permitted network-wide" />}>
            <div className="space-y-2 text-sm">
              <div className="flex flex-wrap gap-1">
                <Badge tone={p.network_enabled ? "connected" : "disabled"}>{p.network_enabled ? "permitted" : "not permitted"}</Badge>
                {Object.entries(p.supports).map(([k, v]) => <Badge key={k} tone={v ? "synced" : "disabled"}>{k.replace("_", " ")}</Badge>)}
              </div>
              {!p.api_available && <Notice tone="warn">No implemented order API — see access requirements.</Notice>}
              <p className="text-xs text-slate-600">{p.access_requirements}</p>
              <p className="text-xs">Acknowledgement deadline: {Math.round(p.ack_deadline_seconds / 60 * 10) / 10} min · <a className="underline" href={p.documentation_url} target="_blank" rel="noreferrer">official docs</a></p>
            </div>
          </Card>
        ))}
      </div>
      <Card title="Integration monitoring — recent webhook events">
        <Table head={["Received", "Provider", "Event", "Status", "Attempts", "Error", ""]} empty={!events.length}>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="px-3 py-1.5 text-xs">{dt(e.received_at)}</td><td className="px-3">{e.provider}</td>
              <td className="px-3 text-xs">{e.event_type}<div className="text-slate-400">{e.external_order_id}</div></td>
              <td className="px-3"><Badge tone={e.status}>{e.status}</Badge></td><td className="px-3">{e.attempts}/{e.delivery_count}</td>
              <td className="max-w-sm px-3 text-xs text-red-700">{e.last_error}</td>
              <td className="px-3">{e.status === "failed" && <button className="text-xs text-brand-600 underline" onClick={() => portalApi.post(`/webhook-events/${e.id}/retry/`).then(load)}>Retry</button>}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
