/**
 * Client for the bbpos payments service (Express API, routes under `/api`).
 *
 * The tablet signs in to bbpos with a bbpos staff account that has the `pos.charge` permission
 * (role CASHIER or above). bbpos owns the Stripe keys, the Terminal location and the
 * PaymentIntents; this app only drives the card reader through the Stripe Terminal SDK.
 *
 * Contract used (bbpos branch claude/adoring-dirac-y07721):
 *   POST /api/auth/login            {email, password, client:"MOBILE"} → {accessToken, refreshToken, user, business}
 *   POST /api/auth/refresh          {refreshToken}                     → {accessToken, refreshToken}
 *   GET  /api/pos/config                                               → business, stripe mode/location, pricing, allowMockPayments
 *   POST /api/terminal/connection-token                                → {secret}
 *   POST /api/terminal/readers/report
 *   POST /api/payments              ChargeRequest                      → ChargeResponse
 *   POST /api/payments/:id/confirm                                     → PaymentStatusResponse (server verifies with Stripe)
 *   GET  /api/payments/:id
 *   POST /api/payments/:id/cancel | /attempt-failed | /mock-complete
 */
import Constants from 'expo-constants';
import { fetchWithTimeout, parseResponse } from './http';
import { normalizeUrl } from './format';
import { storage } from './storage';

const URL_KEY = 'bbpos.apiUrl';
const REFRESH_KEY = 'bbpos.refreshToken';
const USER_KEY = 'bbpos.user';

export type ReaderMode = 'hardware' | 'simulated' | 'mock';
export type TransactionStatus =
  | 'PENDING'
  | 'REQUIRES_PAYMENT'
  | 'PROCESSING'
  | 'SUCCEEDED'
  | 'DECLINED'
  | 'FAILED'
  | 'CANCELED'
  | 'PARTIALLY_REFUNDED'
  | 'REFUNDED';

export interface BbposConfig {
  business: { id: string; name: string; currency: string; timezone: string };
  stripe: { mode: 'test' | 'live'; configured: boolean; terminalLocationId: string | null; terminalLocationName: string | null };
  pricing: {
    tipBase: 'pre_tax' | 'post_tax';
    tipPresetsPpm: number[];
    tipsEnabled: boolean;
    pricesIncludeTax: boolean;
    maxChargeCents: number;
    allowCustomAmounts: boolean;
  };
  allowMockPayments: boolean;
}

export interface ChargeRequest {
  idempotencyKey: string;
  lines: { id: string; name: string; unitPriceCents: number; quantity: number; taxRateIds: string[] }[];
  tip?: { kind: 'fixed'; amountCents: number } | null;
  expectedTotalCents: number;
  readerSerialNumber?: string | null;
  readerMode: ReaderMode;
}

export interface ChargeResponse {
  transactionId: string;
  reference: string;
  status: TransactionStatus;
  amountCents: number;
  currency: string;
  clientSecret: string | null;
  paymentIntentId: string | null;
  livemode: boolean;
  mock: boolean;
}

export interface PaymentStatus {
  transactionId: string;
  reference: string;
  status: TransactionStatus;
  amountCents: number;
  currency: string;
  paidAt: string | null;
  cardBrand: string | null;
  last4: string | null;
  failureCode: string | null;
  failureMessage: string | null;
  declineCode: string | null;
  mock: boolean;
}

export interface BbposUser {
  email: string;
  name: string;
  role: string;
  businessName: string;
}

let baseUrl = normalizeUrl((Constants.expoConfig?.extra?.bbposApiUrl as string | undefined) ?? '');
let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

async function setTokens(access: string | null, refresh?: string | null) {
  accessToken = access;
  if (refresh === null) await storage.remove(REFRESH_KEY);
  else if (refresh) await storage.set(REFRESH_KEY, refresh);
}

