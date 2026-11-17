"use client";
import { useEffect, useState } from "react";
import { Badge, Button, ErrorBanner, Input, Modal, Notice, Table } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { SOURCE_LABEL, dt, money } from "@/lib/format";
import PaymentModal from "./PaymentModal";

const posApi = api("pos");

export async function printReceipt(orderId: string) {
  const r = await posApi.get(`/orders/${orderId}/receipt/`);
  let el = document.getElementById("receipt-print");
  if (!el) {
    el = document.createElement("pre");
    el.id = "receipt-print";
    el.style.display = "none";
    document.body.appendChild(el);
  }
  el.textContent = r.text;
  el.style.display = "block";
  window.print();
  el.style.display = "none";
}

export default function OrderDetail({ orderId, onClose, can }: { orderId: string; onClose: () => void; can: (p: string) => boolean }) {
  const [o, setO] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [mgr, setMgr] = useState("");
  const [reason, setReason] = useState("");
  const [paying, setPaying] = useState(false);
  const load = () => {
    posApi.get(`/orders/${orderId}/`).then(setO).catch((e) => setError(String(e)));
    posApi.get(`/orders/${orderId}/events/`).then(setEvents);
  };
  useEffect(load, [orderId]);

  async function act(fn: () => Promise<any>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }
  if (!o) return null;
  const terminal = ["completed", "cancelled", "rejected"].includes(o.status);
  const due = o.total_cents - o.amount_paid_cents;

  return (
    <Modal open wide onClose={onClose} title={<span>Order #{o.order_number} <Badge tone={o.status}>{o.status.replace("_", " ")}</Badge></span>}>
      <div className="space-y-4">
        <ErrorBanner error={error} />
        <div className="flex flex-wrap gap-2 text-sm">
          <Badge>{SOURCE_LABEL[o.source] ?? o.source}</Badge>
          <Badge>{o.order_type.replace("_", " ")}</Badge>
          {o.entry_channel && <Badge>{o.entry_channel.replace("_", " ")}</Badge>}
          <Badge tone={o.payment_status === "paid" || o.payment_status === "provider_paid" ? "connected" : "pending"}>{o.payment_status.replace("_", " ")}</Badge>
          {o.external_display_id && <Badge>ref {o.external_display_id}</Badge>}
          {o.is_offline_origin && <Badge tone="pending">entered offline</Badge>}
        </div>
        {o.requires_attention && <Notice tone="warn"><b>Needs attention:</b> {o.attention_reason}</Notice>}
        {o.integration && (
          <div className="rounded-lg border p-3 text-sm">
            <div className="flex items-center justify-between"><b>{o.integration.provider_name} synchronization</b><Badge tone={o.provider_sync_status}>{o.provider_sync_status}</Badge></div>
            {o.provider_sync_error && <p className="mt-1 text-xs text-red-700">{o.provider_sync_error}</p>}
            {o.provider_ack_deadline && o.status === "pending_review" && <p className="mt-1 text-xs">Provider deadline: {dt(o.provider_ack_deadline)}</p>}
            {o.validation_issues?.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-xs text-amber-800">{o.validation_issues.map((i: any, k: number) => <li key={k}>{i.message}</li>)}</ul>
            )}
          </div>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <ul className="divide-y rounded-lg border">
              {o.items.map((it: any) => (
                <li key={it.id} className="p-3 text-sm">
                  <div className="flex justify-between font-medium"><span>{it.quantity}× {it.size_name} {it.name}{!it.is_mapped && " ⚠ unmapped"}</span><span>{money(it.line_total_cents)}</span></div>
                  <div className="text-xs text-slate-500">
                    {it.crust_name && <span>{it.crust_name} · </span>}
                    {it.modifiers.filter((m: any) => !(m.is_default && m.quantity === 1 && m.placement === "whole")).map((m: any) => `${m.quantity > 1 ? "Extra " : "+"}${m.name}${m.placement !== "whole" ? ` (${m.placement})` : ""}`).join(", ")}
                    {it.removed.map((r: any) => ` · No ${r.name}`)}
                    {it.special_instructions && <div>“{it.special_instructions}”</div>}
                  </div>
                </li>
              ))}
            </ul>
            <dl className="mt-3 space-y-1 text-sm">
              <div className="flex justify-between"><dt>Subtotal</dt><dd>{money(o.subtotal_cents)}</dd></div>
              {o.discount_cents > 0 && <div className="flex justify-between"><dt>Discounts</dt><dd>−{money(o.discount_cents)}</dd></div>}
              {o.charges_cents > 0 && <div className="flex justify-between"><dt>Charges</dt><dd>{money(o.charges_cents)}</dd></div>}
              <div className="flex justify-between"><dt>Tax</dt><dd>{money(o.tax_cents)}</dd></div>
              <div className="flex justify-between text-lg font-bold"><dt>Total</dt><dd>{money(o.total_cents)}</dd></div>
            </dl>
          </div>
          <div className="space-y-3 text-sm">
            {(o.customer_snapshot?.name || o.customer_snapshot?.phone) && <div><b>Customer:</b> {o.customer_snapshot.name} {o.customer_snapshot.phone}</div>}
            {o.delivery_address?.line1 && <div><b>Deliver to:</b> {o.delivery_address.line1}, {o.delivery_address.city}</div>}
            {o.special_instructions && <div><b>Instructions:</b> {o.special_instructions}</div>}
            <div className="font-semibold">Payments</div>
            <Table head={["Method", "Status", "Amount", ""]} empty={!o.payments.length}>
              {o.payments.map((p: any) => (
                <tr key={p.id}>
                  <td className="px-2 py-1">{p.method}{p.card_last4 && ` ****${p.card_last4}`}</td><td className="px-2"><Badge tone={p.status === "captured" ? "connected" : p.status}>{p.status}</Badge></td>
                  <td className="px-2">{money(p.amount_cents)}{p.refunded_cents > 0 && <span className="text-xs text-red-700"> (−{money(p.refunded_cents)})</span>}</td>
                  <td className="px-2">{can("payments.take") && p.method !== "provider" && ["captured", "partially_refunded"].includes(p.status) && (
                    <Button size="sm" variant="ghost" onClick={() => {
                      const amt = Number(prompt("Refund amount (cents)", String(p.amount_cents - p.refunded_cents)));
                      const why = prompt("Refund reason") || "";
                      if (amt > 0 && why) act(() => posApi.post(`/payments/${p.id}/refund/`, { amount_cents: amt, reason: why, manager_code: mgr || undefined }));
                    }}>Refund</Button>
                  )}</td>
                </tr>
              ))}
            </Table>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t pt-4">
          <Input className="w-40" type="password" inputMode="numeric" placeholder="Manager code" value={mgr} onChange={(e) => setMgr(e.target.value)} />
          <Input className="w-56" placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          {o.status === "pending_review" && (
            <>
              <Button variant="success" onClick={() => act(() => posApi.post(`/orders/${o.id}/accept/`, { manager_code: mgr || undefined, reason }))}>Accept order</Button>
              <Button variant="danger" onClick={() => act(() => posApi.post(`/orders/${o.id}/reject/`, { manager_code: mgr || undefined, reason: reason || "ITEM_UNAVAILABLE" }))}>Reject order</Button>
            </>
          )}
          {["accepted", "preparing"].includes(o.status) && <Button variant="secondary" onClick={() => act(() => posApi.post(`/orders/${o.id}/status/`, { status: "ready" }))}>Mark ready</Button>}
          {o.status === "ready" && o.order_type === "delivery" && <Button variant="secondary" onClick={() => act(() => posApi.post(`/orders/${o.id}/status/`, { status: "out_for_delivery" }))}>Out for delivery</Button>}
          {["ready", "out_for_delivery"].includes(o.status) && <Button variant="secondary" onClick={() => act(() => posApi.post(`/orders/${o.id}/status/`, { status: "completed" }))}>Complete</Button>}
          {!terminal && <Button variant="danger" onClick={() => reason ? act(() => posApi.post(`/orders/${o.id}/cancel/`, { reason, manager_code: mgr || undefined })) : setError("Enter a cancellation reason")}>Cancel order</Button>}
          {o.provider_sync_status === "failed" && <Button variant="secondary" onClick={() => act(() => posApi.post(`/orders/${o.id}/retry-sync/`))}>Retry provider sync</Button>}
          {due > 0 && !terminal && o.payment_status !== "provider_paid" && <Button onClick={() => setPaying(true)}>Take payment ({money(due)})</Button>}
          <Button variant="ghost" onClick={() => printReceipt(o.id)}>Print receipt</Button>
        </div>
        <details className="text-xs">
          <summary className="cursor-pointer font-semibold">Order history ({events.length})</summary>
          <ul className="mt-2 space-y-1">{events.map((e) => <li key={e.id}>{dt(e.created_at)} · <b>{e.event_type}</b> {e.from_status && `${e.from_status} → ${e.to_status}`} · {e.actor_label} {e.message && `— ${e.message}`}</li>)}</ul>
        </details>
      </div>
      {paying && <PaymentModal order={o} onClose={() => { setPaying(false); load(); }} onPaid={() => load()} />}
    </Modal>
  );
}
