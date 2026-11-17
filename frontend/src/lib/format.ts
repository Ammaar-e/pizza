export const money = (cents: number | null | undefined, currency = "CAD") =>
  cents === null || cents === undefined
    ? "—"
    : new Intl.NumberFormat("en-CA", { style: "currency", currency }).format(cents / 100);

export const dt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
export const time = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";

export const SOURCE_LABEL: Record<string, string> = {
  pos: "POS",
  ubereats: "Uber Eats",
  doordash: "DoorDash",
  skip: "SkipTheDishes",
  call_centre: "Call centre",
  external_api: "Web/API",
  android: "Android",
  ios: "iOS",
  other: "Other",
};

export const STATUS_TONE: Record<string, string> = {
  received: "bg-slate-100 text-slate-700",
  pending_review: "bg-amber-100 text-amber-800",
  accepted: "bg-blue-100 text-blue-800",
  preparing: "bg-indigo-100 text-indigo-800",
  ready: "bg-emerald-100 text-emerald-800",
  out_for_delivery: "bg-cyan-100 text-cyan-800",
  completed: "bg-green-100 text-green-800",
  rejected: "bg-red-100 text-red-800",
  cancelled: "bg-zinc-200 text-zinc-700",
  connected: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
  not_configured: "bg-slate-100 text-slate-700",
  pending_access: "bg-amber-100 text-amber-800",
  disabled: "bg-zinc-200 text-zinc-700",
  failed: "bg-red-100 text-red-800",
  synced: "bg-green-100 text-green-800",
  pending: "bg-amber-100 text-amber-800",
  succeeded: "bg-green-100 text-green-800",
  processed: "bg-green-100 text-green-800",
};

export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const uuid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
