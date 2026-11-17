// Bridge to the Pizza POS native app (Android / iOS). The app shows this same web POS in a WebView and adds
// what a browser cannot do: Bluetooth card payments on a BBPOS WisePad 3 through bbpos / Stripe Terminal.
// In a normal browser `native.available` is false and nothing here runs.
// Protocol: mobile/src/bridge/protocol.ts — keep both files in sync.
"use client";
import { ApiError, api } from "./api";

export type CardStep = "preparing" | "awaiting_card" | "processing" | "verifying" | "recording";

export interface CardStatus {
  ready: boolean;
  problem: string | null;
  signedIn: boolean;
  readerMode: "hardware" | "simulated" | "mock";
  reader: { label: string; batteryPercent: number | null; charging: boolean | null } | null;
  stripeMode: "test" | "live" | null;
  tipsEnabled: boolean;
  tipPresetsPpm: number[];
  mock: { outcome: "approve" | "decline" } | null;
  pendingRecords: number;
}

export type CardResult =
  | { kind: "paid"; order: any; payment: { id: string; status: string }; cardLabel: string }
  | { kind: "declined"; message: string }
  | { kind: "canceled" }
  | { kind: "failed"; message: string }
  | { kind: "uncertain"; message: string; transactionId: string }
  | { kind: "record_pending"; message: string; pending: unknown };

declare global {
  interface Window {
    __PIZZA_NATIVE__?: { bridge: number; version: string; platform: string };
    ReactNativeWebView?: { postMessage(msg: string): void };
    __pizzaNative?: { receive(msg: any): void };
  }
}

const BRIDGE = 1;
type Listener = (data: any) => void;
const waiting = new Map<string, { resolve: (v: any) => void; reject: (e: any) => void }>();
const listeners = new Map<string, Set<Listener>>();
let seq = 0;

function post(msg: object) {
  window.ReactNativeWebView?.postMessage(JSON.stringify({ v: BRIDGE, ...msg }));
}

// Pizza API calls requested by the app (recording verified card payments) run here, with this page's
// POS session and device credential, so the app never holds staff tokens.
const posApi = api("pos");
async function handleNativeRequest(msg: { id: string; request: string; payload: any }) {
  if (msg.request !== "api") return post({ replyTo: msg.id, ok: false, error: { status: 400, code: "unknown_request", message: msg.request } });
  const { method, path, body, headers } = msg.payload || {};
  try {
    const result = await posApi.raw(path, { method, body, headers });
    post({ replyTo: msg.id, ok: true, result });
  } catch (e) {
    const err = e instanceof ApiError
      ? { status: e.status, code: e.code, message: e.describe() }
      : { status: 0, code: "network", message: "Cannot reach the central server" };
    post({ replyTo: msg.id, ok: false, error: err });
  }
}

function install() {
  if (typeof window === "undefined" || window.__pizzaNative) return;
  window.__pizzaNative = {
    receive(msg: any) {
      if (msg?.request) return void handleNativeRequest(msg);
      if (msg?.event) {
        listeners.get(msg.event)?.forEach((fn) => fn(msg.data));
        return;
      }
      const w = waiting.get(msg?.id);
      if (!w) return;
      waiting.delete(msg.id);
      if (msg.ok) w.resolve(msg.result);
      else w.reject(new Error(msg.error?.message || "App request failed"));
    },
  };
}

export const native = {
  get available(): boolean {
    return typeof window !== "undefined" && !!window.ReactNativeWebView && !!window.__PIZZA_NATIVE__;
  },
  call<T = any>(type: string, payload: object = {}, timeoutMs = 10 * 60_000): Promise<T> {
    install();
    if (!native.available) return Promise.reject(new Error("Not running in the POS app"));
    const id = `w${++seq}`;
    return new Promise<T>((resolve, reject) => {
      waiting.set(id, { resolve, reject });
      post({ id, type, payload });
      setTimeout(() => waiting.delete(id) && reject(new Error("The POS app did not respond")), timeoutMs);
    });
  },
  on(event: "card.status" | "card.step", fn: Listener): () => void {
    install();
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event)!.add(fn);
    return () => listeners.get(event)?.delete(fn);
  },
};

/** True when this store takes cards on the tablet's WisePad 3 through bbpos. */
export function bbposCardsOnThisDevice(store: { payment_config?: Record<string, any> } | undefined | null) {
  return native.available && store?.payment_config?.card_processor === "bbpos";
}
