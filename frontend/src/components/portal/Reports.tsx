"use client";
import { useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Card, ErrorBanner, Input, Stat, Table } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { SOURCE_LABEL, money } from "@/lib/format";

function Bars({ rows, label, value }: { rows: any[]; label: (r: any) => string; value: (r: any) => number }) {
  const max = Math.max(1, ...rows.map(value));
  return (
    <div className="space-y-2">
      {rows.map((r, i) => (
        <div key={i} className="text-sm">
          <div className="flex justify-between"><span>{label(r)}</span><span className="font-medium">{money(value(r))}</span></div>
          <div className="mt-1 h-2 rounded bg-slate-100"><div className="h-2 rounded bg-brand-500" style={{ width: `${(value(r) / max) * 100}%` }} /></div>
        </div>
      ))}
      {!rows.length && <p className="text-sm text-slate-500">No data.</p>}
    </div>
  );
}

export default function Reports({ storeFilter }: { storeFilter?: { id: number; number: string }[] }) {
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 29 * 864e5).toISOString().slice(0, 10);
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(today);
  const [store, setStore] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = new URLSearchParams({ from, to, ...(store ? { store } : {}) });
    portalApi.get(`/reports/summary/?${q}`).then(setData).catch((e) => setError(e instanceof ApiError ? e.describe() : String(e)));
  }, [from, to, store]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">From <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="text-sm">To <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        {storeFilter && (
          <label className="text-sm">Store
            <select className="block rounded-lg border px-3 py-2 text-sm" value={store} onChange={(e) => setStore(e.target.value)}>
              <option value="">All stores</option>
              {storeFilter.map((s) => <option key={s.id} value={s.id}>{s.number}</option>)}
            </select>
          </label>
        )}
      </div>
      <ErrorBanner error={error} />
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Gross sales" value={money(data.totals.gross_sales_cents)} sub={`Net ${money(data.totals.net_sales_cents)}`} />
            <Stat label="Orders" value={data.totals.order_count} sub={`${data.totals.cancelled_count} cancelled · ${data.totals.rejected_count} rejected`} />
            <Stat label="Average order" value={money(data.totals.average_order_cents)} />
            <Stat label="Discounts / refunds" value={money(data.totals.discount_cents)} sub={`Refunds ${money(data.totals.refund_cents)} (${data.totals.refund_count})`} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Ordering channels"><Bars rows={data.by_channel} label={(r) => `${SOURCE_LABEL[r.source] ?? r.source} (${r.orders})`} value={(r) => r.sales_cents} /></Card>
            <Card title="Payment methods"><Bars rows={data.by_payment_method} label={(r) => `${r.method} (${r.count})`} value={(r) => r.amount_cents} /></Card>
            <Card title="Taxes">
              <ul className="space-y-1 text-sm">
                {Object.entries(data.tax_breakdown).map(([k, v]) => <li key={k} className="flex justify-between"><span>{k}</span><span>{money(v as number)}</span></li>)}
                <li className="flex justify-between border-t pt-1 font-semibold"><span>Total tax</span><span>{money(data.totals.tax_cents)}</span></li>
              </ul>
            </Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Product performance">
              <Table head={["Product", "Qty", "Sales"]} empty={!data.top_products.length}>
                {data.top_products.map((p: any) => (
                  <tr key={p.sku + p.name}><td className="px-3 py-1.5">{p.name}</td><td className="px-3">{p.quantity}</td><td className="px-3">{money(p.sales_cents)}</td></tr>
                ))}
              </Table>
            </Card>
            <Card title="Store comparison">
              <Table head={["Store", "Franchisee", "Orders", "Sales", "Avg", "3rd-party"]} empty={!data.by_store.length}>
                {data.by_store.map((s: any) => (
                  <tr key={s.store_id}><td className="px-3 py-1.5">{s.store__number}</td><td className="px-3">{s.franchisee__code}</td><td className="px-3">{s.orders}</td><td className="px-3">{money(s.sales_cents)}</td><td className="px-3">{money(Math.round(s.avg_cents))}</td><td className="px-3">{s.third_party}</td></tr>
                ))}
              </Table>
            </Card>
          </div>
          <Card title="Employee performance">
            <Table head={["Employee", "Orders", "Sales", "Average", "Discounts given"]} empty={!data.by_employee.length}>
              {data.by_employee.map((e: any) => (
                <tr key={e.created_by__username}><td className="px-3 py-1.5">{e.created_by__username}</td><td className="px-3">{e.orders}</td><td className="px-3">{money(e.sales_cents)}</td><td className="px-3">{money(Math.round(e.avg_cents))}</td><td className="px-3">{money(e.discounts_cents)}</td></tr>
              ))}
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}
