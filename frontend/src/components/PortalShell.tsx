"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, createContext, useContext, useEffect, useState } from "react";
import { Session, api, loadSession, saveSession } from "@/lib/api";
import { cx } from "./ui";

const Ctx = createContext<Session | null>(null);
export const usePortalSession = () => useContext(Ctx)!;
export const portalApi = api("portal");

export interface NavItem {
  href: string;
  label: string;
  perm?: string;
}

export default function PortalShell({ title, nav, level, children }: { title: string; nav: NavItem[]; level: "hq" | "franchise"; children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    const s = loadSession("portal");
    const ok = s && (level === "hq" ? s.user.role_level === "hq" : s.user.role_level === "franchise" || s.user.role_level === "hq");
    if (!ok) {
      router.replace(`/login?next=${encodeURIComponent(path)}`);
      return;
    }
    setSession(s);
    const onLogout = () => router.replace("/login");
    window.addEventListener("pzp-logout-portal", onLogout);
    return () => window.removeEventListener("pzp-logout-portal", onLogout);
  }, [level, path, router]);

  if (!session) return <div className="p-10 text-slate-500">Loading…</div>;
  return (
    <Ctx.Provider value={session}>
      <div className="flex min-h-screen bg-slate-50">
        <aside className="hidden w-60 shrink-0 flex-col bg-ink-900 text-slate-200 md:flex">
          <div className="px-5 py-5">
            <div className="text-xs uppercase tracking-widest text-brand-500">Pizza Network</div>
            <div className="text-lg font-bold text-white">{title}</div>
          </div>
          <nav className="flex-1 space-y-0.5 px-2">
            {nav
              .filter((n) => !n.perm || session.user.permissions.includes(n.perm))
              .map((n) => (
                <Link key={n.href} href={n.href} className={cx("block rounded-lg px-3 py-2 text-sm", path === n.href ? "bg-white/10 font-semibold text-white" : "hover:bg-white/5")}>
                  {n.label}
                </Link>
              ))}
          </nav>
          <div className="border-t border-white/10 p-4 text-sm">
            <div className="font-medium text-white">{session.user.name}</div>
            <div className="text-xs text-slate-400">{session.user.role}</div>
            <button
              className="mt-2 text-xs text-brand-500 hover:underline"
              onClick={() => {
                saveSession("portal", null);
                router.replace("/login");
              }}
            >
              Sign out
            </button>
          </div>
        </aside>
        <main className="flex-1 overflow-x-hidden">
          <div className="border-b bg-white px-4 py-2 md:hidden">
            <select className="w-full rounded border px-2 py-1 text-sm" value={path} onChange={(e) => router.push(e.target.value)}>
              {nav.map((n) => (
                <option key={n.href} value={n.href}>{n.label}</option>
              ))}
            </select>
          </div>
          <div className="mx-auto max-w-7xl p-4 md:p-8">{children}</div>
        </main>
      </div>
    </Ctx.Provider>
  );
}

export function PageTitle({ title, sub, actions }: { title: string; sub?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold text-ink-900">{title}</h1>
        {sub && <p className="mt-1 text-sm text-slate-500">{sub}</p>}
      </div>
      <div className="flex gap-2">{actions}</div>
    </div>
  );
}
