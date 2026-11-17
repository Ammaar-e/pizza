"use client";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, ErrorBanner, Field, Input, Modal, Notice, cx } from "@/components/ui";
import { ApiError, Session, api, loadSession, saveSession, wsUrl } from "@/lib/api";
import { money, time } from "@/lib/format";

const dApi = api("driver");

export default function DriverApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setSession(loadSession("driver"));
    setReady(true);
    const out = () => setSession(null);
    window.addEventListener("pzp-logout-driver", out);
    return () => window.removeEventListener("pzp-logout-driver", out);
  }, []);
  if (!ready) return null;
  if (!session) return <Login onReady={setSession} />;
  return <Home session={session} onLogout={() => { saveSession("driver", null); setSession(null); }} />;
}

function Login({ onReady }: { onReady: (s: Session) => void }) {
  const [f, setF] = useState({ store: "", username: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await dApi.raw<any>("/auth/pos/login/", { method: "POST", auth: false,
        body: { store_number: f.store.trim(), username: f.username.trim(), password: f.password } });
      if (!r.user.permissions.includes("delivery.access")) throw new Error("This sign-in is for delivery drivers");
      const s = { access: r.tokens.access, refresh: r.tokens.refresh, user: r.user, store: r.store };
      saveSession("driver", s);
      onReady(s);
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : String((err as Error).message || err));
    }
  }
  return (
    <main className="flex min-h-screen items-center justify-center bg-ink-900 p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-lg">
        <div><div className="text-xs font-semibold uppercase tracking-widest text-brand-600">Pizza Network</div><h1 className="text-2xl font-bold">Driver sign in</h1></div>
        <ErrorBanner error={error} />
        <Field label="Store number"><Input className="touch-target text-lg" inputMode="numeric" value={f.store} onChange={(e) => setF({ ...f, store: e.target.value })} required /></Field>
        <Field label="Username"><Input className="touch-target text-lg" autoCapitalize="none" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required /></Field>
        <Field label="Password"><Input className="touch-target text-lg" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required /></Field>
        <Button size="xl" className="w-full">Sign in</Button>
      </form>
    </main>
  );
}

function position(): Promise<{ latitude?: number; longitude?: number }> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve({});
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ latitude: Number(p.coords.latitude.toFixed(6)), longitude: Number(p.coords.longitude.toFixed(6)) }),
      () => resolve({}), { timeout: 4000, maximumAge: 60000 });
  });
}

