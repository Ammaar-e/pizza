/**
 * Chooses the card-reader backend:
 *   hardware  — real BBPOS WisePad 3 over Bluetooth via the Stripe Terminal SDK
 *   simulated — Stripe's official simulated reader (bbpos in Stripe test mode only)
 *   mock      — pure UI mock, no Stripe, no money (bbpos test mode, when its admin allows mock payments)
 *
 * In Expo Go (no native SDK) only mock is available.
 */
import React, { createContext, useContext, useEffect, useState } from 'react';
import type { BbposConfig } from '../lib/bbposApi';
import { storage } from '../lib/storage';
import { MockTerminalProvider } from './MockTerminal';
import { StripeTerminalRoot, isNativeTerminalAvailable } from './StripeTerminalBridge';
import type { ReaderMode } from './types';

const MODE_KEY = 'pizza.readerMode';

interface ReaderModeCtx {
  mode: ReaderMode;
  setMode(m: ReaderMode): void;
  available: ReaderMode[];
  nativeAvailable: boolean;
}
const ModeCtx = createContext<ReaderModeCtx | null>(null);

export function useReaderMode() {
  const v = useContext(ModeCtx);
  if (!v) throw new Error('useReaderMode outside TerminalProvider');
  return v;
}

export function TerminalProvider({ config, children }: { config: BbposConfig | null; children: React.ReactNode }) {
  const nativeAvailable = isNativeTerminalAvailable();
  const live = config?.stripe.mode === 'live';
  const available: ReaderMode[] = [
    ...(nativeAvailable ? (['hardware'] as const) : []),
    ...(nativeAvailable && !live ? (['simulated'] as const) : []),
    ...(config?.allowMockPayments && !live ? (['mock'] as const) : []),
  ];
  const [stored, setStored] = useState<ReaderMode | null>(null);

  useEffect(() => {
    void storage.get(MODE_KEY).then((m) => setStored((m as ReaderMode) ?? null));
  }, []);

  // Never allow a mode the current configuration forbids (e.g. mock in live mode).
  const mode: ReaderMode = stored && available.includes(stored) ? stored : (available[0] ?? 'mock');
  const setMode = (m: ReaderMode) => {
    setStored(m);
    void storage.set(MODE_KEY, m);
  };

  const ctx = { mode, setMode, available, nativeAvailable };
  // The Stripe SDK needs a signed-in bbpos session (it fetches connection tokens through bbpos).
  if (mode === 'mock' || !nativeAvailable || !config) {
    return (
      <ModeCtx.Provider value={ctx}>
        <MockTerminalProvider key="mock">{children}</MockTerminalProvider>
      </ModeCtx.Provider>
    );
  }
  return (
    <ModeCtx.Provider value={ctx}>
      <StripeTerminalRoot key={mode} readerMode={mode} locationId={config.stripe.terminalLocationId ?? null}>
        {children}
      </StripeTerminalRoot>
    </ModeCtx.Provider>
  );
}
