// Adapted from bbpos apps/mobile/src/terminal (branch claude/adoring-dirac-y07721).
/**
 * Real card-reader implementation using the OFFICIAL Stripe Terminal React
 * Native SDK. Handles Bluetooth LE discovery and connection to the BBPOS
 * WisePad 3, reader software updates, and payment collection/confirmation.
 *
 * Nothing here talks to the reader directly: every operation goes through
 * the certified SDK. Card data never touches this app.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Linking, NativeModules, Platform } from 'react-native';
import { storage as secureStorage } from '../lib/storage';
import type {
  PaymentIntent,
  Reader,
  StripeError,
  useStripeTerminal as UseStripeTerminal,
  StripeTerminalProvider as StripeTerminalProviderType,
  requestNeededAndroidPermissions as RequestPerms,
} from '@stripe/stripe-terminal-react-native';
import { bbpos } from '../lib/bbposApi';
import { DISCONNECT_REASONS, READER_PROMPTS, isCancel, isDecline, toTerminalError } from './errors';
import type { ConnectionStatus, ReaderInfo, ReaderMode, TerminalApi, TerminalError } from './types';
import { TerminalContext, reportReader } from './context';

type Sdk = {
  StripeTerminalProvider: typeof StripeTerminalProviderType;
  useStripeTerminal: typeof UseStripeTerminal;
  requestNeededAndroidPermissions: typeof RequestPerms;
};

/** True only in a development/production build that includes the native module. */
export function isNativeTerminalAvailable(): boolean {
  return Boolean(NativeModules.StripeTerminalReactNative);
}

let sdkCache: Sdk | null = null;
function loadSdk(): Sdk {
  // Lazy: the SDK reads native constants at import time.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  if (!sdkCache) sdkCache = require('@stripe/stripe-terminal-react-native') as Sdk;
  return sdkCache;
}

const LAST_READER_KEY = 'pizza.lastReaderSerial';
const DISCOVERY_TIMEOUT_MS = 60_000;
const CONNECT_TIMEOUT_MS = 60_000;

async function fetchConnectionToken(): Promise<string> {
  const res = await bbpos.connectionToken();
  return res.secret;
}

export function toReaderInfo(r: Reader.Type): ReaderInfo {
  return {
    key: r.serialNumber ?? r.id ?? r.label ?? 'reader',
    serialNumber: r.serialNumber ?? null,
    stripeReaderId: r.id ?? null,
    label: r.label ?? null,
    deviceType: r.deviceType,
    softwareVersion: r.deviceSoftwareVersion ?? r.firmwareVersion ?? null,
    batteryLevel: typeof r.batteryLevel === 'number' ? r.batteryLevel : null,
    batteryStatus: r.batteryStatus ?? null,
    isCharging: typeof r.isCharging === 'boolean' ? r.isCharging : null,
    locationId: r.locationId ?? r.location?.id ?? null,
    locationStatus: r.locationStatus ?? null,
    simulated: Boolean(r.simulated),
    availableUpdate: r.availableUpdate
      ? { version: r.availableUpdate.deviceSoftwareVersion, estimate: r.availableUpdate.estimatedUpdateTime, requiredAt: r.availableUpdate.requiredAt ?? null }
      : null,
  };
}

interface Props {
  readerMode: Exclude<ReaderMode, 'mock'>;
  locationId: string | null;
  children: React.ReactNode;
}

export function StripeTerminalRoot({ readerMode, locationId, children }: Props) {
  const { StripeTerminalProvider } = loadSdk();
  return (
    <StripeTerminalProvider tokenProvider={fetchConnectionToken} localeConfig={{ type: 'cardLanguagePreferenceIfAvailable' }}>
      <StripeBridge readerMode={readerMode} locationId={locationId}>
        {children}
      </StripeBridge>
    </StripeTerminalProvider>
  );
}

