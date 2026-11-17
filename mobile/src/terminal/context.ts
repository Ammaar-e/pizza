// Adapted from bbpos apps/mobile/src/terminal (branch claude/adoring-dirac-y07721).
import { createContext, useContext } from 'react';
import { bbpos } from '../lib/bbposApi';
import type { ReaderInfo, ReaderMode, TerminalApi } from './types';

export const TerminalContext = createContext<TerminalApi | null>(null);

export function useTerminal(): TerminalApi {
  const v = useContext(TerminalContext);
  if (!v) throw new Error('useTerminal outside TerminalProvider');
  return v;
}

/** Tell the backend about reader lifecycle events (best effort, never blocks UI). */
export async function reportReader(
  event: 'connected' | 'disconnected' | 'battery' | 'updated',
  r: ReaderInfo,
  readerMode: ReaderMode,
  reason?: string,
) {
  if (!r.serialNumber) return;
  try {
    await bbpos.reportReader({
      event,
      serialNumber: r.serialNumber,
      stripeReaderId: r.stripeReaderId,
      label: r.label,
      deviceType: r.deviceType,
      softwareVersion: r.softwareVersion,
      batteryLevel: r.batteryLevel,
      isCharging: r.isCharging,
      stripeLocationId: r.locationId,
      readerMode,
      reason,
    });
  } catch {
    /* non-critical */
  }
}
