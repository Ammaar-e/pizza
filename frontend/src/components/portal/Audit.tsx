"use client";
import { useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Card, Input, Table } from "@/components/ui";
import { dt } from "@/lib/format";

export default function Audit() {
  const [rows, setRows] = useState<any[]>([]);
  const [action, setAction] = useState("");
  useEffect(() => {
    portalApi.get(`/audit-logs/${action ? `?action=${encodeURIComponent(action)}` : ""}`).then(setRows);
  }, [action]);
  return (
    <Card title="Audit trail" actions={<Input placeholder="Filter by action, e.g. order.cancel" value={action} onChange={(e) => setAction(e.target.value)} />}>
      <Table head={["When", "Action", "Entity", "Actor", "Authorized by", "Store", "Details"]} empty={!rows.length}>
        {rows.map((a) => (
          <tr key={a.id}>
            <td className="px-3 py-1.5 text-xs">{dt(a.at)}</td><td className="px-3 font-mono text-xs">{a.action}</td>
            <td className="px-3 text-xs">{a.entity_type} {String(a.entity_id).slice(0, 8)}</td><td className="px-3 text-xs">{a.actor}</td>
            <td className="px-3 text-xs">{a.authorized_by ?? ""}</td><td className="px-3 text-xs">{a.store}</td>
            <td className="max-w-md truncate px-3 text-xs text-slate-500" title={JSON.stringify(a.data)}>{JSON.stringify(a.data)}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
