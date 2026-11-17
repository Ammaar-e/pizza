/**
 * Native app state: which POS server to load, the bbpos sign-in/config used for card payments,
 * the card-payment flow and its queue of approved-but-not-yet-recorded payments.
 *
 * Staff sign-in, device registration, orders, cash etc. all happen in the web POS itself
 * (loaded in the WebView), exactly as in the browser.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import Constants from 'expo-constants';
import { bbpos, type BbposConfig, type BbposUser } from '../lib/bbposApi';
import { CardFlow, type PendingRecord, type PizzaPort } from '../lib/cardFlow';
import { normalizeUrl, uuid } from '../lib/format';
import { ApiError, describeError } from '../lib/http';
import { storage } from '../lib/storage';

export const APP_VERSION = (Constants.expoConfig?.version as string | undefined) ?? '1.0.0';
const SERVER_KEY = 'pizza.serverUrl';
const DEFAULT_SERVER = normalizeUrl((Constants.expoConfig?.extra?.pizzaApiUrl as string | undefined) ?? 'https://pos.amrt.ca');

type Panel = 'reader' | 'settings' | null;

/** Executes a Pizza API call inside the web POS (with its session). Set by the WebView host. */
export type WebApi = (method: 'GET' | 'POST', path: string, body?: unknown, headers?: Record<string, string>) => Promise<unknown>;

interface Ctx {
  ready: boolean;
  serverUrl: string;
  setServerUrl(url: string): Promise<void>;

  bbposUser: BbposUser | null;
  bbposConfig: BbposConfig | null;
  bbposError: string | null;
  reloadBbpos(): Promise<void>;
  bbposLogin(email: string, password: string): Promise<void>;
  bbposLogout(): Promise<void>;
  /** The store's bbpos URL as configured in the portal (sent by the web POS). */
  adoptStoreBbposUrl(url: string | undefined): Promise<void>;

  cardFlow: CardFlow;
  pending: PendingRecord[];
  retryPending(): Promise<void>;
  webApiRef: React.MutableRefObject<WebApi | null>;

  panel: Panel;
  setPanel(p: Panel): void;
}

const AppCtx = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const v = useContext(AppCtx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [serverUrl, setServer] = useState(DEFAULT_SERVER);
  const [bbposUser, setBbposUser] = useState<BbposUser | null>(null);
  const [bbposConfig, setBbposConfig] = useState<BbposConfig | null>(null);
  const [bbposError, setBbposError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingRecord[]>([]);
  const [panel, setPanel] = useState<Panel>(null);
  const webApiRef = useRef<WebApi | null>(null);

  const cardFlow = useMemo(() => {
    const pizza: PizzaPort = {
      post: async (path, body, headers) => {
        const call = webApiRef.current;
        if (!call) throw new ApiError(0, 'POS_NOT_LOADED', 'The POS is not loaded; the payment will be saved when it is.');
        return call('POST', path, body, headers) as Promise<any>;
      },
    };
    return new CardFlow({ kv: storage, bbpos, pizza, newKey: uuid });
  }, []);

  const reloadBbpos = useCallback(async () => {
    setBbposError(null);
    const signedIn = await bbpos.isSignedIn();
    setBbposUser(signedIn ? await bbpos.user() : null);
    if (!signedIn || !bbpos.baseUrl) {
      setBbposConfig(null);
      return;
    }
    try {
      setBbposConfig(await bbpos.config());
    } catch (e) {
      setBbposError(describeError(e));
    }
  }, []);

  const refreshPending = useCallback(async () => setPending(await cardFlow.pending()), [cardFlow]);
  const retryPending = useCallback(async () => {
    await cardFlow.retryPending();
    await refreshPending();
  }, [cardFlow, refreshPending]);

  useEffect(() => {
    (async () => {
      const saved = await storage.get(SERVER_KEY);
      if (saved) setServer(saved);
      await bbpos.init();
      await reloadBbpos();
      await refreshPending();
      setReady(true);
    })();
  }, [reloadBbpos, refreshPending]);

  // Approved card payments that could not be saved to their order yet are retried every 30 s.
  useEffect(() => {
    const t = setInterval(() => {
      void cardFlow.pending().then((p) => (p.length && webApiRef.current ? retryPending() : setPending(p)));
    }, 30_000);
    return () => clearInterval(t);
  }, [cardFlow, retryPending]);

  const value: Ctx = {
    ready,
    serverUrl,
    setServerUrl: async (url) => {
      const u = normalizeUrl(url);
      await storage.set(SERVER_KEY, u);
      setServer(u);
    },
    bbposUser,
    bbposConfig,
    bbposError,
    reloadBbpos,
    bbposLogin: async (email, password) => {
      await bbpos.login(email, password);
      await reloadBbpos();
    },
    bbposLogout: async () => {
      await bbpos.logout();
      await reloadBbpos();
    },
    adoptStoreBbposUrl: async (url) => {
      if (!url) return;
      const before = bbpos.baseUrl;
      await bbpos.adoptDefaultUrl(url);
      if (bbpos.baseUrl !== before) await reloadBbpos();
    },
    cardFlow,
    pending,
    retryPending,
    webApiRef,
    panel,
    setPanel,
  };
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}
