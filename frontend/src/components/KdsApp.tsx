"use client";
import { useCallback, useEffect, useState } from "react";
import Activation from "@/components/pos/Activation";
import { Badge, Button, cx } from "@/components/ui";
import { ApiError, Session, api, loadSession, saveSession, wsUrl } from "@/lib/api";
import { SOURCE_LABEL } from "@/lib/format";
import { useDeviceAgent } from "@/lib/device";

const kdsApi = api("kds");
const NEXT: Record<string, { to: string; label: string; cls: string }> = {
  received: { to: "preparing", label: "Start", cls: "bg-indigo-600" },
  preparing: { to: "ready", label: "Ready", cls: "bg-emerald-600" },
  ready: { to: "completed", label: "Complete", cls: "bg-ink-800" },
};

export default function KdsApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setSession(loadSession("kds"));
    setReady(true);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    const out = () => setSession(null);
    window.addEventListener("pzp-logout-kds", out);
    return () => window.removeEventListener("pzp-logout-kds", out);
  }, []);
  if (!ready) return null;
  if (!session?.store)
    return (
      <main className="flex min-h-screen items-center justify-center bg-black p-4">
        <Activation ns="kds" title="Kitchen display" deviceType="kds" onReady={setSession} />
      </main>
    );
  return <Board session={session} onLogout={() => { saveSession("kds", null); setSession(null); }} />;
}

