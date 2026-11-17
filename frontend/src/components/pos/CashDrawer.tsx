"use client";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, ErrorBanner, Field, Input, Modal, Notice, Select, Table, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { money, time, uuid } from "@/lib/format";

const posApi = api("pos");

const KIND_LABEL: Record<string, string> = {
  sale: "Cash sale", refund: "Cash refund", drop: "Drop to safe", addition: "Cash addition", paid_out: "Paid-out",
  petty_expense: "Petty expense", correction: "Correction", transfer_in: "Transfer in", transfer_out: "Transfer out",
};
const DENOMS = [10000, 5000, 2000, 1000, 500, 200, 100, 25, 10, 5];

export function ShiftReport({ id, onClose, ns = "pos" }: { id: string; onClose: () => void; ns?: "pos" | "portal" }) {
  const [text, setText] = useState("");
  useEffect(() => {
    api(ns).get(`/shift-closings/${id}/print/`).then((r) => setText(r.text));
  }, [id, ns]);
  async function exportCsv() {
    const s = JSON.parse(localStorage.getItem(`pzp.session.${ns}`) || "{}");
    const r = await fetch(`/api/v1/shift-closings/${id}/export/`, { headers: { Authorization: `Bearer ${s.access}` } });
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `shift-${id.slice(0, 8)}.csv`;
    a.click();
  }
  function print() {
    let el = document.getElementById("receipt-print");
    if (!el) {
      el = document.createElement("pre");
      el.id = "receipt-print";
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.style.display = "block";
    window.print();
    el.style.display = "none";
  }
  return (
    <Modal open title="Shift closing report" onClose={onClose}>
      <pre className="max-h-[60vh] overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs leading-5">{text || "Loading…"}</pre>
      <div className="mt-3 flex gap-2">
        <Button onClick={print}>Print</Button>
        <Button variant="secondary" onClick={exportCsv}>Export CSV</Button>
      </div>
    </Modal>
  );
}

export default function CashDrawer({ can, hasDevice, cashEnabled }: { can: (p: string) => boolean; hasDevice: boolean; cashEnabled: boolean }) {
  const [session, setSession] = useState<any>(undefined);
  const [ledger, setLedger] = useState<any[]>([]);
  const [others, setOthers] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState({ kind: "drop", amount: "", reason: "", mgr: "", category: "" });
  const [float, setFloat] = useState("");
  const [closing, setClosing] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [transfer, setTransfer] = useState<{ to: string; amount: string; mgr: string } | null>(null);
  const [pending, setPending] = useState<any[]>([]);

  const load = useCallback(async () => {
    const s = await posApi.get("/cash-sessions/current/").catch(() => null);
    setSession(s);
    if (s) setLedger(await posApi.get(`/cash-sessions/${s.id}/transactions/`));
    if (can("cash.review")) {
      const all = await posApi.get("/cash-sessions/?status=open,pending_approval&page_size=50").catch(() => ({ results: [] }));
      setOthers(all.results.filter((x: any) => x.id !== s?.id && x.status === "open"));
      setPending(all.results.filter((x: any) => x.status === "pending_approval"));
    }
  }, [can]);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<any>, ok?: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice(ok);
      setForm({ kind: form.kind, amount: "", reason: "", mgr: "", category: "" });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }

  if (!hasDevice) return <div className="p-6"><Notice tone="warn">The cash drawer requires a registered and approved terminal.</Notice></div>;
  if (!cashEnabled) return <div className="p-6"><Notice tone="warn">Cash handling is disabled for this device by its configuration profile.</Notice></div>;
  if (session === undefined) return null;

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <ErrorBanner error={error} />
          {notice && <Notice tone="ok">{notice}</Notice>}
          {!session ? (
            <div className="space-y-3 rounded-2xl bg-white p-6 shadow-sm">
              <h2 className="text-xl font-bold">Open register</h2>
              <p className="text-sm text-slate-500">Count the starting float. The register is assigned to you for this session.</p>
              <Input type="number" inputMode="numeric" placeholder="Opening float (cents)" value={float} onChange={(e) => setFloat(e.target.value)} />
              <Button size="xl" className="w-full" disabled={!can("cash.manage_session")}
                onClick={() => run(() => posApi.post("/cash-sessions/open/", { opening_float_cents: Number(float || 0) }), "Register opened")}>Open register</Button>
            </div>
          ) : (
            <>
              <div className="rounded-2xl bg-white p-6 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h2 className="text-xl font-bold">{session.register_name || "Register"} <Badge tone="connected">open</Badge></h2>
                    <p className="text-sm text-slate-500">Responsible: <b>{session.employee_name}</b> · opened {time(session.opened_at)} by {session.opened_by_name}</p>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-slate-500">Opening float</div>
                    <div className="text-xl font-bold">{money(session.opening_float_cents)}</div>
                  </div>
                </div>
                <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">
                  {session.expected_now_cents === null
                    ? "Blind count: the expected drawer amount is revealed after you close and count."
                    : <>Expected in drawer now: <b>{money(session.expected_now_cents)}</b></>}
                </div>
              </div>
              <div className="rounded-2xl bg-white p-6 shadow-sm">
                <h3 className="mb-3 font-semibold">Record cash movement</h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                    <option value="drop">Cash drop to safe</option>
                    <option value="addition">Cash addition</option>
                    <option value="paid_out">Paid-out</option>
                    <option value="petty_expense">Petty cash expense</option>
                    <option value="correction">Correction (manager)</option>
                  </Select>
                  <Input type="number" placeholder={form.kind === "correction" ? "Amount (cents, negative to remove)" : "Amount (cents)"} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                  <Input placeholder="Reason" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
                  {form.kind === "petty_expense" && <Input placeholder="Expense category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />}
                  <Input type="password" inputMode="numeric" placeholder="Manager code (if required)" value={form.mgr} onChange={(e) => setForm({ ...form, mgr: e.target.value })} />
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button onClick={() => run(() => posApi.post(`/cash-sessions/${session.id}/transactions/`, {
                    kind: form.kind, amount_cents: Number(form.amount), reason: form.reason, expense_category: form.category, manager_code: form.mgr || undefined,
                  }, { "Idempotency-Key": uuid() }), `${KIND_LABEL[form.kind]} recorded`)} disabled={!form.amount}>Record</Button>
                  {can("cash.paid_in_out") || can("cash.review") ? <Button variant="secondary" onClick={() => setTransfer({ to: "", amount: "", mgr: "" })}>Transfer to another register</Button> : null}
                  <Button variant="danger" className="ml-auto" onClick={() => setClosing(true)}>Close & count register</Button>
                </div>
              </div>
              <div className="rounded-2xl bg-white p-6 shadow-sm">
                <h3 className="mb-2 font-semibold">Drawer ledger</h3>
                <Table head={["Time", "Type", "Amount", "Reason / order", "By"]} empty={!ledger.length}>
                  {ledger.map((t) => (
                    <tr key={t.id}>
                      <td className="px-3 py-1.5 text-xs">{time(t.created_at)}</td>
                      <td className="px-3">{KIND_LABEL[t.kind] ?? t.kind}{t.post_close && <Badge tone="pending">post-close</Badge>}</td>
                      <td className={cx("px-3 font-mono", t.amount_cents < 0 ? "text-red-700" : "text-emerald-700")}>{money(t.amount_cents)}</td>
                      <td className="px-3 text-xs">{t.order_number ? `Order #${t.order_number}` : t.reason}</td>
                      <td className="px-3 text-xs">{t.user_name}{t.authorized_by_name && ` (auth ${t.authorized_by_name})`}</td>
                    </tr>
                  ))}
                </Table>
              </div>
            </>
          )}
        </div>
        <aside className="space-y-4">
          {can("cash.review") && (
            <div className="rounded-2xl bg-white p-4 shadow-sm">
              <h3 className="mb-2 font-semibold">Other open registers</h3>
              {others.length ? others.map((s) => (
                <div key={s.id} className="flex justify-between border-b py-1 text-sm"><span>{s.register_name}</span><span>{s.employee_name} · {money(s.expected_now_cents)}</span></div>
              )) : <p className="text-sm text-slate-500">None</p>}
            </div>
          )}
          {can("cash.approve") && (
            <div className="rounded-2xl bg-white p-4 shadow-sm">
              <h3 className="mb-2 font-semibold">Awaiting approval</h3>
              {pending.length ? pending.map((s) => (
                <div key={s.id} className="border-b py-2 text-sm">
                  <div className="flex justify-between"><b>{s.register_name}</b><span className={s.variance_cents < 0 ? "text-red-700" : "text-emerald-700"}>{money(s.variance_cents)}</span></div>
                  <div className="text-xs text-slate-500">{s.employee_name} · closed {time(s.closed_at)}</div>
                  <Button size="sm" className="mt-1" onClick={() => run(() => posApi.post(`/cash-sessions/${s.id}/approve/`, { notes: prompt("Approval notes") || "" }), "Approved")}>Review & approve</Button>
                </div>
              )) : <p className="text-sm text-slate-500">Nothing pending</p>}
            </div>
          )}
        </aside>
      </div>
      {closing && session && <CloseModal session={session} onClose={() => setClosing(false)} onClosed={(r) => { setClosing(false); load(); if (r.shift_closing_id) setReport(r.shift_closing_id); }} />}
      {report && <ShiftReport id={report} onClose={() => setReport(null)} />}
      {transfer && session && (
        <Modal open title="Transfer cash between registers" onClose={() => setTransfer(null)}>
          <div className="space-y-3">
            <Field label="To register"><Select value={transfer.to} onChange={(e) => setTransfer({ ...transfer, to: e.target.value })}>
              <option value="">Choose…</option>
              {others.map((s) => <option key={s.id} value={s.id}>{s.register_name} ({s.employee_name})</option>)}
            </Select></Field>
            <Field label="Amount (cents)"><Input type="number" value={transfer.amount} onChange={(e) => setTransfer({ ...transfer, amount: e.target.value })} /></Field>
            <Field label="Manager code"><Input type="password" value={transfer.mgr} onChange={(e) => setTransfer({ ...transfer, mgr: e.target.value })} /></Field>
            <Button onClick={() => run(() => posApi.post(`/cash-sessions/${session.id}/transfer/`, { to_session: transfer.to, amount_cents: Number(transfer.amount), manager_code: transfer.mgr || undefined, reason: "Register transfer" }), "Transfer recorded").then(() => setTransfer(null))}>Transfer</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function CloseModal({ session, onClose, onClosed }: { session: any; onClose: () => void; onClosed: (r: any) => void }) {
  const [counts, setCounts] = useState<Record<number, string>>({});
  const [explanation, setExplanation] = useState("");
  const [needExplain, setNeedExplain] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const total = DENOMS.reduce((s, d) => s + d * Number(counts[d] || 0), 0);
  async function submit() {
    setError(null);
    try {
      const denominations = Object.fromEntries(DENOMS.filter((d) => Number(counts[d] || 0) > 0).map((d) => [String(d), Number(counts[d])]));
      const r = await posApi.post(`/cash-sessions/${session.id}/close/`, { counted_cash_cents: total, denominations, explanation });
      onClosed(r);
    } catch (e) {
      if (e instanceof ApiError && e.code === "explanation_required") setNeedExplain(e.errors?.[0]);
      else setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }
  return (
    <Modal open title="Close register — count the drawer" onClose={onClose}>
      <div className="space-y-3">
        <ErrorBanner error={error} />
        <div className="grid grid-cols-2 gap-2">
          {DENOMS.map((d) => (
            <label key={d} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
              <span className="w-16 font-semibold">{money(d)}</span>
              <input className="w-20 rounded border px-2 py-1 text-right" type="number" min={0} inputMode="numeric" value={counts[d] || ""} onChange={(e) => setCounts({ ...counts, [d]: e.target.value })} />
            </label>
          ))}
        </div>
        <div className="flex justify-between text-lg font-bold"><span>Counted</span><span>{money(total)}</span></div>
        {needExplain && (
          <Notice tone="warn">
            The count differs from expected by <b>{money(needExplain.variance_cents)}</b> (allowed ±{money(needExplain.threshold_cents)}). Explain the discrepancy; a manager must approve this closing.
          </Notice>
        )}
        {needExplain && <textarea className="h-20 w-full rounded-lg border p-2 text-sm" placeholder="Explanation" value={explanation} onChange={(e) => setExplanation(e.target.value)} />}
        <Button size="xl" className="w-full" onClick={submit} disabled={needExplain && !explanation.trim()}>Close register</Button>
      </div>
    </Modal>
  );
}
