// Adapted from bbpos apps/mobile/src/terminal (branch claude/adoring-dirac-y07721).
/**
 * MOCK reader — for UI and workflow testing without hardware or Stripe.
 *
 * It does NOT process card payments. Transactions created in mock mode are
 * flagged `isMock` on the server, never touch Stripe, and the backend refuses
 * mock mode entirely when the business is in live mode.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import type { ConnectionStatus, ReaderInfo, TerminalApi } from './types';
import { TerminalContext, reportReader } from './context';

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

const MOCK_READERS: ReaderInfo[] = [1, 2].map((n) => ({
  key: `MOCK-WP3-000${n}`,
  serialNumber: `MOCK-WP3-000${n}`,
  stripeReaderId: null,
  label: `Mock WisePad 3 #${n}`,
  deviceType: 'wisePad3',
  softwareVersion: '0.0.0-mock',
  batteryLevel: n === 1 ? 0.82 : 0.18,
  batteryStatus: n === 1 ? 'nominal' : 'low',
  isCharging: false,
  locationId: null,
  locationStatus: 'notSet',
  simulated: true,
  availableUpdate: null,
}));

export function MockTerminalProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>('notConnected');
  const [discovered, setDiscovered] = useState<ReaderInfo[]>([]);
  const [connected, setConnected] = useState<ReaderInfo | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [lastSerial, setLastSerial] = useState<string | null>(null);
  const [nextOutcome, setNextOutcome] = useState<'approve' | 'decline'>('approve');
  const cancelRef = useRef(false);
  const discoveringRef = useRef(false);

  const discover = useCallback(async () => {
    discoveringRef.current = true;
    setStatus('discovering');
    setDiscovered([]);
    await wait(900);
    if (!discoveringRef.current) return;
    setDiscovered([MOCK_READERS[0]!]);
    await wait(700);
    if (!discoveringRef.current) return;
    setDiscovered(MOCK_READERS);
    setStatus((s) => (s === 'discovering' ? 'notConnected' : s));
    discoveringRef.current = false;
  }, []);

  const connect = useCallback(async (key: string) => {
    discoveringRef.current = false;
    const r = MOCK_READERS.find((m) => m.key === key);
    if (!r) return false;
    setStatus('connecting');
    await wait(1200);
    setConnected(r);
    setLastSerial(r.key);
    setStatus('connected');
    void reportReader('connected', r, 'mock');
    return true;
  }, []);

  const collect = useCallback<TerminalApi['collect']>(async () => {
    cancelRef.current = false;
    setPrompt('MOCK: simulating "Insert, tap or swipe card"');
    for (let i = 0; i < 25; i++) {
      await wait(100);
      if (cancelRef.current) {
        setPrompt(null);
        return { ok: false, outcome: 'canceled', error: { code: 'CANCELED', message: 'The payment was cancelled.' } };
      }
    }
    setPrompt(null);
    return { ok: true };
  }, []);

  const value = useMemo<TerminalApi>(
    () => ({
      backend: 'mock',
      readerMode: 'mock',
      initialized: true,
      initError: null,
      connectionStatus: status,
      connectedReader: connected,
      discoveredReaders: discovered,
      discoveryError: null,
      connectError: null,
      lastDisconnect: null,
      readerPrompt: prompt,
      update: { installing: false, progress: null, error: null },
      lowBattery: false,
      lastConnectedSerial: lastSerial,
      requestPermissions: async () => null,
      discover,
      cancelDiscovery: async () => {
        discoveringRef.current = false;
        setStatus((s) => (s === 'discovering' ? 'notConnected' : s));
      },
      connect,
      disconnect: async () => {
        if (connected) void reportReader('disconnected', connected, 'mock', 'disconnectRequested');
        setConnected(null);
        setStatus('notConnected');
      },
      reconnectLast: async () => {
        if (lastSerial) await connect(lastSerial);
      },
      installUpdate: async () => undefined,
      collect,
      // Mock confirmation is decided by the server via /mock-complete.
      confirm: async () => ({ ok: true }),
      cancelCollect: async () => {
        cancelRef.current = true;
        return true;
      },
      refundInPerson: async () => ({ ok: true }),
      mockNextOutcome: nextOutcome,
      setMockNextOutcome: setNextOutcome,
    }),
    [status, connected, discovered, prompt, lastSerial, discover, connect, collect, nextOutcome],
  );
  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}