function StripeBridge({ readerMode, locationId, children }: Props) {
  const { useStripeTerminal, requestNeededAndroidPermissions } = loadSdk();

  const [initialized, setInitialized] = useState(false);
  const [initError, setInitError] = useState<TerminalError | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('notConnected');
  const [discovered, setDiscovered] = useState<Reader.Type[]>([]);
  const [connected, setConnected] = useState<Reader.Type | null>(null);
  const [discoveryError, setDiscoveryError] = useState<TerminalError | null>(null);
  const [connectError, setConnectError] = useState<TerminalError | null>(null);
  const [lastDisconnect, setLastDisconnect] = useState<TerminalApi['lastDisconnect']>(null);
  const [readerPrompt, setReaderPrompt] = useState<string | null>(null);
  const [update, setUpdate] = useState<TerminalApi['update']>({ installing: false, progress: null, error: null });
  const [lowBattery, setLowBattery] = useState(false);
  const [battery, setBattery] = useState<Reader.BatteryLevel | null>(null);
  const [lastSerial, setLastSerial] = useState<string | null>(null);

  const discoveredRef = useRef<Reader.Type[]>([]);
  const collectedRef = useRef<PaymentIntent.Type | null>(null);
  const autoConnectSerialRef = useRef<string | null>(null);
  const updateStartedRef = useRef(false);
  const connectRef = useRef<(key: string) => Promise<boolean>>(async () => false);

  const sdk = useStripeTerminal({
    onUpdateDiscoveredReaders: (readers) => {
      discoveredRef.current = readers;
      setDiscovered(readers);
      const auto = autoConnectSerialRef.current;
      if (auto && readers.some((r) => r.serialNumber === auto)) {
        autoConnectSerialRef.current = null;
        void connectRef.current(auto);
      }
    },
    onFinishDiscoveringReaders: (error) => {
      if (error && !isCancel(toTerminalError(error))) setDiscoveryError(toTerminalError(error));
    },
    onDidChangeConnectionStatus: (status) => setConnectionStatus(status),
    onDidDisconnect: (reason) => {
      setConnected(null);
      setLastDisconnect({ reason: DISCONNECT_REASONS[reason] ?? DISCONNECT_REASONS.unknown!, at: Date.now() });
      setReaderPrompt(null);
    },
    onDidStartReaderReconnect: () => setConnectionStatus('reconnecting'),
    onDidSucceedReaderReconnect: (reader) => {
      setConnected(reader);
      setConnectionStatus('connected');
    },
    onDidFailReaderReconnect: () => {
      setConnected(null);
      setConnectionStatus('notConnected');
      setLastDisconnect({ reason: 'Could not reconnect to the reader', at: Date.now() });
    },
    onDidReportAvailableUpdate: () => {
      /* surfaced via connectedReader.availableUpdate */
    },
    onDidStartInstallingUpdate: () => {
      updateStartedRef.current = true;
      setUpdate({ installing: true, progress: 0, error: null });
    },
    onDidReportReaderSoftwareUpdateProgress: (progress) => setUpdate((u) => ({ ...u, installing: true, progress })),
    onDidFinishInstallingUpdate: (_update, error) => {
      updateStartedRef.current = false;
      setUpdate({ installing: false, progress: null, error: error ? toTerminalError(error) : null });
    },
    onDidRequestReaderInput: (options) => {
      const parts = options.map((o) => ({ insertCard: 'insert', tapCard: 'tap', swipeCard: 'swipe' })[o]);
      setReaderPrompt(parts.length ? `${parts.join(', ').replace(/^./, (c) => c.toUpperCase())} card` : null);
    },
    onDidRequestReaderDisplayMessage: (message) => setReaderPrompt(READER_PROMPTS[message] ?? null),
    onDidUpdateBatteryLevel: (b) => setBattery(b),
    onDidReportLowBatteryWarning: () => setLowBattery(true),
  });

  // Initialise once (fetches a connection token through the backend).
  useEffect(() => {
    let alive = true;
    (async () => {
      setLastSerial(await secureStorage.get(LAST_READER_KEY));
      const { error, reader } = await sdk.initialize();
      if (!alive) return;
      if (error) setInitError(toTerminalError(error));
      else {
        setInitialized(true);
        if (reader) setConnected(reader); // already connected from a previous session
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Report battery changes to the backend (reader management in admin).
  useEffect(() => {
    if (connected?.serialNumber && battery) {
      void reportReader('battery', toReaderInfo({ ...connected, batteryLevel: battery.batteryLevel, isCharging: battery.isCharging }), readerMode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [battery?.batteryLevel, battery?.isCharging]);

  const requestPermissions = useCallback(async (): Promise<TerminalError | null> => {
    if (Platform.OS !== 'android') return null; // iOS prompts on first Bluetooth use
    const res = (await requestNeededAndroidPermissions({
      accessFineLocation: {
        title: 'Location permission',
        message: 'Stripe requires location access to accept card payments.',
        buttonPositive: 'Allow',
      },
    })) as unknown as { error: unknown };
    if (res?.error) {
      return {
        code: 'BLUETOOTH_PERMISSION_DENIED',
        message: 'Bluetooth or location permission was denied.',
        hint: 'Allow "Nearby devices" and "Location" for this app in Settings.',
        openSettings: true,
      };
    }
    return null;
  }, [requestNeededAndroidPermissions]);

  const discoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelDiscovery = useCallback(async () => {
    if (discoverTimer.current) clearTimeout(discoverTimer.current);
    autoConnectSerialRef.current = null;
    await sdk.cancelDiscovering().catch(() => undefined);
  }, [sdk]);

  const discover = useCallback(async () => {
    setDiscoveryError(null);
    setConnectError(null);
    if (!initialized) {
      setDiscoveryError(initError ?? { code: 'NOT_INITIALIZED', message: 'The card reader service is still starting.' });
      return;
    }
    const permErr = await requestPermissions();
    if (permErr) {
      setDiscoveryError(permErr);
      return;
    }
    discoveredRef.current = [];
    setDiscovered([]);
    if (discoverTimer.current) clearTimeout(discoverTimer.current);
    discoverTimer.current = setTimeout(() => {
      void sdk.cancelDiscovering().catch(() => undefined);
      if (discoveredRef.current.length === 0) {
        setDiscoveryError({
          code: 'BLUETOOTH_SCAN_TIMED_OUT',
          message: 'No reader found.',
          hint: 'Press the power button on the WisePad 3, keep it within a metre, and make sure it is not connected to another device.',
        });
      }
    }, DISCOVERY_TIMEOUT_MS);
    // Resolves when discovery finishes or is cancelled; results arrive via onUpdateDiscoveredReaders.
    const { error } = await sdk.discoverReaders({ discoveryMethod: 'bluetoothScan', simulated: readerMode === 'simulated' });
    if (discoverTimer.current) clearTimeout(discoverTimer.current);
    if (error) {
      const te = toTerminalError(error);
      if (!isCancel(te)) setDiscoveryError(te);
    }
  }, [initialized, initError, requestPermissions, sdk, readerMode]);

  const connect = useCallback(
    async (key: string): Promise<boolean> => {
      setConnectError(null);
      if (!locationId) {
        setConnectError({
          code: 'LOCATION_NOT_CONFIGURED',
          message: 'No Stripe Terminal location is configured.',
          hint: 'An administrator must create or select a Terminal location in the bbpos admin → Readers & Locations.',
        });
        return false;
      }
      if (connected) {
        if ((connected.serialNumber ?? connected.id) === key) return true;
        setConnectError(toTerminalError({ code: 'ALREADY_CONNECTED_TO_READER' }));
        return false;
      }
      const reader = discoveredRef.current.find((r) => (r.serialNumber ?? r.id ?? r.label) === key);
      if (!reader) {
        setConnectError({ code: 'READER_NOT_FOUND', message: 'That reader is no longer nearby.', hint: 'Scan again.' });
        return false;
      }
      await sdk.cancelDiscovering().catch(() => undefined);
      updateStartedRef.current = false;

      // Timeout — suspended while a required reader update is installing (can take minutes).
      let timedOut = false;
      const timeout = new Promise<'timeout'>((resolve) => {
        const check = (elapsed: number) =>
          setTimeout(() => {
            if (updateStartedRef.current) check(0);
            else if (elapsed + 5000 >= CONNECT_TIMEOUT_MS) resolve('timeout');
            else check(elapsed + 5000);
          }, 5000);
        check(0);
      });
      const attempt = sdk.connectReader({
        discoveryMethod: 'bluetoothScan',
        reader,
        locationId,
        autoReconnectOnUnexpectedDisconnect: true,
      });
      const result = await Promise.race([attempt, timeout]);
      if (result === 'timeout') {
        timedOut = true;
        setConnectError({
          code: 'CONNECT_TIMEOUT',
          message: 'Connecting to the reader timed out.',
          hint: 'Restart the WisePad 3 (hold the power button), then scan again.',
        });
        void attempt.then((r) => {
          // If the connection eventually succeeded after we gave up, disconnect cleanly.
          if (timedOut && r.reader) void sdk.disconnectReader();
        });
        return false;
      }
      if (result.error) {
        setConnectError(toTerminalError(result.error));
        return false;
      }
      setConnected(result.reader);
      const serial = result.reader.serialNumber ?? null;
      if (serial) {
        await secureStorage.set(LAST_READER_KEY, serial);
        setLastSerial(serial);
      }
      setLowBattery(false);
      void reportReader('connected', toReaderInfo(result.reader), readerMode);
      return true;
    },
    [connected, locationId, readerMode, sdk],
  );
  connectRef.current = connect;

  const disconnect = useCallback(async () => {
    const current = connected;
    const res = await sdk.disconnectReader();
    if (!res?.error) {
      setConnected(null);
      if (current) void reportReader('disconnected', toReaderInfo(current), readerMode, 'disconnectRequested');
    }
  }, [connected, readerMode, sdk]);

  const reconnectLast = useCallback(async () => {
    if (!lastSerial) return;
    autoConnectSerialRef.current = lastSerial;
    await discover();
  }, [discover, lastSerial]);

  const installUpdate = useCallback(async () => {
    const res = await sdk.installAvailableUpdate();
    if (res?.error) setUpdate({ installing: false, progress: null, error: toTerminalError(res.error) });
  }, [sdk]);

  // ---- payments ----------------------------------------------------------------

  const collect = useCallback<TerminalApi['collect']>(
    async (clientSecret) => {
      collectedRef.current = null;
      setReaderPrompt(null);
      if (!clientSecret) return { ok: false, outcome: 'failed', error: { code: 'NO_CLIENT_SECRET', message: 'Payment could not be prepared.' } };
      if (!connected) return { ok: false, outcome: 'failed', error: toTerminalError({ code: 'NOT_CONNECTED_TO_READER' }) };
      const retrieved = await sdk.retrievePaymentIntent(clientSecret);
      if (retrieved.error) return { ok: false, outcome: 'failed', error: toTerminalError(retrieved.error) };
      // WisePad 3 has no on-reader tipping; tips are chosen on the phone and included in the amount.
      const collected = await sdk.collectPaymentMethod({ paymentIntent: retrieved.paymentIntent, skipTipping: true });
      setReaderPrompt(null);
      if (collected.error) {
        const te = toTerminalError(collected.error);
        return { ok: false, error: te, outcome: isCancel(te) ? 'canceled' : isDecline(te) ? 'declined' : 'failed' };
      }
      collectedRef.current = collected.paymentIntent;
      return { ok: true };
    },
    [connected, sdk],
  );

  const confirm = useCallback<TerminalApi['confirm']>(async () => {
    const pi = collectedRef.current;
    if (!pi) return { ok: false, outcome: 'uncertain', error: { code: 'NO_COLLECTED_PAYMENT', message: 'No card has been collected.' } };
    const res = await sdk.confirmPaymentIntent({ paymentIntent: pi });
    setReaderPrompt(null);
    if (res.error) {
      const te = toTerminalError(res.error as StripeError);
      // Anything other than a clear decline/cancel is treated as UNKNOWN and verified server-side.
      return { ok: false, error: te, outcome: isDecline(te) ? 'declined' : isCancel(te) ? 'canceled' : 'uncertain' };
    }
    collectedRef.current = null;
    return { ok: true };
  }, [sdk]);

  const cancelCollect = useCallback(async () => {
    const res = await sdk.cancelCollectPaymentMethod();
    return !res?.error;
  }, [sdk]);

  const refundInPerson = useCallback<TerminalApi['refundInPerson']>(
    async ({ chargeId, amountCents, currency }) => {
      if (!connected) return { ok: false, error: toTerminalError({ code: 'NOT_CONNECTED_TO_READER' }) };
      const res = await sdk.processRefund({ chargeId, amount: amountCents, currency: currency.toLowerCase() });
      if (res.error) return { ok: false, error: toTerminalError(res.error) };
      return { ok: true };
    },
    [connected, sdk],
  );

  const setSimulatedCard = useCallback(
    async (cardNumber: string) => {
      if (readerMode === 'simulated') await sdk.setSimulatedCard(cardNumber);
    },
    [readerMode, sdk],
  );

  const connectedInfo = useMemo(() => {
    if (!connected) return null;
    const info = toReaderInfo(connected);
    if (battery) {
      info.batteryLevel = battery.batteryLevel;
      info.batteryStatus = battery.batteryStatus;
      info.isCharging = battery.isCharging;
    }
    return info;
  }, [connected, battery]);

  const value: TerminalApi = {
    backend: 'stripe',
    readerMode,
    initialized,
    initError,
    connectionStatus,
    connectedReader: connectedInfo,
    discoveredReaders: useMemo(() => discovered.map(toReaderInfo), [discovered]),
    discoveryError,
    connectError,
    lastDisconnect,
    readerPrompt,
    update,
    lowBattery,
    lastConnectedSerial: lastSerial,
    requestPermissions,
    discover,
    cancelDiscovery,
    connect,
    disconnect,
    reconnectLast,
    installUpdate,
    collect,
    confirm,
    cancelCollect,
    refundInPerson,
    setSimulatedCard,
  };
  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}

export const openSystemSettings = () => Linking.openSettings();