function elapsed(iso: string, now: number) {
  const s = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Board({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const store = session.store!;
  const [tickets, setTickets] = useState<any[]>([]);
  const [stations, setStations] = useState<any[]>([]);
  const [station, setStation] = useState<string>(() => (typeof window !== "undefined" && localStorage.getItem("pzp.kds.station")) || "");
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  useDeviceAgent({ appStatus: "kds", syncStatus: () => (navigator.onLine ? "ok" : "offline"), pending: () => 0,
                   username: session.user.username });

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams({ store: String(store.id), ...(station ? { station } : {}), ...(showDone ? { include_completed: "1" } : {}) });
      setTickets(await kdsApi.get(`/kitchen/tickets/?${q}`));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : "Connection to central server lost");
    }
  }, [store.id, station, showDone]);

  useEffect(() => {
    kdsApi.get(`/kitchen/stations/?store=${store.id}`).then(setStations).catch(() => undefined);
  }, [store.id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let timer: any;
    const connect = () => {
      ws = new WebSocket(wsUrl(`/ws/stores/${store.id}/kds/`, "kds"));
      ws.onopen = () => { setLive(true); load(); };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        const t = msg.ticket;
        if (!t || (station && t.station !== station)) return;
        setTickets((prev) => {
          const rest = prev.filter((x) => x.id !== t.id);
          if (t.status === "void" || (!showDone && t.status === "completed")) return rest;
          return [...rest, t].sort((a, b) => a.received_at.localeCompare(b.received_at));
        });
      };
      ws.onclose = () => { setLive(false); if (!stop) timer = setTimeout(connect, 3000); };
    };
    connect();
    const poll = setInterval(load, 20000); // safety net if a WebSocket message is missed
    return () => { stop = true; clearTimeout(timer); clearInterval(poll); ws?.close(); };
  }, [store.id, station, showDone, load]);

  async function bump(t: any, to: string) {
    try {
      const updated = await kdsApi.post(`/kitchen/tickets/${t.id}/bump/`, { status: to });
      setTickets((prev) => (to === "completed" && !showDone ? prev.filter((x) => x.id !== t.id) : prev.map((x) => (x.id === t.id ? updated : x))));
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-4">
          <div className="text-xl font-bold">Kitchen · Store {store.number}</div>
          <select className="rounded bg-zinc-800 px-3 py-2" value={station} onChange={(e) => { setStation(e.target.value); localStorage.setItem("pzp.kds.station", e.target.value); }}>
            <option value="">All stations</option>
            {stations.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Show completed</label>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className={cx("rounded-full px-3 py-1 font-semibold", live ? "bg-emerald-600" : "bg-red-600")}>{live ? "LIVE" : "RECONNECTING"}</span>
          <span>{tickets.length} tickets</span>
          <Button size="sm" variant="secondary" onClick={onLogout}>Sign out</Button>
        </div>
      </header>
      {error && <div className="bg-red-900 px-4 py-2 text-sm">{error}</div>}
      <main className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
        {tickets.map((t) => {
          const age = (now - new Date(t.received_at).getTime()) / 60000;
          const next = NEXT[t.status];
          return (
            <article key={t.id} className={cx("flex flex-col rounded-xl border-t-8 bg-zinc-900", age > 20 ? "border-red-500" : age > 10 ? "border-amber-400" : "border-emerald-500")}>
              <header className="flex items-start justify-between p-3">
                <div>
                  <div className="text-3xl font-black">#{t.order_number}</div>
                  <div className="text-xs text-zinc-400">Store {t.store_number} · {t.station}</div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-2xl">{elapsed(t.received_at, now)}</div>
                  <Badge tone={t.status}>{t.status}</Badge>
                </div>
              </header>
              <div className="flex flex-wrap gap-1 px-3 text-xs">
                <span className="rounded bg-zinc-800 px-2 py-0.5">{SOURCE_LABEL[t.source] ?? t.source}{t.external_display_id && ` ${t.external_display_id}`}</span>
                <span className="rounded bg-zinc-800 px-2 py-0.5 uppercase">{t.order_type.replace("_", " ")}</span>
                {t.customer_name && <span className="rounded bg-zinc-800 px-2 py-0.5">{t.customer_name}</span>}
              </div>
              <ul className="flex-1 space-y-2 p-3">
                {t.items.map((it: any) => (
                  <li key={it.id} className="border-b border-zinc-800 pb-2">
                    <div className="text-lg font-bold">{it.quantity}× {it.size} {it.name}{!it.is_mapped && <span className="text-amber-400"> (unmapped)</span>}</div>
                    {it.crust && <div className="text-sm text-zinc-300">{it.crust} crust</div>}
                    <ul className="text-sm">
                      {it.modifiers.filter((m: any) => !(m.is_default && m.quantity === 1 && m.placement === "whole")).map((m: any, i: number) => (
                        <li key={i} className={cx(m.placement !== "whole" && "text-sky-300")}>{m.quantity > 1 ? "EXTRA " : "+ "}{m.name}{m.placement !== "whole" && ` — ${m.placement.toUpperCase()} HALF`}</li>
                      ))}
                      {it.removed.map((r: string) => <li key={r} className="font-semibold text-red-400">NO {r}</li>)}
                    </ul>
                    {it.special_instructions && <div className="mt-1 rounded bg-amber-500/20 px-2 py-1 text-sm text-amber-200">{it.special_instructions}</div>}
                  </li>
                ))}
              </ul>
              {t.special_instructions && <div className="mx-3 mb-2 rounded bg-amber-500/20 px-2 py-1 text-sm text-amber-200">Order: {t.special_instructions}</div>}
              <footer className="grid grid-cols-2 gap-2 p-3">
                {t.status !== "received" && t.status !== "completed" && <button className="rounded-lg bg-zinc-700 py-4 font-semibold" onClick={() => bump(t, t.status === "ready" ? "preparing" : "received")}>Back</button>}
                {next && <button className={cx("col-span-1 rounded-lg py-4 text-lg font-bold", next.cls, t.status === "received" && "col-span-2")} onClick={() => bump(t, next.to)}>{next.label}</button>}
              </footer>
            </article>
          );
        })}
        {!tickets.length && <p className="col-span-full py-20 text-center text-2xl text-zinc-600">No open tickets</p>}
      </main>
    </div>
  );
}
