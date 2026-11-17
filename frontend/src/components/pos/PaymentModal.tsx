"use client";
import { useEffect, useState } from "react";
import { Button, ErrorBanner, Input, Modal, Notice, cx } from "@/components/ui";
import { ApiError, api, loadSession } from "@/lib/api";
import { money, uuid } from "@/lib/format";
import { CardResult, CardStatus, CardStep, bbposCardsOnThisDevice, native } from "@/lib/native";

const posApi = api("pos");

const STEP_TEXT: Record<CardStep, string> = {
  preparing: "Preparing payment…",
  awaiting_card: "Present card on the reader",
  processing: "Processing…",
  verifying: "Verifying with the bank…",
  recording: "Saving payment to the order…",
};

export default function PaymentModal({ order, onClose, onPaid }: { order: any; onClose: () => void; onPaid: (o: any) => void }) {
  const due = order.total_cents - (order.amount_paid_cents ?? 0);
  const readerCards = bbposCardsOnThisDevice(loadSession("pos")?.store);
  const [method, setMethod] = useState<"cash" | "card" | "online">(readerCards ? "card" : "cash");
  const [amount, setAmount] = useState(due);
  const [tendered, setTendered] = useState(0);
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pendingCard, setPendingCard] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [change, setChange] = useState<number | null>(null);
  // Card reader (POS app + bbpos)
  const [status, setStatus] = useState<CardStatus | null>(null);
  const [tip, setTip] = useState(0);
  const [customTip, setCustomTip] = useState("");
  const [step, setStep] = useState<{ step: CardStep | null; prompt: string | null }>({ step: null, prompt: null });
  const [result, setResult] = useState<CardResult | null>(null);
  const quick = [due, Math.ceil(due / 500) * 500, Math.ceil(due / 1000) * 1000, Math.ceil(due / 2000) * 2000, 5000, 10000].filter((v, i, a) => v >= due && a.indexOf(v) === i).slice(0, 5);

  useEffect(() => {
    if (!readerCards) return;
    native.call<CardStatus>("card.status").then(setStatus).catch(() => undefined);
    const offStatus = native.on("card.status", setStatus);
    const offStep = native.on("card.step", setStep);
    return () => { offStatus(); offStep(); };
  }, [readerCards]);

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

  function handleCardResult(r: CardResult | null) {
    if (!r) return;
    setResult(r);
    if (r.kind === "paid") onPaid(r.order);
  }

  async function chargeOnReader() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      handleCardResult(await native.call<CardResult>("card.charge", { orderId: order.id, orderNumber: order.order_number, amountCents: amount, tipCents: tip }));
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
      setStep({ step: null, prompt: null });
    }
  }

  async function recheck() {
    setBusy(true);
    try {
      handleCardResult(await native.call<CardResult | null>("card.recheck", { orderId: order.id }));
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  }

  const cardPaid = result?.kind === "paid";
  const inProgress = !!step.step;
  const presets = status?.tipPresetsPpm?.length ? status.tipPresetsPpm : [150000, 180000, 200000];

  return (
    <Modal open onClose={inProgress ? () => undefined : onClose} title={`Payment · Order #${order.order_number}`}>
      <div className="space-y-4">
        <div className="flex items-baseline justify-between"><span className="text-slate-500">{cardPaid ? "Amount paid" : "Amount due"}</span><span className="text-3xl font-bold">{money(cardPaid ? amount + tip : due)}</span></div>
        <ErrorBanner error={error} />
        {change !== null && <Notice tone="ok">Paid. Change due: <b>{money(change)}</b></Notice>}
        {cardPaid && result.kind === "paid" && <Notice tone="ok">Approved · {result.cardLabel}{tip ? ` · incl. tip ${money(tip)}` : ""}</Notice>}
        {inProgress ? (
          <div className="space-y-3 py-6 text-center">
            <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-brand-600 border-t-transparent" />
            <div className="text-xl font-bold">{STEP_TEXT[step.step!]}</div>
            {step.prompt && <div className="text-lg">{step.prompt}</div>}
            <div className="text-sm text-slate-500">{money(amount + tip)}{tip ? ` (incl. tip ${money(tip)})` : ""}</div>
            {step.step === "awaiting_card" && <Button variant="secondary" onClick={() => native.call("card.cancel").catch(() => undefined)}>Cancel</Button>}
          </div>
        ) : pendingCard ? (
          <div className="space-y-3">
            <Notice>Present card on the terminal. The POS never handles card numbers.</Notice>
            <Button size="xl" className="w-full" onClick={confirmCard}>Check terminal status</Button>
          </div>
        ) : change === null && !cardPaid && (
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
            {method === "card" && readerCards ? (
              <div className="space-y-3">
                {status && !status.ready ? (
                  <Notice tone="warn">
                    {status.problem} <button className="font-semibold underline" onClick={() => native.call("app.openReader").catch(() => undefined)}>Open card reader</button>
                  </Notice>
                ) : (
                  <div className="text-sm text-slate-500">
                    Reader: {status?.reader?.label ?? "…"}{status?.stripeMode === "test" ? " · TEST mode" : ""}
                  </div>
                )}
                {status?.tipsEnabled && (
                  <div>
                    <div className="mb-2 text-sm text-slate-500">Tip</div>
                    <div className="grid grid-cols-4 gap-2">
                      <button onClick={() => { setTip(0); setCustomTip(""); }} className={cx("touch-target rounded-lg border-2 font-semibold", tip === 0 ? "border-brand-600 bg-brand-50" : "border-slate-200")}>No tip</button>
                      {presets.map((ppm) => {
                        const v = Math.round((amount * ppm) / 1_000_000);
                        return (
                          <button key={ppm} onClick={() => { setTip(v); setCustomTip(""); }} className={cx("touch-target rounded-lg border-2 font-semibold", !customTip && tip === v && v > 0 ? "border-brand-600 bg-brand-50" : "border-slate-200")}>
                            {ppm / 10000}%<div className="text-xs font-normal text-slate-500">{money(v)}</div>
                          </button>
                        );
                      })}
                    </div>
                    <Input className="mt-2" type="number" placeholder="Custom tip (cents)" value={customTip} onChange={(e) => { setCustomTip(e.target.value); setTip(Math.max(0, Number(e.target.value) || 0)); }} />
                  </div>
                )}
                {status?.mock && (
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-slate-500">Mock reader outcome:</span>
                    {(["approve", "decline"] as const).map((o) => (
                      <button key={o} onClick={() => native.call("card.setMockOutcome", { outcome: o }).catch(() => undefined)} className={cx("rounded-lg border-2 px-3 py-1 font-semibold capitalize", status.mock?.outcome === o ? "border-brand-600 bg-brand-50" : "border-slate-200")}>{o}</button>
                    ))}
                  </div>
                )}
                {result?.kind === "declined" && <ErrorBanner error={`Declined: ${result.message}`} />}
                {result?.kind === "failed" && <ErrorBanner error={result.message} />}
                {result?.kind === "canceled" && <Notice tone="warn">Payment cancelled.</Notice>}
                {result?.kind === "record_pending" && <Notice tone="warn">{result.message}</Notice>}
                {result?.kind === "uncertain" ? (
                  <>
                    <Notice tone="warn">{result.message}</Notice>
                    <Button size="xl" variant="secondary" className="w-full" disabled={busy} onClick={recheck}>Check again</Button>
                  </>
                ) : result?.kind !== "record_pending" && (
                  <Button size="xl" className="w-full" disabled={busy || !status?.ready || amount <= 0 || amount > due} onClick={chargeOnReader}>
                    {result ? "Try again · " : "Charge "}{money(amount + tip)}
                  </Button>
                )}
              </div>
            ) : (
              <Button size="xl" className="w-full" disabled={busy || amount <= 0} onClick={pay}>{busy ? "Processing…" : `Take ${money(amount)}`}</Button>
            )}
          </>
        )}
        {(change !== null || cardPaid || result?.kind === "record_pending") && <Button size="lg" className="w-full" onClick={onClose}>Done</Button>}
      </div>
    </Modal>
  );
}
