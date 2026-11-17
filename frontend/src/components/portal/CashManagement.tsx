"use client";
import { useCallback, useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { ShiftReport } from "@/components/pos/CashDrawer";
import { Badge, Button, Card, ErrorBanner, Input, Select, Stat, Table } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { dt, money } from "@/lib/format";
import { useStores } from "./useStores";

export default function CashManagement() {
  const s = usePortalSession();
  const { stores } = useStores();
  const today = new Date().toISOString().slice(0, 10);
  const [range, setRange] = useState({ from: new Date(Date.now() - 6 * 864e5).toISOString().slice(0, 10), to: today, store: "" });
  const [report, setReport] = useState<any>(null);
  const [sessions, setSessions] = useState<any[]>([]);
  const [closings, setClosings] = useState<any[]>([]);
  const [shift, setShift] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    const q = new URLSearchParams(Object.entries(range).filter(([, v]) => v) as [string, string][]);
    portalApi.get(`/reports/cash/?${q}`).then(setReport).catch((e) => setError(e instanceof ApiError ? e.describe() : String(e)));
    portalApi.get(`/cash-sessions/?page_size=100${range.store ? `&store=${range.store}` : ""}`).then((d) => setSessions(results(d)));
    portalApi.get(`/shift-closings/?page_size=50${range.store ? `&store=${range.store}` : ""}`).then((d) => setClosings(results(d)));
  }, [range]);
  useEffect(load, [load]);
  const approve = async (id: string) => {
    try { await portalApi.post(`/cash-sessions/${id}/approve/`, { notes: prompt("Approval notes") || "" }); load(); }
    catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">From <Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></label>
        <label className="text-sm">To <Input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></label>
        <label className="text-sm">Store <Select value={range.store} onChange={(e) => setRange({ ...range, store: e.target.value })}>
          <option value="">All stores</option>{stores.map((st) => <option key={st.id} value={st.id}>{st.number}</option>)}</Select></label>
      </div>
      <ErrorBanner error={error} />
      {report && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Closed sessions" value={report.totals.closed_sessions} sub={`${report.totals.open_sessions} open now`} />
            <Stat label="Awaiting approval" value={report.totals.pending_approval} />
            <Stat label="Net variance" value={money(report.totals.net_variance_cents)} />
            <Stat label="Short sessions" value={report.totals.shortage_sessions} />
            <Stat label="Over sessions" value={report.totals.overage_sessions} />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="By store">
              <Table head={["Store", "Franchisee", "Sessions", "Net variance", "Shortages"]} empty={!report.by_store.length}>
                {report.by_store.map((r: any) => <tr key={r.store_id}><td className="px-3 py-1.5">{r.store__number}</td><td className="px-3">{r.store__franchisee__code}</td><td className="px-3">{r.sessions}</td><td className="px-3">{money(r.net_variance_cents)}</td><td className="px-3">{r.shortages}</td></tr>)}
              </Table>
            </Card>
            <Card title="By employee">
              <Table head={["Employee", "Sessions", "Net variance", "Shortages"]} empty={!report.by_employee.length}>
                {report.by_employee.map((r: any) => <tr key={r.employee__username}><td className="px-3 py-1.5">{r.employee__username}</td><td className="px-3">{r.sessions}</td><td className="px-3">{money(r.net_variance_cents)}</td><td className="px-3">{r.shortages}</td></tr>)}
              </Table>
            </Card>
          </div>
          <Card title="Open discrepancies">
            <Table head={["Store", "Employee", "Amount", "Explanation", "When", ""]} empty={!report.open_discrepancies.length}>
              {report.open_discrepancies.map((d: any) => (
                <tr key={d.id}><td className="px-3 py-1.5">{d.store}</td><td className="px-3">{d.employee}</td><td className="px-3 text-red-700">{money(d.amount_cents)}</td><td className="px-3 text-xs">{d.explanation}</td><td className="px-3 text-xs">{dt(d.created_at)}</td>
                  <td className="px-3">{s.user.permissions.includes("cash.approve") && <Button size="sm" onClick={() => approve(d.session)}>Approve</Button>}</td></tr>
              ))}
            </Table>
          </Card>
        </>
      )}
      <Card title="Register sessions">
        <Table head={["Store", "Register", "Employee", "Opened", "Closed", "Float", "Expected", "Counted", "Variance", "Status"]} empty={!sessions.length}>
          {sessions.map((x) => (
            <tr key={x.id}>
              <td className="px-3 py-1.5">{x.store_number}</td><td className="px-3">{x.register_name}</td><td className="px-3">{x.employee_name}</td>
              <td className="px-3 text-xs">{dt(x.opened_at)}</td><td className="px-3 text-xs">{dt(x.closed_at)}</td>
              <td className="px-3">{money(x.opening_float_cents)}</td><td className="px-3">{money(x.expected_cash_cents ?? x.expected_now_cents)}</td>
              <td className="px-3">{money(x.counted_cash_cents)}</td>
              <td className={`px-3 ${x.variance_cents < 0 ? "text-red-700" : ""}`}>{money(x.variance_cents)}{x.adjustments?.post_close_corrections_cents ? <div className="text-xs text-slate-500">adj. {money(x.adjustments.adjusted_variance_cents)}</div> : null}</td>
              <td className="px-3"><Badge tone={x.status === "open" ? "accepted" : x.status === "closed" ? "connected" : "pending"}>{x.status.replace("_", " ")}</Badge></td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Shift closing reports">
        <Table head={["Store", "Employee", "Period", "Orders", "Sales", "Cash variance", "Status", ""]} empty={!closings.length}>
          {closings.map((c) => (
            <tr key={c.id}>
              <td className="px-3 py-1.5">{c.store_number}</td><td className="px-3">{c.employee_name}</td>
              <td className="px-3 text-xs">{dt(c.period_start)} – {dt(c.period_end)}</td>
              <td className="px-3">{c.summary.sales.order_count}</td><td className="px-3">{money(c.summary.sales.gross_sales_cents)}</td>
              <td className="px-3">{money(c.summary.cash?.variance_cents)}</td>
              <td className="px-3"><Badge tone={c.status === "pending_approval" ? "pending" : "connected"}>{c.status.replace("_", " ")}</Badge></td>
              <td className="px-3"><Button size="sm" variant="ghost" onClick={() => setShift(c.id)}>View / print</Button></td>
            </tr>
          ))}
        </Table>
      </Card>
      {shift && <ShiftReport id={shift} ns="portal" onClose={() => setShift(null)} />}
    </div>
  );
}
