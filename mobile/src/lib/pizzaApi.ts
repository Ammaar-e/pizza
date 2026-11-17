/**
 * Client for the Pizza platform API (`/api/v1/`): POS JWT login with automatic refresh,
 * and the registered-device credential (`X-Device-Token`) that the server uses for device
 * management, PIN sign-in and the cash drawer.
 */
import Constants from 'expo-constants';
import { ApiError, fetchWithTimeout, parseResponse } from './http';
import { normalizeUrl } from './format';
import { storage } from './storage';
import type { PosSession, DeviceCredential } from './types';

const URL_KEY = 'pizza.apiUrl';
const SESSION_KEY = 'pizza.session';
const DEVICE_KEY = 'pizza.device';

let baseUrl = normalizeUrl((Constants.expoConfig?.extra?.pizzaApiUrl as string | undefined) ?? 'https://pos.amrt.ca');
let session: PosSession | null = null;
let device: DeviceCredential | null = null;
let refreshInFlight: Promise<boolean> | null = null;
let onSessionEnded: (() => void) | null = null;

async function refresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    if (!session) return false;
    try {
      const res = await fetchWithTimeout(`${baseUrl}/api/v1/auth/refresh/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ refresh: session.refresh }),
      });
      if (!res.ok) return false;
      const body = (await res.json()) as { access: string; refresh?: string };
      session = { ...session, access: body.access, refresh: body.refresh ?? session.refresh };
      await storage.setJson(SESSION_KEY, session);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

interface Opts {
  headers?: Record<string, string>;
  auth?: boolean;
  timeoutMs?: number;
}

async function request<T>(method: string, path: string, body?: unknown, opts: Opts = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...(opts.headers ?? {}) };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.auth !== false && session) headers.Authorization = `Bearer ${session.access}`;
  if (device && !device.pending) headers['X-Device-Token'] = device.token;
  const res = await fetchWithTimeout(
    `${baseUrl}/api/v1${path}`,
    { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined },
    opts.timeoutMs,
  );
  if (res.status === 401 && opts.auth !== false && !retried && session) {
    if (await refresh()) return request<T>(method, path, body, opts, true);
    onSessionEnded?.();
  }
  return parseResponse<T>(res);
}

export const pizza = {
  async init() {
    const url = await storage.get(URL_KEY);
    if (url) baseUrl = url;
    session = await storage.getJson<PosSession>(SESSION_KEY);
    device = await storage.getJson<DeviceCredential>(DEVICE_KEY);
    return { session, device };
  },
  get baseUrl() {
    return baseUrl;
  },
  async setBaseUrl(url: string) {
    baseUrl = normalizeUrl(url);
    await storage.set(URL_KEY, baseUrl);
  },
  get session() {
    return session;
  },
  async setSession(s: PosSession | null) {
    session = s;
    if (s) await storage.setJson(SESSION_KEY, s);
    else await storage.remove(SESSION_KEY);
  },
  get device() {
    return device;
  },
  async setDevice(d: DeviceCredential | null) {
    device = d;
    if (d) await storage.setJson(DEVICE_KEY, d);
    else await storage.remove(DEVICE_KEY);
  },
  setSessionEndedHandler(fn: () => void) {
    onSessionEnded = fn;
  },
  get: <T = any>(path: string) => request<T>('GET', path),
  post: <T = any>(path: string, body: unknown = {}, headers?: Record<string, string>) => request<T>('POST', path, body, { headers }),
  patch: <T = any>(path: string, body: unknown) => request<T>('PATCH', path, body),
  /** Unauthenticated call (login, enrollment). */
  postAnon: <T = any>(path: string, body: unknown, headers?: Record<string, string>) =>
    request<T>('POST', path, body, { auth: false, headers }),
  /** Device-credential call that bypasses the device "pending" filter (approval polling). */
  async deviceMe(token: string): Promise<{ status: string; [k: string]: unknown }> {
    const res = await fetchWithTimeout(`${baseUrl}/api/v1/device-auth/me/`, { headers: { Accept: 'application/json', 'X-Device-Token': token } });
    return parseResponse(res);
  },
  /** Device-authenticated call (heartbeat, config): only the device credential is sent. */
  async device_(method: 'GET' | 'POST', path: string, body?: unknown): Promise<{ status: number; body: any }> {
    if (!device || device.pending) throw new ApiError(0, 'NO_DEVICE', 'This tablet is not a registered device');
    const res = await fetchWithTimeout(`${baseUrl}/api/v1${path}`, {
      method,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Device-Token': device.token },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let parsed: any = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      /* ignore */
    }
    return { status: res.status, body: parsed };
  },
  /** Health probe used by Settings → "Test connection". */
  async health(): Promise<{ ok: boolean; status: number; detail: string }> {
    try {
      const res = await fetchWithTimeout(`${baseUrl}/healthz/`, { headers: { Accept: 'application/json' } }, 10_000);
      const text = await res.text();
      return { ok: res.ok, status: res.status, detail: res.ok ? 'API reachable and database OK' : text.slice(0, 120) };
    } catch (e) {
      return { ok: false, status: 0, detail: e instanceof ApiError ? e.message : String(e) };
    }
  },
};

export function can(perm: string): boolean {
  return Boolean(session?.user.permissions.includes(perm));
}
