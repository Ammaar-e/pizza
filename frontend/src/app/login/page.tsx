"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Button, ErrorBanner, Field, Input } from "@/components/ui";
import { ApiError, api, saveSession } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api("portal").raw<any>("/auth/login/", { method: "POST", body: { username, password }, auth: false });
      saveSession("portal", { access: r.tokens.access, refresh: r.tokens.refresh, user: r.user });
      const level = r.user.role_level;
      if (level === "store") {
        setError("Store employees sign in through the POS or kitchen display.");
        saveSession("portal", null);
        return;
      }
      router.replace(next && next.startsWith(level === "hq" ? "/" : "/franchise") ? next : level === "hq" ? "/hq" : "/franchise");
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : "Unable to reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border bg-white p-8 shadow-sm">
      <div>
        <div className="text-xs font-semibold uppercase tracking-widest text-brand-600">Pizza Network</div>
        <h1 className="text-2xl font-bold">Portal sign in</h1>
        <p className="text-sm text-slate-500">Headquarters and franchise users</p>
      </div>
      <ErrorBanner error={error} />
      <Field label="Username">
        <Input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </Field>
      <Field label="Password">
        <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </Field>
      <Button className="w-full" size="lg" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
