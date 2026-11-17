/**
 * App-wide state: the Pizza POS session and device credential, the bbpos sign-in and
 * configuration, the card-payment flow, and the device heartbeat.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState as RNAppState, Platform } from 'react-native';
import Constants from 'expo-constants';
import { bbpos, type BbposConfig, type BbposUser } from '../lib/bbposApi';
import { CardFlow, type PendingRecord } from '../lib/cardFlow';
import { describeError } from '../lib/http';
import { pizza } from '../lib/pizzaApi';
import { storage } from '../lib/storage';
import { uuid } from '../lib/format';
import type { DeviceCredential, PosSession } from '../lib/types';

export const APP_VERSION = (Constants.expoConfig?.version as string | undefined) ?? '1.0.0';

interface DeviceState {
  status?: string;
  versionStatus?: string;
  revoked?: string;
  features: Record<string, boolean>;
}

interface Ctx {
  ready: boolean;
  session: PosSession | null;
  device: DeviceCredential | null;
  deviceState: DeviceState;
  setSession(s: PosSession | null): Promise<void>;
  setDevice(d: DeviceCredential | null): Promise<void>;
  logout(): Promise<void>;

  /** True when this store takes cards through bbpos (portal → store → payment settings). */
  cardViaBbpos: boolean;
  bbposUser: BbposUser | null;
  bbposConfig: BbposConfig | null;
  bbposError: string | null;
  reloadBbpos(): Promise<void>;
  bbposLogin(email: string, password: string): Promise<void>;
  bbposLogout(): Promise<void>;

  cardFlow: CardFlow;
  pending: PendingRecord[];
  retryPending(): Promise<void>;
}

const AppCtx = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const v = useContext(AppCtx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSessionState] = useState<PosSession | null>(null);
  const [device, setDeviceState] = useState<DeviceCredential | null>(null);
  const [deviceState, setDeviceStatus] = useState<DeviceState>({ features: {} });
  const [bbposUser, setBbposUser] = useState<BbposUser | null>(null);
  const [bbposConfig, setBbposConfig] = useState<BbposConfig | null>(null);
  const [bbposError, setBbposError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingRecord[]>([]);

  const cardFlow = useMemo(() => new CardFlow({ kv: storage, bbpos, pizza, newKey: uuid }), []);

  const setSession = useCallback(async (s: PosSession | null) => {
    await pizza.setSession(s);
    setSessionState(s);
  }, []);
  const setDevice = useCallback(async (d: DeviceCredential | null) => {
    await pizza.setDevice(d);
    setDeviceState(d);
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

  // Boot: restore saved sessions.
  useEffect(() => {
    (async () => {
      const { session: s, device: d } = await pizza.init();
      await bbpos.init();
      setSessionState(s);
      setDeviceState(d);
      if (s) await bbpos.adoptDefaultUrl(s.store.payment_config.bbpos_public_url ?? s.store.payment_config.bbpos_api_url);
      await reloadBbpos();
      await refreshPending();
      setReady(true);
    })();
    pizza.setSessionEndedHandler(() => {
      void pizza.setSession(null);
      setSessionState(null);
    });
  }, [reloadBbpos, refreshPending]);

  useEffect(() => {
    if (session) void bbpos.adoptDefaultUrl(session.store.payment_config.bbpos_public_url ?? session.store.payment_config.bbpos_api_url).then(reloadBbpos);
  }, [session, reloadBbpos]);

  // Retry card payments that were approved but not yet recorded on the order.
  useEffect(() => {
    if (!session) return;
    const t = setInterval(() => {
      void cardFlow.pending().then((p) => (p.length ? retryPending() : setPending(p)));
    }, 30_000);
    return () => clearInterval(t);
  }, [session, cardFlow, retryPending]);

  // Device heartbeat + central configuration (registered devices only).
  const beatRef = useRef<() => Promise<void>>(async () => undefined);
  beatRef.current = async () => {
    if (!device || device.pending) return;
    try {
      const cfg = await storage.getJson<{ config_version: number; settings: { features?: Record<string, boolean>; heartbeat_interval_seconds?: number } }>('pizza.deviceConfig');
      const res = await pizza.device_('POST', '/device-auth/heartbeat/', {
        software_version: APP_VERSION,
        os_info: `${Platform.OS} ${String(Platform.Version)} (mobile app)`,
        app_status: 'active',
        sync_status: pending.length ? 'card_records_pending' : 'ok',
        pending_offline_count: pending.length,
        config_version: cfg?.config_version ?? 0,
        signed_in_username: session?.user.username ?? '',
      });
      if (res.status === 401 || res.status === 403) {
        const code = res.body?.error?.code as string | undefined;
        setDeviceStatus((d) => ({ ...d, revoked: res.body?.error?.message ?? 'Device access revoked' }));
        if (code && ['device_revoked', 'device_deactivated', 'invalid_device'].includes(code)) await setDevice(null);
        return;
      }
      const body = res.body ?? {};
      let features = cfg?.settings?.features ?? {};
      if (body.config_stale || !cfg) {
        const c = await pizza.device_('GET', '/device-auth/config/');
        if (c.body?.config_version) {
          await storage.setJson('pizza.deviceConfig', c.body);
          features = c.body.settings?.features ?? {};
          await pizza.device_('POST', '/device-auth/config/', { config_version: c.body.config_version });
        }
      }
      setDeviceStatus({ status: body.status, versionStatus: body.version_status, features });
    } catch {
      /* offline: the server marks the device offline from missing heartbeats */
    }
  };
  useEffect(() => {
    if (!device || device.pending) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const loop = async () => {
      await beatRef.current();
      const cfg = await storage.getJson<{ settings?: { heartbeat_interval_seconds?: number } }>('pizza.deviceConfig');
      if (!stopped) timer = setTimeout(loop, (cfg?.settings?.heartbeat_interval_seconds ?? 60) * 1000);
    };
    void loop();
    const sub = RNAppState.addEventListener('change', (st) => st === 'active' && void beatRef.current());
    return () => {
      stopped = true;
      clearTimeout(timer);
      sub.remove();
    };
  }, [device]);

  const value: Ctx = {
    ready,
    session,
    device,
    deviceState,
    setSession,
    setDevice,
    logout: () => setSession(null),
    cardViaBbpos: session?.store.payment_config.card_processor === 'bbpos',
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
    cardFlow,
    pending,
    retryPending,
  };
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function featureOn(state: DeviceState, feature: string) {
  return state.features[feature] !== false;
}
