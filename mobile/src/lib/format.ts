import * as Crypto from 'expo-crypto';

export function money(cents: number | null | undefined, currency = 'CAD'): string {
  const v = (cents ?? 0) / 100;
  try {
    return new Intl.NumberFormat('en-CA', { style: 'currency', currency }).format(v);
  } catch {
    return `$${v.toFixed(2)}`;
  }
}

export function uuid(): string {
  return Crypto.randomUUID();
}

export function time(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "https://pos.amrt.ca/api/v1/" → "https://pos.amrt.ca" */
export function normalizeUrl(url: string): string {
  return url.trim().replace(/\/+$/, '').replace(/\/api(\/v1)?$/, '');
}
