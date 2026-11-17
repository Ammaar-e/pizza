"use client";
import { useState } from "react";
import { Button, ErrorBanner, Field, Input, Notice } from "@/components/ui";
import { ApiError, DEVICE_KEY, Namespace, Session, api, deviceInfo, saveSession } from "@/lib/api";

/** POS / KDS activation: store number + employee username + password (or PIN on a registered device). */
export default function Activation({ ns, title, deviceType, onReady }: { ns: Namespace; title: string; deviceType: "pos" | "kds"; onReady: (s: Session) => void }) {
  const dev = deviceInfo();
  const [storeNumber, setStoreNumber] = useState(dev ? String(dev.store_number ?? "") : "");
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [usePin, setUsePin] = useState(!!dev);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Session | null>(null);
  const [deviceName, setDeviceName] = useState(deviceType === "pos" ? "Front counter" : "Kitchen screen");

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api(ns).raw<any>("/auth/pos/login/", {
        method: "POST", auth: false,
        body: { store_number: storeNumber.trim(), username: username.trim(), ...(usePin ? { pin: secret } : { password: secret }) },
        headers: dev ? { "X-Device-Token": dev.token } : {},
      });
      const s: Session = { access: r.tokens.access, refresh: r.tokens.refresh, user: r.user, store: r.store };
      saveSession(ns, s);
      if (!dev || dev.store !== r.store.id) setPending(s);
      else onReady(s);
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : "Cannot reach the central server. Check the network connection.");
    } finally {
      setBusy(false);
    }
  }

  async function register() {
    if (!pending) return;
    setError(null);
    try {
      const d = await api(ns).post("/devices/", { store: pending.store!.id, device_type: deviceType, name: deviceName });
      localStorage.setItem(DEVICE_KEY, JSON.stringify({ token: d.device_token, id: d.id, name: d.name, store: pending.store!.id, store_number: pending.store!.number, type: deviceType }));
      onReady(pending);
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : String(err));
    }
  }

  if (pending) {
    const canRegister = pending.user.permissions.includes("org.manage_devices");
    return (
      <div className="w-full max-w-md space-y-4 rounded-2xl bg-white p-8 shadow-lg">
        <h1 className="text-2xl font-bold">Register this {deviceType === "pos" ? "terminal" : "display"}</h1>
        <p className="text-sm text-slate-600">Store {pending.store!.number} · {pending.store!.name}</p>
        <ErrorBanner error={error} />
        {canRegister ? (
          <>
            <Field label="Device name"><Input value={deviceName} onChange={(e) => setDeviceName(e.target.value)} /></Field>
            <Button size="lg" className="w-full" onClick={register}>Register device</Button>
          </>
        ) : (
          <Notice tone="warn">This device is not registered to store {pending.store!.number}. A manager must sign in once to register it. You can continue in limited mode (no cash drawer, no offline queue).</Notice>
        )}
        <Button variant="secondary" size="lg" className="w-full" onClick={() => onReady(pending)}>Continue without registering</Button>
      </div>
    );
  }

  return (
    <form onSubmit={login} className="w-full max-w-md space-y-5 rounded-2xl bg-white p-8 shadow-lg">
      <div>
        <div className="text-xs font-semibold uppercase tracking-widest text-brand-600">Pizza Network</div>
        <h1 className="text-3xl font-bold">{title}</h1>
        {dev && <p className="mt-1 text-sm text-slate-500">Registered device: {dev.name}</p>}
      </div>
      <ErrorBanner error={error} />
      <Field label="Store number"><Input className="touch-target text-lg" inputMode="numeric" value={storeNumber} onChange={(e) => setStoreNumber(e.target.value)} required /></Field>
      <Field label="Employee username"><Input className="touch-target text-lg" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} required /></Field>
      <Field label={usePin ? "PIN" : "Password"}>
        <Input className="touch-target text-lg" type="password" inputMode={usePin ? "numeric" : undefined} value={secret} onChange={(e) => setSecret(e.target.value)} required />
      </Field>
      {dev && <button type="button" className="text-sm text-brand-600 underline" onClick={() => setUsePin(!usePin)}>Use {usePin ? "password" : "PIN"} instead</button>}
      <Button size="xl" className="w-full" disabled={busy}>{busy ? "Validating…" : "Sign in"}</Button>
    </form>
  );
}
