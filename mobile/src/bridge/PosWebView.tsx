/**
 * Hosts the real web POS (`<server>/pos`) full-screen and connects it to the native card reader.
 * The POS looks and behaves exactly like the browser version; only card payments, the reader
 * and bbpos settings are handled natively.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, BackHandler, Platform, Text, View } from 'react-native';
import { WebView, type WebViewMessageEvent, type WebViewProps } from 'react-native-webview';
import { Banner, Button, Card, H1, Muted, colors } from '../components/ui';
import type { CardResult, Step } from '../lib/cardFlow';
import { ApiError } from '../lib/http';
import { APP_VERSION, useApp } from '../state/AppState';
import { useTerminal } from '../terminal/context';
import { BRIDGE_VERSION, type BridgeError, type CardStatus, type WebRequests } from './protocol';

// react-native-webview 14 declares `class WebView<P = undefined> extends Component<WebViewProps & P>`, which
// collapses to `never` with the React 19 types. The runtime component is fine; give JSX a usable type.
type WebViewHandle = { goBack(): void; injectJavaScript(script: string): void };
const Web = WebView as unknown as React.ComponentType<WebViewProps & { ref?: React.Ref<WebViewHandle> }>;

const USER_AGENT_SUFFIX = `PizzaPOSApp/${APP_VERSION}`;

// Runs before the POS page's own scripts: lets the page know it is inside the app.
const BOOTSTRAP = `
  window.__PIZZA_NATIVE__ = { bridge: ${BRIDGE_VERSION}, version: ${JSON.stringify(APP_VERSION)}, platform: ${JSON.stringify(Platform.OS)} };
  true;
`;

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };

export function PosWebView() {
  const app = useApp();
  const terminal = useTerminal();
  const web = useRef<WebViewHandle>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [pageReady, setPageReady] = useState(false);
  const outgoing = useRef(new Map<string, Pending>());
  const seq = useRef(0);
  const canGoBack = useRef(false);

  const send = useCallback((msg: unknown) => {
    web.current?.injectJavaScript(`window.__pizzaNative && window.__pizzaNative.receive(${JSON.stringify(msg)}); true;`);
  }, []);

  // ---- native → web API calls (the POS session lives in the web page) ----------------
  useEffect(() => {
    app.webApiRef.current = pageReady
      ? (method, path, body, headers) =>
          new Promise((resolve, reject) => {
            const id = `n${++seq.current}`;
            outgoing.current.set(id, { resolve, reject });
            send({ id, request: 'api', payload: { method, path, body, headers } });
            setTimeout(() => {
              if (outgoing.current.delete(id)) reject(new ApiError(0, 'TIMEOUT', 'The POS did not answer in time.'));
            }, 45_000);
          })
      : null;
    if (pageReady && app.pending.length) void app.retryPending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageReady, send]);

  // ---- card status pushed to the page -----------------------------------------------
  const status: CardStatus = useMemo(() => {
    const cfg = app.bbposConfig;
    const reader = terminal.connectedReader;
    let problem: string | null = null;
    if (!app.bbposUser) problem = 'Card payments are not set up on this tablet: tap “Card reader” and sign in to bbpos.';
    else if (app.bbposError) problem = `bbpos: ${app.bbposError}`;
    else if (!cfg) problem = 'Loading card payment settings…';
    else if (!cfg.pricing.allowCustomAmounts) problem = 'bbpos has “custom amounts” disabled (bbpos admin → Settings → Pricing).';
    else if (terminal.backend !== 'mock' && !reader) problem = 'No card reader connected: tap “Card reader” and connect the WisePad 3.';
    return {
      ready: problem === null,
      problem,
      signedIn: Boolean(app.bbposUser),
      readerMode: terminal.readerMode,
      reader: reader
        ? {
            label: reader.label ?? reader.serialNumber ?? 'Card reader',
            batteryPercent: reader.batteryLevel === null ? null : Math.round(reader.batteryLevel * 100),
            charging: reader.isCharging,
          }
        : terminal.backend === 'mock'
          ? { label: 'Mock reader (test)', batteryPercent: null, charging: null }
          : null,
      stripeMode: cfg?.stripe.mode ?? null,
      tipsEnabled: Boolean(cfg?.pricing.tipsEnabled),
      tipPresetsPpm: cfg?.pricing.tipPresetsPpm ?? [],
      mock: terminal.backend === 'mock' ? { outcome: terminal.mockNextOutcome ?? 'approve' } : null,
      pendingRecords: app.pending.length,
    };
  }, [app.bbposConfig, app.bbposUser, app.bbposError, app.pending.length, terminal]);
  const statusRef = useRef(status);
  statusRef.current = status;

  useEffect(() => {
    if (pageReady) send({ event: 'card.status', data: status });
  }, [status, pageReady, send]);

  const stepRef = useRef<Step | null>(null);
  const charging = useRef(false);
  // Relay the reader's prompts ("Insert, tap or swipe card", "Remove card") to the payment window.
  useEffect(() => {
    if (pageReady && stepRef.current) send({ event: 'card.step', data: { step: stepRef.current, prompt: terminal.readerPrompt } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terminal.readerPrompt]);

  // ---- web → native requests -------------------------------------------------------
  const handlers: { [K in keyof WebRequests]: (p: WebRequests[K]['payload']) => Promise<WebRequests[K]['result']> } = {
    hello: async ({ store }) => {
      setPageReady(true);
      if (store?.cardProcessor === 'bbpos') await app.adoptStoreBbposUrl(store.bbposUrl);
      return { appVersion: APP_VERSION, platform: Platform.OS, bridge: BRIDGE_VERSION };
    },
    'card.status': async () => statusRef.current,
    'card.charge': async ({ orderId, orderNumber, amountCents, tipCents }) => {
      if (charging.current) return { kind: 'failed', message: 'A card payment is already in progress.' } as CardResult;
      const st = statusRef.current;
      if (!st.ready) return { kind: 'failed', message: st.problem ?? 'Card payments are not ready.' } as CardResult;
      charging.current = true;
      const onStep = (step: Step) => {
        stepRef.current = step;
        send({ event: 'card.step', data: { step, prompt: terminal.readerPrompt } });
      };
      try {
        return await app.cardFlow.charge(
          { id: orderId, order_number: orderNumber },
          amountCents,
          tipCents,
          {
            readerMode: terminal.readerMode,
            readerSerial: terminal.connectedReader?.serialNumber ?? null,
            collect: terminal.collect,
            confirm: terminal.confirm,
            mockOutcome: terminal.mockNextOutcome,
          },
          onStep,
        );
      } finally {
        charging.current = false;
        stepRef.current = null;
        send({ event: 'card.step', data: { step: null, prompt: null } });
        void app.retryPending();
      }
    },
    'card.cancel': async () => ({ ok: await terminal.cancelCollect() }),
    'card.recheck': async ({ orderId }) => {
      try {
        return await app.cardFlow.recheck(orderId, (step) => send({ event: 'card.step', data: { step, prompt: null } }));
      } finally {
        send({ event: 'card.step', data: { step: null, prompt: null } });
      }
    },
    'card.setMockOutcome': async ({ outcome }) => {
      terminal.setMockNextOutcome?.(outcome);
      return { ok: Boolean(terminal.setMockNextOutcome) };
    },
    'app.openReader': async () => {
      app.setPanel('reader');
      return { ok: true };
    },
    'app.openSettings': async () => {
      app.setPanel('settings');
      return { ok: true };
    },
  };
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const onMessage = useCallback(
    async (e: WebViewMessageEvent) => {
      let msg: any;
      try {
        msg = JSON.parse(e.nativeEvent.data);
      } catch {
        return;
      }
      if (!msg || msg.v !== BRIDGE_VERSION) return;
      // Answer to a native → web request.
      if (msg.replyTo) {
        const p = outgoing.current.get(msg.replyTo);
        if (!p) return;
        outgoing.current.delete(msg.replyTo);
        if (msg.ok) p.resolve(msg.result);
        else {
          const err = (msg.error ?? {}) as BridgeError;
          p.reject(new ApiError(err.status ?? 0, err.code ?? 'ERROR', err.message ?? 'Request failed'));
        }
        return;
      }
      const handler = (handlersRef.current as Record<string, (p: unknown) => Promise<unknown>>)[msg.type];
      if (!handler) {
        send({ id: msg.id, ok: false, error: { status: 400, code: 'UNKNOWN_REQUEST', message: `Unknown request ${msg.type}` } });
        return;
      }
      try {
        send({ id: msg.id, ok: true, result: await handler(msg.payload ?? {}) });
      } catch (err) {
        send({ id: msg.id, ok: false, error: { status: 0, code: 'NATIVE_ERROR', message: err instanceof Error ? err.message : String(err) } });
      }
    },
    [send],
  );

  // Android back button navigates inside the POS instead of closing the app.
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (canGoBack.current) {
        web.current?.goBack();
        return true;
      }
      return false;
    });
    return () => sub.remove();
  }, []);

  if (loadError) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.dark, alignItems: 'center', justifyContent: 'center', padding: 20 }}>
        <Card style={{ width: '100%', maxWidth: 520 }}>
          <H1>POS not reachable</H1>
          <Muted style={{ marginVertical: 8 }}>{app.serverUrl}/pos</Muted>
          <Banner tone="bad">{loadError}</Banner>
          <Button title="Try again" size="lg" onPress={() => { setLoadError(null); setLoading(true); setReloadKey((k) => k + 1); }} />
          <Button title="Server & card settings" variant="secondary" style={{ marginTop: 8 }} onPress={() => app.setPanel('settings')} />
        </Card>
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: '#0f172a' }}>
      <Web
        key={`${app.serverUrl}-${reloadKey}`}
        ref={web}
        source={{ uri: `${app.serverUrl}/pos` }}
        applicationNameForUserAgent={USER_AGENT_SUFFIX}
        injectedJavaScriptBeforeContentLoaded={BOOTSTRAP}
        onMessage={onMessage}
        onLoadStart={() => {
          setPageReady(false);
          setLoading(true);
        }}
        onLoadEnd={() => setLoading(false)}
        onError={(e) => setLoadError(`${e.nativeEvent.description || 'Network error'} (code ${e.nativeEvent.code})`)}
        onHttpError={(e) => {
          if (e.nativeEvent.statusCode >= 500 && e.nativeEvent.url.replace(/\/+$/, '').endsWith('/pos')) {
            setLoadError(`The server answered ${e.nativeEvent.statusCode}. It may be restarting — try again in a minute.`);
          }
        }}
        onNavigationStateChange={(s) => (canGoBack.current = s.canGoBack)}
        domStorageEnabled
        javaScriptEnabled
        cacheEnabled
        sharedCookiesEnabled
        allowsBackForwardNavigationGestures={false}
        setSupportMultipleWindows={false}
        keyboardDisplayRequiresUserAction={false}
        pullToRefreshEnabled={false}
        overScrollMode="never"
        bounces={false}
        textZoom={100}
        style={{ flex: 1, backgroundColor: '#0f172a' }}
      />
      {loading ? (
        <View style={{ position: 'absolute', inset: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: '#0f172a' }}>
          <ActivityIndicator color="#fff" size="large" />
          <Text style={{ color: '#cbd5e1', marginTop: 12 }}>Loading POS…</Text>
        </View>
      ) : null}
    </View>
  );
}