async function refreshTokens(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    const refreshToken = await storage.get(REFRESH_KEY);
    if (!refreshToken || !baseUrl) return false;
    try {
      const res = await fetchWithTimeout(`${baseUrl}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refreshToken }),
      });
      if (!res.ok) {
        if (res.status === 401) await setTokens(null, null);
        return false;
      }
      const data = (await res.json()) as { accessToken: string; refreshToken: string };
      await setTokens(data.accessToken, data.refreshToken);
      return true;
    } catch {
      return false; // network error: keep the refresh token and try again later
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function request<T>(method: string, path: string, body?: unknown, timeoutMs?: number, retried = false): Promise<T> {
  if (!baseUrl) throw new Error('The bbpos payments server is not configured. Open Settings → Card payments.');
  if (!accessToken && !retried && (await storage.get(REFRESH_KEY))) await refreshTokens();
  const res = await fetchWithTimeout(
    `${baseUrl}${path}`,
    {
      method,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined && { 'Content-Type': 'application/json' }),
        ...(accessToken && { Authorization: `Bearer ${accessToken}` }),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    timeoutMs,
  );
  if (res.status === 401 && !retried && (await refreshTokens())) return request<T>(method, path, body, timeoutMs, true);
  return parseResponse<T>(res);
}

export const bbpos = {
  async init() {
    const saved = await storage.get(URL_KEY);
    if (saved) baseUrl = saved;
  },
  get baseUrl() {
    return baseUrl;
  },
  async setBaseUrl(url: string) {
    baseUrl = normalizeUrl(url);
    await storage.set(URL_KEY, baseUrl);
  },
  /** Use the URL the restaurant configured centrally unless one was set on this device. */
  async adoptDefaultUrl(url: string | undefined) {
    if (url && !(await storage.get(URL_KEY))) baseUrl = normalizeUrl(url);
  },
  async isSignedIn() {
    return Boolean(accessToken || (await storage.get(REFRESH_KEY)));
  },
  async user() {
    return storage.getJson<BbposUser>(USER_KEY);
  },
  async login(email: string, password: string): Promise<BbposUser> {
    const res = await fetchWithTimeout(`${baseUrl}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ email, password, client: 'MOBILE' }),
    });
    const data = await parseResponse<{
      accessToken: string;
      refreshToken: string;
      user: { email: string; name: string; role: string };
      business: { name: string };
    }>(res);
    await setTokens(data.accessToken, data.refreshToken);
    const user = { email: data.user.email, name: data.user.name, role: data.user.role, businessName: data.business.name };
    await storage.setJson(USER_KEY, user);
    return user;
  },
  async logout() {
    try {
      if (accessToken) await request('POST', '/api/auth/logout');
    } catch {
      /* best effort */
    }
    await setTokens(null, null);
    await storage.remove(USER_KEY);
  },
  config: () => request<BbposConfig>('GET', '/api/pos/config'),
  connectionToken: () => request<{ secret: string }>('POST', '/api/terminal/connection-token'),
  reportReader: (body: Record<string, unknown>) => request('POST', '/api/terminal/readers/report', body),
  createCharge: (req: ChargeRequest) => request<ChargeResponse>('POST', '/api/payments', req, 30_000),
  confirm: (id: string) => request<PaymentStatus>('POST', `/api/payments/${id}/confirm`, undefined, 30_000),
  status: (id: string) => request<PaymentStatus>('GET', `/api/payments/${id}`),
  cancel: (id: string) => request<PaymentStatus>('POST', `/api/payments/${id}/cancel`),
  attemptFailed: (id: string, body: { code: string; message: string; declineCode?: string | null; stage: 'collect' | 'confirm' }) =>
    request<PaymentStatus>('POST', `/api/payments/${id}/attempt-failed`, body),
  mockComplete: (id: string, outcome: 'approve' | 'decline') => request<PaymentStatus>('POST', `/api/payments/${id}/mock-complete`, { outcome }),
};