function Home({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [me, setMe] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<any>(null);
  const load = useCallback(async () => {
    try { setMe(await dApi.get("/driver/me/")); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  }, []);
  useEffect(() => {
    load();
    let ws: WebSocket | null = null;
    let stop = false;
    let t: any;
    const connect = () => {
      ws = new WebSocket(wsUrl("/ws/driver/", "driver"));
      ws.onmessage = () => load();
      ws.onclose = () => { if (!stop) t = setTimeout(connect, 5000); };
    };
    connect();
    const poll = setInterval(load, 30000);
    return () => { stop = true; clearTimeout(t); clearInterval(poll); ws?.close(); };
  }, [load]);

  async function setStatus(status: string) {
    try { await dApi.post("/driver/me/", { status }); load(); } catch (e) { setError(e instanceof ApiError ? e.describe() : String(e)); }
  }
  if (!me) return <div className="p-6 text-slate-500">{error || "Loading…"}</div>;
  const d = me.driver;
  return (
    <div className="min-h-screen bg-slate-100 pb-8">
      <header className="sticky top-0 z-10 flex items-center justify-between bg-ink-900 px-4 py-3 text-white">
        <div><div className="text-xs text-slate-400">Store {session.store?.number}</div><div className="font-bold">{d.name}</div></div>
        <button className="text-sm underline" onClick={onLogout}>Sign out</button>
      </header>
      <div className="space-y-4 p-4">
        <ErrorBanner error={error} />
        <div className="rounded-2xl bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between"><span className="font-semibold">My status</span><Badge tone={d.status === "available" ? "connected" : "pending"}>{d.status.replace("_", " ")}</Badge></div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {[["available", "Available"], ["on_break", "On break"], ["unavailable", "Unavailable"], ["off_shift", "End shift"]].map(([s, l]) => (
              <button key={s} disabled={d.status === s} onClick={() => setStatus(s)} className={cx("touch-target rounded-xl border-2 font-semibold", d.status === s ? "border-brand-600 bg-brand-50" : "border-slate-200 bg-white")}>{l}</button>
            ))}
          </div>
        </div>
        <h2 className="text-lg font-bold">My deliveries ({me.active.length})</h2>
        {me.active.length === 0 && <Notice>No deliveries assigned to you right now.</Notice>}
        {me.active.map((a: any) => (
          <button key={a.id} onClick={() => setOpen(a)} className={cx("block w-full rounded-2xl bg-white p-4 text-left shadow-sm", a.status === "failed" && "ring-2 ring-amber-400")}>
            <div className="flex justify-between"><span className="text-xl font-bold">#{a.order_number}</span><Badge tone={a.status === "assigned" ? "pending" : "accepted"}>{a.status.replace(/_/g, " ")}</Badge></div>
            <div className="mt-1 font-medium">{a.address?.line1}</div>
            <div className="text-sm text-slate-500">{a.customer?.name} {a.estimated_delivery_at && `· ETA ${time(a.estimated_delivery_at)}`}</div>
            {a.amount_due_cents > 0 && <div className="mt-1 text-sm font-semibold text-amber-700">Collect {money(a.amount_due_cents)}</div>}
          </button>
        ))}
        {me.recent.length > 0 && (
          <div className="rounded-2xl bg-white p-4 text-sm shadow-sm">
            <h3 className="mb-2 font-semibold">Recent</h3>
            {me.recent.map((r: any) => <div key={r.id} className="flex justify-between border-b py-1"><span>#{r.order_number} {r.address?.line1}</span><span>{r.status}</span></div>)}
          </div>
        )}
      </div>
      {open && <DeliverySheet delivery={open} onClose={() => { setOpen(null); load(); }} />}
    </div>
  );
}

function DeliverySheet({ delivery, onClose }: { delivery: any; onClose: () => void }) {
  const [a, setA] = useState(delivery);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [recipient, setRecipient] = useState("");
  const [photo, setPhoto] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function status(s: string, extra: any = {}) {
    setBusy(true);
    setError(null);
    try {
      setA(await dApi.post(`/driver/deliveries/${a.id}/status/`, { status: s, note, ...extra, ...(await position()) }));
      setNote("");
      if (["delivered", "failed"].includes(s)) onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function saveProof() {
    if (!photo) return;
    try {
      await dApi.post(`/driver/deliveries/${a.id}/proof/`, { kind: "photo", image_data: photo, recipient_name: recipient, ...(await position()) });
      setError(null);
      setPhoto(null);
      alert("Proof of delivery saved");
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }
  function onFile(f: File | undefined) {
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => setPhoto(String(reader.result));
    reader.readAsDataURL(f);
  }
  return (
    <Modal open title={`Delivery #${a.order_number}`} onClose={onClose}>
      <div className="space-y-4">
        <ErrorBanner error={error} />
        <div>
          <div className="text-lg font-semibold">{a.address?.line1}</div>
          <div className="text-sm text-slate-600">{a.address?.city} {a.address?.postal_code}</div>
          {a.delivery_instructions && <div className="mt-1 rounded bg-amber-50 p-2 text-sm">{a.delivery_instructions}</div>}
          <a href={a.maps_url} target="_blank" rel="noreferrer" className="mt-2 inline-block rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white">Open directions</a>
        </div>
        <div className="text-sm"><b>{a.customer?.name}</b> {a.customer?.phone && <a className="ml-2 text-blue-700 underline" href={`tel:${a.customer.phone}`}>{a.customer.phone}</a>}</div>
        <ul className="rounded-lg bg-slate-50 p-2 text-sm">{a.items.map((i: any, k: number) => <li key={k}>{i.quantity}× {i.name}</li>)}</ul>
        {a.amount_due_cents > 0 && <Notice tone="warn">Collect payment: <b>{money(a.amount_due_cents)}</b></Notice>}
        {a.special_instructions && <div className="text-sm">Notes: {a.special_instructions}</div>}
        <Input placeholder="Delivery note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        {a.status === "assigned" && <Button size="xl" className="w-full" disabled={busy} onClick={() => status("picked_up")}>Picked up from store</Button>}
        {a.status === "picked_up" && <Button size="xl" variant="secondary" className="w-full" disabled={busy} onClick={() => status("out_for_delivery")}>On my way</Button>}
        {["picked_up", "out_for_delivery"].includes(a.status) && (
          <>
            {a.proof_required !== "none" && <Notice>Proof of delivery required: {a.proof_required}</Notice>}
            <div className="space-y-2 rounded-lg border p-3">
              <div className="text-sm font-semibold">Proof of delivery</div>
              <input type="file" accept="image/*" capture="environment" onChange={(e) => onFile(e.target.files?.[0])} />
              {photo && <img src={photo} alt="Proof preview" className="max-h-40 rounded" />}
              <Input placeholder="Received by (name)" value={recipient} onChange={(e) => setRecipient(e.target.value)} />
              <Button size="sm" variant="secondary" disabled={!photo} onClick={saveProof}>Save proof</Button>
            </div>
            <Button size="xl" variant="success" className="w-full" disabled={busy} onClick={() => status("delivered")}>Delivered</Button>
            <div className="space-y-2 rounded-lg border border-red-200 p-3">
              <Input placeholder="Reason delivery failed" value={reason} onChange={(e) => setReason(e.target.value)} />
              <Button variant="danger" className="w-full" disabled={busy || !reason} onClick={() => status("failed", { failure_reason: reason })}>Report failed delivery</Button>
            </div>
          </>
        )}
        {a.status === "failed" && <Notice tone="warn">Reported failed: {a.failure_reason}. Return to the store; dispatch will re-dispatch or close it.</Notice>}
      </div>
    </Modal>
  );
}
