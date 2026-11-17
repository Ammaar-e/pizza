"use client";
import { useState } from "react";
import { Button, ErrorBanner, Input, Modal, Notice, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { money, uuid } from "@/lib/format";

const posApi = api("pos");

export default function PaymentModal({ order, onClose, onPaid }: { order: any; onClose: () => void; onPaid: (o: any) => void }) {
  const due = order.total_cents - (order.amount_paid_cents ?? 0);
  const [method, setMethod] = useState<"cash" | "card" | "online">("cash");
  const [amount, setAmount] = useState(due);
  const [tendered, setTendered] = useState(0);
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pendingCard, setPendingCard] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [change, setChange] = useState<number | null>(null);
  const quick = [due, Math.ceil(due / 500) * 500, Math.ceil(due / 1000) * 1000, Math.ceil(due / 2000) * 2000, 5000, 10000].filter((v, i, a) => v >= due && a.indexOf(v) === i).slice(0, 5);

  async function pay() {
    setBusy(true);
    setError(null);
    try {
      const body: any = { method, amount_cents: amount };
      if (method === "cash") body.tendered_cents = tendered || amount;
      if (method === "online") body.reference = reference;
      const r = await posApi.post(`/orders/${order.id}/payments/`, body, { "Idempotency-Key": uuid() });
      if (r.payment.status === "pending" && method === "card") setPendingCard(r.payment.id);
      else if (r.payment.status === "failed") setError(r.payment.failure_reason || "Payment failed");
      else {
        setChange(r.payment.change_cents);
        onPaid(r);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : "Network error — payment not recorded");
    } finally {
      setBusy(false);
    }
  }

  async function confirmCard() {
    const r = await posApi.post(`/payments/${pendingCard}/confirm/`);
    if (r.status === "captured") {
      const o = await posApi.get(`/orders/${order.id}/`);
      setPendingCard(null);
      onPaid(o);
    } else if (r.status === "failed") {
      setPendingCard(null);
      setError("Card declined");
    } else setError("Waiting for the card reader…");
  }

  return (
    <Modal open onClose={onClose} title={`Payment · Order #${order.order_number}`}>
      <div className="space-y-4">
        <div className="flex items-baseline justify-between"><span className="text-slate-500">Amount due</span><span className="text-3xl font-bold">{money(due)}</span></div>
        <ErrorBanner error={error} />
        {change !== null && <Notice tone="ok">Paid. Change due: <b>{money(change)}</b></Notice>}
        {pendingCard ? (
          <div className="space-y-3">
            <Notice>Present card on the terminal. The POS never handles card numbers.</Notice>
            <Button size="xl" className="w-full" onClick={confirmCard}>Check terminal status</Button>
          </div>
        ) : change === null && (
          <>
            <div className="grid grid-cols-3 gap-2">
              {(["cash", "card", "online"] as const).map((m) => (
                <button key={m} onClick={() => setMethod(m)} className={cx("touch-target rounded-xl border-2 font-semibold capitalize", method === m ? "border-brand-600 bg-brand-50" : "border-slate-200")}>{m === "online" ? "Online ref" : m}</button>
              ))}
            </div>
            <label className="block text-sm">Amount to apply<Input type="number" value={amount} onChange={(e) => setAmount(Number(e.target.value))} /></label>
            {method === "cash" && (
              <div>
                <div className="mb-2 text-sm text-slate-500">Cash tendered</div>
                <div className="grid grid-cols-5 gap-2">
                  {quick.map((q) => <button key={q} onClick={() => setTendered(q)} className={cx("touch-target rounded-lg border-2 font-semibold", tendered === q ? "border-brand-600" : "border-slate-200")}>{money(q)}</button>)}
                </div>
                <Input className="mt-2" type="number" placeholder="Other amount (cents)" value={tendered || ""} onChange={(e) => setTendered(Number(e.target.value))} />
                {tendered > amount && <p className="mt-1 text-sm">Change: <b>{money(tendered - amount)}</b></p>}
              </div>
            )}
            {method === "online" && <Input placeholder="Payment gateway reference" value={reference} onChange={(e) => setReference(e.target.value)} />}
            <Button size="xl" className="w-full" disabled={busy || amount <= 0} onClick={pay}>{busy ? "Processing…" : `Take ${money(amount)}`}</Button>
          </>
        )}
        {change !== null && <Button size="lg" className="w-full" onClick={onClose}>Done</Button>}
      </div>
    </Modal>
  );
}
