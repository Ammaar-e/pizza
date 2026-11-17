"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, ErrorBanner, Input, Notice, Select, Toggle, cx } from "@/components/ui";
import { ApiError, Session, api, deviceInfo, loadSession, saveSession, wsUrl } from "@/lib/api";
import { SOURCE_LABEL, money, time, uuid } from "@/lib/format";
import { QueuedOrder, cacheGet, cachePut, dequeue, enqueue, queued } from "@/lib/offline";
import { CartLine, MenuProduct, describeLine, estimateUnit, lineToApi } from "@/lib/pricing";
import Activation from "./Activation";
import Customizer from "./Customizer";
import OrderDetail, { printReceipt } from "./OrderDetail";
import PaymentModal from "./PaymentModal";

const posApi = api("pos");
type Tab = "order" | "orders" | "delivery" | "cash" | "availability";
const THIRD_PARTY = ["ubereats", "doordash", "skip"];

export default function PosApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setSession(loadSession("pos"));
    setReady(true);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    const out = () => setSession(null);
    window.addEventListener("pzp-logout-pos", out);
    return () => window.removeEventListener("pzp-logout-pos", out);
  }, []);
  if (!ready) return null;
  if (!session?.store)
    return (
      <main className="flex min-h-screen items-center justify-center bg-ink-900 p-4">
        <Activation ns="pos" title="POS activation" deviceType="pos" onReady={setSession} />
      </main>
    );
  return <Terminal session={session} onLogout={() => { saveSession("pos", null); setSession(null); }} />;
}

