/**
 * Message protocol between the web POS (running inside the app's WebView) and the native app.
 * The web side lives in frontend/src/lib/native.ts — keep both files in sync.
 *
 * Web → native   (window.ReactNativeWebView.postMessage(JSON))
 *   {v:1, id, type, payload}            request; native answers {id, ok, result | error}
 *   {v:1, replyTo, ok, result | error}  answer to a native → web request
 * Native → web   (injectJavaScript: window.__pizzaNative.receive(msg))
 *   {id, ok, result | error}            answer to a web request
 *   {event, data}                       push event
 *   {id, request, payload}              request from native (the web answers with replyTo)
 */
import type { CardResult, Step } from '../lib/cardFlow';

export const BRIDGE_VERSION = 1;

/** Requests the web POS can make. */
export interface WebRequests {
  /** Sent when the POS page loads and whenever the signed-in store changes. */
  hello: {
    payload: { page: string; store?: { id: number; number: string; cardProcessor?: string; bbposUrl?: string } | null };
    result: { appVersion: string; platform: string; bridge: number };
  };
  'card.status': { payload: Record<string, never>; result: CardStatus };
  'card.charge': { payload: { orderId: string; orderNumber: number; amountCents: number; tipCents: number }; result: CardResult };
  'card.cancel': { payload: Record<string, never>; result: { ok: boolean } };
  'card.recheck': { payload: { orderId: string }; result: CardResult | null };
  'card.setMockOutcome': { payload: { outcome: 'approve' | 'decline' }; result: { ok: boolean } };
  'app.openReader': { payload: Record<string, never>; result: { ok: boolean } };
  'app.openSettings': { payload: Record<string, never>; result: { ok: boolean } };
}

/** Requests the native app makes to the web POS (executed with the signed-in POS session). */
export interface NativeRequests {
  api: {
    payload: { method: 'GET' | 'POST'; path: string; body?: unknown; headers?: Record<string, string> };
    result: unknown;
  };
}

export interface CardStatus {
  /** True when everything needed to take a card is in place. */
  ready: boolean;
  /** Why the card button is disabled, for the cashier. */
  problem: string | null;
  signedIn: boolean;
  readerMode: 'hardware' | 'simulated' | 'mock';
  reader: { label: string; batteryPercent: number | null; charging: boolean | null } | null;
  stripeMode: 'test' | 'live' | null;
  tipsEnabled: boolean;
  tipPresetsPpm: number[];
  mock: { outcome: 'approve' | 'decline' } | null;
  pendingRecords: number;
}

export type BridgeEvent =
  | { event: 'card.step'; data: { step: Step | null; prompt: string | null } }
  | { event: 'card.status'; data: CardStatus };

export interface BridgeError {
  status: number;
  code: string;
  message: string;
}
