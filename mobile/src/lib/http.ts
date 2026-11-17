/** Shared HTTP plumbing for the two backends (Pizza platform and bbpos). */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public errors?: { code?: string; message: string }[] | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
  /** True when the request may not have reached the server, or no response came back. */
  get isNetwork() {
    return this.status === 0;
  }
  /** One readable line for the cashier. */
  describe(): string {
    if (this.errors?.length) return this.errors.map((e) => e.message).join('; ');
    if ([502, 503, 504].includes(this.status) && this.code.startsWith('HTTP_')) {
      return `The server is unavailable (${this.status}): the web server is up but the API service behind it is not responding. See deploy/TROUBLESHOOTING.md.`;
    }
    if (this.details && typeof this.details === 'object' && !Array.isArray(this.details)) {
      const parts = Object.entries(this.details as Record<string, unknown>)
        .filter(([, v]) => typeof v === 'string' || Array.isArray(v))
        .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`);
      if (parts.length) return `${this.message} (${parts.join('; ')})`;
    }
    return this.message;
  }
}

export function describeError(e: unknown): string {
  if (e instanceof ApiError) return e.describe();
  return e instanceof Error ? e.message : String(e);
}

export async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs = 20_000): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } catch (e) {
    const aborted = (e as Error)?.name === 'AbortError';
    throw new ApiError(
      0,
      aborted ? 'TIMEOUT' : 'NETWORK',
      aborted ? 'The server took too long to respond.' : 'Cannot reach the server. Check the Wi-Fi and the server address in Settings.',
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Parse a response body and throw ApiError for non-2xx. Understands both backends' error shapes. */
export async function parseResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* HTML error page from a proxy (e.g. nginx 502) */
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown; errors?: [] } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? `HTTP_${res.status}`, err?.message ?? `Request failed (${res.status})`, err?.details, err?.errors ?? null);
  }
  return data as T;
}