function Terminal({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const store = session.store!;
  const can = useCallback((p: string) => session.user.permissions.includes(p), [session]);
  const device = deviceInfo();
  const [tab, setTab] = useState<Tab>("order");
  const [online, setOnline] = useState(true);
  const [wsState, setWsState] = useState<"connecting" | "live" | "down">("connecting");
  const [menu, setMenu] = useState<any>(null);
  const [menuStale, setMenuStale] = useState(false);
  const [category, setCategory] = useState<string | null>(null);
  const [customizing, setCustomizing] = useState<{ product: MenuProduct; line?: CartLine } | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [orderType, setOrderType] = useState<"pickup" | "delivery" | "dine_in">("pickup");
  const [entry, setEntry] = useState<"walk_in" | "phone">("walk_in");
  const [customer, setCustomer] = useState({ name: "", phone: "" });
  const [address, setAddress] = useState({ line1: "", city: store.city, postal_code: "", instructions: "" });
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [payOrder, setPayOrder] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [detail, setDetail] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueuedOrder[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [alertCount, setAlertCount] = useState(0);

  // ---- menu (cached encrypted for offline use) ---------------------------------------
  const loadMenu = useCallback(async () => {
    try {
      const m = await posApi.get(`/stores/${store.id}/menu/`);
      setMenu(m);
      setMenuStale(false);
      await cachePut(`menu:${store.id}`, m);
      await cachePut(`store:${store.id}`, store);
      setOnline(true);
    } catch (e) {
      if (!(e instanceof ApiError)) {
        setOnline(false);
        const cached = await cacheGet<any>(`menu:${store.id}`);
        if (cached) {
          setMenu(cached);
          setMenuStale(true);
        }
      }
    }
  }, [store]);

  const loadOrders = useCallback(async () => {
    try {
      const r = await posApi.get(`/orders/?store=${store.id}&date=today&page_size=200`);
      setOrders(r.results);
      setAlertCount(r.results.filter((o: any) => o.requires_attention && !["cancelled", "rejected", "completed"].includes(o.status)).length);
      setOnline(true);
    } catch (e) {
      if (!(e instanceof ApiError)) setOnline(false);
    }
  }, [store.id]);

  const refreshQueue = useCallback(async () => setQueue(await queued()), []);

  useEffect(() => {
    loadMenu();
    loadOrders();
    refreshQueue();
  }, [loadMenu, loadOrders, refreshQueue]);

  // ---- real-time feed ---------------------------------------------------------------------
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let timer: any;
    const connect = () => {
      setWsState("connecting");
      ws = new WebSocket(wsUrl(`/ws/stores/${store.id}/pos/`, "pos"));
      ws.onopen = () => setWsState("live");
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.event?.startsWith("order.")) {
          loadOrders();
          if (THIRD_PARTY.includes(msg.order?.source) && msg.order?.status === "pending_review") {
            try { new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=").play(); } catch { /* no audio */ }
          }
        }
      };
      ws.onclose = () => {
        setWsState("down");
        if (!stop) timer = setTimeout(connect, 5000);
      };
    };
    connect();
    const poll = setInterval(loadOrders, 30000);
    return () => {
      stop = true;
      clearTimeout(timer);
      clearInterval(poll);
      ws?.close();
    };
  }, [store.id, loadOrders]);

  // ---- offline queue sync ------------------------------------------------------------------
  const syncQueue = useCallback(async () => {
    const items = await queued();
    if (!items.length || !device) return;
    try {
      const r = await posApi.post("/pos/sync/", {
        orders: items.map((q) => ({ offline_client_ref: q.ref, order: q.order, payments: q.payments })),
      });
      for (const res of r.results) {
        if (res.status === "created" || res.status === "duplicate") await dequeue(res.offline_client_ref);
        else {
          const q = items.find((i) => i.ref === res.offline_client_ref);
          if (q) await enqueue({ ...q, status: "error", error: typeof res.error === "string" ? res.error : JSON.stringify(res.error) });
        }
      }
      setOnline(true);
      refreshQueue();
      loadOrders();
    } catch (e) {
      if (!(e instanceof ApiError)) setOnline(false);
    }
  }, [device, refreshQueue, loadOrders]);

  useEffect(() => {
    const t = setInterval(syncQueue, 15000);
    const on = () => { setOnline(true); syncQueue(); loadMenu(); };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => { clearInterval(t); window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, [syncQueue, loadMenu]);

  // ---- cart -------------------------------------------------------------------------------
  const products: MenuProduct[] = useMemo(() => (menu?.categories ?? []).flatMap((c: any) => c.products), [menu]);
  const visible = useMemo(() => {
    const cats = menu?.categories ?? [];
    const cat = cats.find((c: any) => c.code === (category ?? cats[0]?.code));
    return cat?.products ?? [];
  }, [menu, category]);
  const subtotal = cart.reduce((s, l) => s + estimateUnit(l) * l.quantity, 0);

  function addProduct(p: MenuProduct) {
    if (!p.is_available) return;
    if (p.sizes.length || p.crusts.length || p.modifier_groups.length) setCustomizing({ product: p });
    else setCart([...cart, { key: uuid(), product: p, quantity: 1, modifiers: [], removed: [], special_instructions: "" }]);
  }

  function resetOrder() {
    setCart([]);
    setCustomer({ name: "", phone: "" });
    setAddress({ line1: "", city: store.city, postal_code: "", instructions: "" });
    setNotes("");
    setEntry("walk_in");
    setOrderType("pickup");
  }

  function buildOrder() {
    return {
      store_id: store.id,
      order_type: orderType,
      entry_channel: entry,
      items: cart.map(lineToApi),
      customer: customer.name || customer.phone ? customer : undefined,
      delivery_address: orderType === "delivery" ? address : undefined,
      special_instructions: notes,
    };
  }

  async function submit(payNow: boolean) {
    if (!cart.length) return;
    setError(null);
    setNotice(null);
    setSubmitting(true);
    const order = buildOrder();
    const ref = `${device?.id ?? "x"}-${uuid()}`;
    try {
      const r = await posApi.post("/orders/", order, { "Idempotency-Key": ref });
      resetOrder();
      setOnline(true);
      loadOrders();
      if (payNow) setPayOrder(r);
      else setNotice(`Order #${r.order_number} sent to kitchen — unpaid`);
    } catch (e) {
      if (e instanceof ApiError) setError(e.describe());
      else if (!device) setError("Offline and this terminal is not registered — cannot queue orders.");
      else {
        // Network failure: queue locally (encrypted). Clearly NOT accepted centrally yet.
        let payments: QueuedOrder["payments"] = [];
        if (payNow) {
          // Offline: cash only. The exact total (incl. tax) is applied by the server when the order syncs.
          const tendered = Number(prompt(`OFFLINE — cash only.\nSubtotal before tax ${money(subtotal)}.\nCash tendered (cents):`) || 0);
          if (tendered > 0) payments = [{ method: "cash", tendered_cents: tendered }];
        }
        await enqueue({ ref, createdAt: new Date().toISOString(), order, payments, status: "queued", summary: { total_cents: subtotal, items: cart.length, customer: customer.name } });
        setOnline(false);
        resetOrder();
        refreshQueue();
        setNotice("Central server unreachable: order QUEUED LOCALLY. It is not yet accepted and will sync automatically.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const statusPill = (
    <div className="flex items-center gap-2 text-xs">
      <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-1 font-semibold", online ? "bg-emerald-500/20 text-emerald-200" : "bg-red-500/30 text-red-100")}>
        <span className={cx("h-2 w-2 rounded-full", online ? "bg-emerald-400" : "bg-red-400")} />{online ? "Online" : "OFFLINE"}
      </span>
      <span className={cx("rounded-full px-2 py-1", wsState === "live" ? "bg-white/10" : "bg-amber-500/30")}>Live feed: {wsState}</span>
      {queue.length > 0 && <span className="rounded-full bg-amber-500 px-2 py-1 font-bold text-ink-900">{queue.length} queued</span>}
      {menuStale && <span className="rounded-full bg-amber-500/30 px-2 py-1">cached menu</span>}
    </div>
  );

  const tabs: [Tab, string, number?][] = [
    ["order", "New order"],
    ["orders", "Orders"],
    ["delivery", "Delivery apps", alertCount],
    ["cash", "Cash drawer"],
    ["availability", "86 items"],
  ];

  return (
    <div className="flex h-screen flex-col bg-slate-100">
      <header className="flex items-center justify-between gap-4 bg-ink-900 px-4 py-2 text-white">
        <div className="flex items-center gap-4">
          <div><div className="text-xs text-slate-400">Store {store.number}</div><div className="font-bold">{store.name}</div></div>
          <nav className="flex gap-1">
            {tabs.map(([k, label, badge]) => (
              <button key={k} onClick={() => setTab(k)} className={cx("relative rounded-lg px-4 py-3 text-sm font-semibold", tab === k ? "bg-white text-ink-900" : "hover:bg-white/10")}>
                {label}
                {!!badge && <span className="absolute -right-1 -top-1 rounded-full bg-red-500 px-1.5 text-xs text-white">{badge}</span>}
              </button>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-4">
          {statusPill}
          <div className="text-right text-xs"><div className="font-semibold">{session.user.name}</div><div className="text-slate-400">{session.user.role}</div></div>
          <Button variant="secondary" size="sm" onClick={onLogout}>{device ? "Lock" : "Sign out"}</Button>
        </div>
      </header>
      {(error || notice) && (
        <div className="space-y-1 px-4 pt-2">
          <ErrorBanner error={error} />
          {notice && <Notice tone={notice.includes("QUEUED") ? "warn" : "ok"}>{notice}</Notice>}
        </div>
      )}

      {tab === "order" && (
        <div className="flex min-h-0 flex-1">
          <section className="flex min-w-0 flex-1 flex-col">
            <div className="flex gap-2 overflow-x-auto p-3">
              {(menu?.categories ?? []).map((c: any) => (
                <button key={c.code} onClick={() => setCategory(c.code)} className={cx("touch-target whitespace-nowrap rounded-xl px-5 font-semibold", (category ?? menu.categories[0].code) === c.code ? "bg-brand-600 text-white" : "bg-white")}>{c.name}</button>
              ))}
            </div>
            <div className="grid flex-1 auto-rows-min grid-cols-2 gap-3 overflow-y-auto p-3 pt-0 lg:grid-cols-3 xl:grid-cols-4">
              {!menu && <p className="text-slate-500">Loading menu…</p>}
              {visible.map((p: MenuProduct) => (
                <button key={p.sku} onClick={() => addProduct(p)} disabled={!p.is_available} className={cx("flex min-h-[110px] flex-col justify-between rounded-2xl bg-white p-4 text-left shadow-sm active:scale-[0.98]", !p.is_available && "opacity-40")}>
                  <div className="font-semibold leading-tight">{p.name}</div>
                  <div className="text-sm text-slate-500">{p.sizes.length ? `from ${money(Math.min(...p.sizes.map((s) => s.price_cents)))}` : money(p.price_cents)}{!p.is_available && " · unavailable"}</div>
                </button>
              ))}
            </div>
          </section>
          <aside className="flex w-[380px] shrink-0 flex-col border-l bg-white">
            <div className="grid grid-cols-3 gap-1 p-3">
              {(["pickup", "delivery", "dine_in"] as const).map((t) => (
                <button key={t} onClick={() => setOrderType(t)} className={cx("touch-target rounded-lg text-sm font-semibold capitalize", orderType === t ? "bg-ink-900 text-white" : "bg-slate-100")}>{t.replace("_", " ")}</button>
              ))}
            </div>
            <div className="flex items-center gap-2 px-3 text-sm"><Toggle checked={entry === "phone"} onChange={(v) => setEntry(v ? "phone" : "walk_in")} /> Telephone order</div>
            <div className="flex-1 overflow-y-auto px-3 py-2">
              {cart.length === 0 && <p className="py-8 text-center text-slate-400">Tap products to start an order</p>}
              {cart.map((l) => (
                <div key={l.key} className="border-b py-2">
                  <div className="flex justify-between gap-2">
                    <button className="text-left font-medium" onClick={() => setCustomizing({ product: l.product, line: l })}>{l.quantity}× {l.product.sizes.find((s) => s.code === l.size)?.name} {l.product.name}</button>
                    <span className="font-semibold">{money(estimateUnit(l) * l.quantity)}</span>
                  </div>
                  <div className="text-xs text-slate-500">{describeLine(l).join(" · ")}</div>
                  <button className="text-xs text-red-600" onClick={() => setCart(cart.filter((x) => x.key !== l.key))}>Remove</button>
                </div>
              ))}
            </div>
            <div className="space-y-2 border-t p-3">
              <div className="grid grid-cols-2 gap-2">
                <Input placeholder="Customer name" value={customer.name} onChange={(e) => setCustomer({ ...customer, name: e.target.value })} />
                <Input placeholder="Phone" inputMode="tel" value={customer.phone} onChange={(e) => setCustomer({ ...customer, phone: e.target.value })} />
              </div>
              {orderType === "delivery" && (
                <div className="grid grid-cols-2 gap-2">
                  <Input className="col-span-2" placeholder="Street address" value={address.line1} onChange={(e) => setAddress({ ...address, line1: e.target.value })} />
                  <Input placeholder="City" value={address.city} onChange={(e) => setAddress({ ...address, city: e.target.value })} />
                  <Input placeholder="Postal code" value={address.postal_code} onChange={(e) => setAddress({ ...address, postal_code: e.target.value })} />
                </div>
              )}
              <Input placeholder="Order notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
              <div className="flex justify-between text-lg font-bold"><span>Subtotal{!online && " (est.)"}</span><span>{money(subtotal)}</span></div>
              <p className="text-xs text-slate-500">Taxes and fees are calculated by the central server.</p>
              <div className="grid grid-cols-2 gap-2">
                <Button size="xl" variant="secondary" disabled={!cart.length || submitting} onClick={() => submit(false)}>Send (pay later)</Button>
                <Button size="xl" disabled={!cart.length || submitting} onClick={() => submit(true)}>Pay</Button>
              </div>
            </div>
          </aside>
        </div>
      )}

      {tab === "orders" && <OrdersList orders={orders} queue={queue} onOpen={setDetail} onSync={syncQueue} />}
      {tab === "delivery" && <DeliveryApps orders={orders.filter((o) => THIRD_PARTY.includes(o.source))} onOpen={setDetail} />}
      {tab === "cash" && <CashPanel can={can} hasDevice={!!device} />}
      {tab === "availability" && <AvailabilityPanel storeId={store.id} products={products} can={can} onChange={loadMenu} />}

      {customizing && (
        <Customizer product={customizing.product} initial={customizing.line} onClose={() => setCustomizing(null)}
          onAdd={(l) => { setCart(customizing.line ? cart.map((x) => (x.key === l.key ? l : x)) : [...cart, l]); setCustomizing(null); }} />
      )}
      {payOrder && (
        <PaymentModal order={payOrder} onClose={() => { setPayOrder(null); loadOrders(); }}
          onPaid={(r) => { setNotice(`Order #${r.order_number} paid`); printReceipt(r.id).catch(() => undefined); }} />
      )}
      {detail && <OrderDetail orderId={detail} can={can} onClose={() => { setDetail(null); loadOrders(); }} />}
    </div>
  );
}

function OrdersList({ orders, queue, onOpen, onSync }: { orders: any[]; queue: QueuedOrder[]; onOpen: (id: string) => void; onSync: () => void }) {
  const [filter, setFilter] = useState("active");
  const shown = orders.filter((o) => filter === "all" || (filter === "active" ? !["completed", "cancelled", "rejected"].includes(o.status) : o.status === filter));
  return (
    <div className="flex-1 overflow-y-auto p-4">
      {queue.length > 0 && (
        <div className="mb-4 rounded-xl border-2 border-amber-400 bg-amber-50 p-3">
          <div className="flex items-center justify-between"><b>Queued locally — not yet accepted by the central server</b><Button size="sm" onClick={onSync}>Sync now</Button></div>
          <ul className="mt-2 space-y-1 text-sm">
            {queue.map((q) => (
              <li key={q.ref} className="flex justify-between"><span>{new Date(q.createdAt).toLocaleTimeString()} · {q.summary.items} items {q.summary.customer && `· ${q.summary.customer}`}</span>
                <span>{q.status === "error" ? <span className="text-red-700">Rejected on sync: {q.error}</span> : <Badge tone="pending">queued</Badge>}</span></li>
            ))}
          </ul>
        </div>
      )}
      <div className="mb-3 flex gap-2">
        <Select className="w-48" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="active">Active</option><option value="all">All today</option><option value="completed">Completed</option><option value="cancelled">Cancelled</option><option value="rejected">Rejected</option>
        </Select>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {shown.map((o) => (
          <button key={o.id} onClick={() => onOpen(o.id)} className={cx("rounded-xl bg-white p-4 text-left shadow-sm", o.requires_attention && "ring-2 ring-amber-400")}>
            <div className="flex items-center justify-between"><span className="text-xl font-bold">#{o.order_number}</span><Badge tone={o.status}>{o.status.replace("_", " ")}</Badge></div>
            <div className="mt-1 flex flex-wrap gap-1 text-xs"><Badge>{SOURCE_LABEL[o.source] ?? o.source}</Badge><Badge>{o.order_type.replace("_", " ")}</Badge><Badge tone={o.payment_status === "unpaid" ? "pending" : "connected"}>{o.payment_status.replace("_", " ")}</Badge></div>
            <div className="mt-2 flex justify-between text-sm"><span>{o.customer_name || "—"} · {o.item_count} items</span><span className="font-semibold">{money(o.total_cents)}</span></div>
            <div className="text-xs text-slate-500">{time(o.placed_at)}</div>
          </button>
        ))}
        {!shown.length && <p className="text-slate-500">No orders.</p>}
      </div>
    </div>
  );
}

function DeliveryApps({ orders, onOpen }: { orders: any[]; onOpen: (id: string) => void }) {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <Notice>Third-party orders arrive automatically from the provider integrations and are accepted or rejected according to the store&apos;s policy. Use this view to review exceptions, see synchronization status and take permitted manual actions.</Notice>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {orders.map((o) => (
          <button key={o.id} onClick={() => onOpen(o.id)} className={cx("rounded-xl bg-white p-4 text-left shadow-sm", o.requires_attention && "ring-2 ring-amber-400")}>
            <div className="flex items-center justify-between"><span className="text-lg font-bold">{SOURCE_LABEL[o.source]} {o.external_display_id && `· ${o.external_display_id}`}</span><Badge tone={o.status}>{o.status.replace("_", " ")}</Badge></div>
            <div className="mt-1 text-sm">#{o.order_number} · {o.customer_name} · {money(o.total_cents)}</div>
            <div className="mt-1 flex gap-1 text-xs"><Badge tone={o.provider_sync_status}>sync: {o.provider_sync_status}</Badge>{o.entry_channel === "manual_provider" && <Badge>entered manually</Badge>}</div>
            {o.requires_attention && <div className="mt-2 text-xs font-medium text-amber-800">⚠ {o.attention_reason}</div>}
          </button>
        ))}
        {!orders.length && <p className="text-slate-500">No delivery-app orders today.</p>}
      </div>
    </div>
  );
}

function CashPanel({ can, hasDevice }: { can: (p: string) => boolean; hasDevice: boolean }) {
  const [session, setSession] = useState<any>(undefined);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [mgr, setMgr] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => posApi.get("/cash-sessions/current/").then(setSession).catch(() => setSession(null)), []);
  useEffect(() => { load(); }, [load]);
  const run = async (fn: () => Promise<any>) => {
    setError(null);
    try { await fn(); setAmount(""); setReason(""); load(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };
  if (!hasDevice) return <div className="p-6"><Notice tone="warn">Cash drawer requires a registered terminal.</Notice></div>;
  if (session === undefined) return null;
  return (
    <div className="mx-auto w-full max-w-xl space-y-4 p-6">
      <ErrorBanner error={error} />
      {!session ? (
        <div className="space-y-3 rounded-2xl bg-white p-6 shadow-sm">
          <h2 className="text-xl font-bold">Open cash drawer</h2>
          <Input type="number" placeholder="Opening float (cents)" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <Button size="xl" className="w-full" disabled={!can("cash.manage_session")} onClick={() => run(() => posApi.post("/cash-sessions/open/", { opening_float_cents: Number(amount || 0) }))}>Open session</Button>
        </div>
      ) : (
        <div className="space-y-4 rounded-2xl bg-white p-6 shadow-sm">
          <div className="flex justify-between"><h2 className="text-xl font-bold">Drawer open</h2><span className="text-sm text-slate-500">since {time(session.opened_at)} by {session.opened_by_name}</span></div>
          <div className="grid grid-cols-2 gap-3 text-center">
            <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs text-slate-500">Opening float</div><div className="text-xl font-bold">{money(session.opening_float_cents)}</div></div>
            <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs text-slate-500">Expected in drawer</div><div className="text-xl font-bold">{money(session.expected_now_cents)}</div></div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Input type="number" placeholder="Amount (cents)" value={amount} onChange={(e) => setAmount(e.target.value)} />
            <Input placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Input type="password" inputMode="numeric" placeholder="Manager code (if required)" value={mgr} onChange={(e) => setMgr(e.target.value)} />
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => run(() => posApi.post(`/cash-sessions/${session.id}/movements/`, { kind: "paid_in", amount_cents: Number(amount), reason, manager_code: mgr || undefined }))}>Paid in</Button>
              <Button variant="secondary" onClick={() => run(() => posApi.post(`/cash-sessions/${session.id}/movements/`, { kind: "paid_out", amount_cents: Number(amount), reason, manager_code: mgr || undefined }))}>Paid out</Button>
            </div>
          </div>
          <Button size="lg" variant="danger" className="w-full" onClick={() => {
            const counted = prompt("Counted cash in drawer (cents)");
            if (counted !== null) run(() => posApi.post(`/cash-sessions/${session.id}/close/`, { counted_cash_cents: Number(counted) }).then((r) => alert(`Session closed. Expected ${money(r.expected_cash_cents)}, counted ${money(r.counted_cash_cents)}, variance ${money(r.variance_cents)}`)));
          }}>Close & count drawer</Button>
        </div>
      )}
    </div>
  );
}

function AvailabilityPanel({ storeId, products, can, onChange }: { storeId: number; products: MenuProduct[]; can: (p: string) => boolean; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const mods = useMemo(() => {
    const m = new Map<string, { code: string; name: string; is_available: boolean }>();
    products.forEach((p) => p.modifier_groups.forEach((g) => g.modifiers.forEach((x) => m.set(x.code, x))));
    return [...m.values()];
  }, [products]);
  const set = async (body: any) => {
    setError(null);
    try { await posApi.post(`/stores/${storeId}/availability/update/`, body); onChange(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  };
  if (!can("catalog.set_availability")) return <div className="p-6"><Notice tone="warn">You do not have permission to change item availability.</Notice></div>;
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <ErrorBanner error={error} />
      <h2 className="mb-2 text-lg font-bold">Products</h2>
      <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-4">
        {products.map((p) => (
          <div key={p.sku} className="flex items-center justify-between rounded-xl bg-white p-3"><span className={cx(!p.is_available && "text-red-600 line-through")}>{p.name}</span><Toggle checked={p.is_available} onChange={(v) => set({ products: [{ sku: p.sku, is_available: v }] })} /></div>
        ))}
      </div>
      <h2 className="mb-2 mt-6 text-lg font-bold">Toppings & options</h2>
      <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-4">
        {mods.map((m) => (
          <div key={m.code} className="flex items-center justify-between rounded-xl bg-white p-3"><span className={cx(!m.is_available && "text-red-600 line-through")}>{m.name}</span><Toggle checked={m.is_available} onChange={(v) => set({ modifiers: [{ code: m.code, is_available: v }] })} /></div>
        ))}
      </div>
    </div>
  );
}
