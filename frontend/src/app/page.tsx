import Link from "next/link";

const APPS = [
  { href: "/hq", title: "Headquarters", desc: "Network, franchisees, master menu, providers, reporting" },
  { href: "/franchise", title: "Franchise portal", desc: "Stores, menu & pricing, integrations, staff, reporting" },
  { href: "/pos", title: "Restaurant POS", desc: "Touch POS for counter, phone and delivery orders" },
  { href: "/kds", title: "Kitchen display", desc: "Real-time kitchen tickets by station" },
];

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center p-8">
      <div className="text-sm font-semibold uppercase tracking-widest text-brand-600">Pizza Network</div>
      <h1 className="mt-1 text-3xl font-bold">Restaurant operations platform</h1>
      <p className="mt-2 text-slate-600">Internal system — staff access only.</p>
      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {APPS.map((a) => (
          <Link key={a.href} href={a.href} className="rounded-xl border bg-white p-5 shadow-sm transition hover:border-brand-500 hover:shadow">
            <div className="text-lg font-semibold">{a.title}</div>
            <div className="mt-1 text-sm text-slate-600">{a.desc}</div>
          </Link>
        ))}
      </div>
      <p className="mt-8 text-xs text-slate-500">
        API documentation: <a className="underline" href="/api/v1/docs/">/api/v1/docs/</a>
      </p>
    </main>
  );
}
