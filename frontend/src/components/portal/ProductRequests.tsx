"use client";
import { useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Table } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { dt, money } from "@/lib/format";

export default function ProductRequests() {
  const s = usePortalSession();
  const [rows, setRows] = useState<any[]>([]);
  const [form, setForm] = useState({ name: "", description: "", proposed_price_cents: "" });
  const [error, setError] = useState<string | null>(null);
  const load = () => portalApi.get("/product-requests/").then((d) => setRows(results(d)));
  useEffect(() => {
    load();
  }, []);
  const canSubmit = s.user.permissions.includes("catalog.request_products");
  const canReview = s.user.permissions.includes("catalog.approve_requests");
  return (
    <div className="space-y-6">
      {canSubmit && (
        <Card title="Request a new product">
          <ErrorBanner error={error} />
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Product name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Proposed price (cents)"><Input type="number" value={form.proposed_price_cents} onChange={(e) => setForm({ ...form, proposed_price_cents: e.target.value })} /></Field>
            <Field label="Description / rationale"><Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          </div>
          <Button className="mt-3" disabled={!form.name} onClick={async () => {
            try {
              await portalApi.post("/product-requests/", { ...form, proposed_price_cents: form.proposed_price_cents ? Number(form.proposed_price_cents) : null });
              setForm({ name: "", description: "", proposed_price_cents: "" });
              load();
            } catch (e) {
              setError(e instanceof ApiError ? e.describe() : String(e));
            }
          }}>Submit to headquarters</Button>
        </Card>
      )}
      <Card title="Product requests">
        <Table head={["Submitted", "Franchisee", "Product", "Price", "Status", "Notes", ""]} empty={!rows.length}>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="px-3 py-2 text-xs">{dt(r.created_at)}</td>
              <td className="px-3">{r.franchisee_code}</td>
              <td className="px-3"><div className="font-medium">{r.name}</div><div className="text-xs text-slate-500">{r.description}</div></td>
              <td className="px-3">{money(r.proposed_price_cents)}</td>
              <td className="px-3"><Badge tone={r.status === "approved" ? "connected" : r.status === "rejected" ? "error" : "pending"}>{r.status}</Badge></td>
              <td className="px-3 text-xs">{r.review_notes}</td>
              <td className="px-3">
                {canReview && r.status === "pending" && (
                  <div className="flex gap-1">
                    <Button size="sm" variant="success" onClick={() => portalApi.post(`/product-requests/${r.id}/approve/`, { notes: prompt("Approval notes") || "" }).then(load)}>Approve</Button>
                    <Button size="sm" variant="danger" onClick={() => portalApi.post(`/product-requests/${r.id}/reject/`, { notes: prompt("Reason") || "" }).then(load)}>Reject</Button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
