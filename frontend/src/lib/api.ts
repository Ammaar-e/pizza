// Thin API client: JWT bearer auth per application namespace, automatic refresh, typed errors.
export type Namespace = "portal" | "pos" | "kds" | "driver";

export interface Session {
  access: string;
  refresh: string;
  user: Profile;
  store?: Store;
}

export interface Profile {
  id: number;
  username: string;
  name: string;
  role: string | null;
  role_level: "hq" | "franchise" | "store" | null;
  franchisee_id: number | null;
  permissions: string[];
  store_ids: number[] | null;
}

export interface Store {
  id: number;
  number: string;
  name: string;
  franchisee: number;
  franchisee_code: string;
  address_line1: string;
  city: string;
  region: string;
  postal_code: string;
  phone: string;
  timezone: string;
  is_active: boolean;
  is_temporarily_closed: boolean;
  is_open_now: boolean;
  currency: string;
  delivery_config: Record<string, any>;
  pickup_config: Record<string, any>;
  ordering_channels: Record<string, boolean>;
  payment_config: Record<string, any>;
  hours: { day_of_week: number; opens_at: string; closes_at: string }[];
  [k: string]: any;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: any;
  errors: any[] | null;
  constructor(status: number, body: any) {
    const err = body?.error ?? {};
    super(err.message || `Request failed (${status})`);
    this.status = status;
    this.code = err.code || "error";
    this.details = err.details;
    this.errors = err.errors;
  }
  describe(): string {
    if (this.errors?.length) return this.errors.map((e: any) => e.message).join("; ");
    if (this.details && typeof this.details === "object") {
      return Object.entries(this.details)
        .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v}`)
        .join("; ");
    }
    return this.message;
  }
}

const KEY = (ns: Namespace) => `pzp.session.${ns}`;
export const DEVICE_KEY = "pzp.device";

export function loadSession(ns: Namespace): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(KEY(ns));
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(ns: Namespace, s: Session | null) {
  if (s) localStorage.setItem(KEY(ns), JSON.stringify(s));
  else localStorage.removeItem(KEY(ns));
}

export function deviceInfo(): { token: string; id: number; name: string; store: number; store_number?: string; type: string; pending?: boolean } | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY);
    const d = raw ? JSON.parse(raw) : null;
    return d && !d.pending ? d : null; // a pending (unapproved) device is not presented to the API
  } catch {
    return null;
  }
}

export function pendingDevice() {
  try {
    const d = JSON.parse(localStorage.getItem(DEVICE_KEY) || "null");
    return d?.pending ? d : null;
  } catch {
    return null;
  }
}

export function can(s: Session | null, perm: string) {
  return !!s?.user.permissions.includes(perm);
}

async function refresh(ns: Namespace): Promise<boolean> {
  const s = loadSession(ns);
  if (!s) return false;
  const r = await fetch("/api/v1/auth/refresh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh: s.refresh }),
  });
  if (!r.ok) return false;
  const body = await r.json();
  saveSession(ns, { ...s, access: body.access, refresh: body.refresh ?? s.refresh });
  return true;
}

export interface RequestOpts {
  method?: string;
  body?: any;
  headers?: Record<string, string>;
  auth?: boolean;
}

export function api(ns: Namespace) {
  async function request<T = any>(path: string, opts: RequestOpts = {}, retried = false): Promise<T> {
    const s = loadSession(ns);
    const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.auth !== false && s) headers["Authorization"] = `Bearer ${s.access}`;
    const dev = deviceInfo();
    if (dev && (ns === "pos" || ns === "kds")) headers["X-Device-Token"] = dev.token;
    const r = await fetch(`/api/v1${path}`, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (r.status === 401 && opts.auth !== false && !retried && (await refresh(ns))) {
      return request<T>(path, opts, true);
    }
    const text = await r.text();
    const body = text ? JSON.parse(text) : null;
    if (!r.ok) {
      if (r.status === 401 && opts.auth !== false) {
        saveSession(ns, null);
        if (typeof window !== "undefined") window.dispatchEvent(new Event(`pzp-logout-${ns}`));
      }
      throw new ApiError(r.status, body);
    }
    return body as T;
  }
  return {
    get: <T = any>(p: string) => request<T>(p),
    post: <T = any>(p: string, body: any = {}, headers?: Record<string, string>) =>
      request<T>(p, { method: "POST", body, headers }),
    patch: <T = any>(p: string, body: any) => request<T>(p, { method: "PATCH", body }),
    put: <T = any>(p: string, body: any) => request<T>(p, { method: "PUT", body }),
    del: <T = any>(p: string) => request<T>(p, { method: "DELETE" }),
    raw: request,
  };
}

export function wsUrl(path: string, ns: Namespace) {
  const s = loadSession(ns);
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = process.env.NEXT_PUBLIC_WS_HOST || window.location.host;
  return `${proto}://${host}${path}?token=${encodeURIComponent(s?.access || "")}`;
}

export function results<T>(data: any): T[] {
  return Array.isArray(data) ? data : data?.results ?? [];
}
