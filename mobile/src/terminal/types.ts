// Adapted from bbpos apps/mobile/src/terminal (branch claude/adoring-dirac-y07721).
import type { ReaderMode } from '../lib/bbposApi';

export type { ReaderMode };

export type ConnectionStatus = 'notConnected' | 'connecting' | 'connected' | 'discovering' | 'reconnecting';

export interface ReaderInfo {
  /** Stable key for lists: serial number when available. */
  key: string;
  serialNumber: string | null;
  stripeReaderId: string | null;
  label: string | null;
  deviceType: string;
  softwareVersion: string | null;
  batteryLevel: number | null;
  batteryStatus: 'critical' | 'low' | 'nominal' | 'unknown' | null;
  isCharging: boolean | null;
  locationId: string | null;
  locationStatus: 'notSet' | 'set' | 'unknown' | null;
  simulated: boolean;
  availableUpdate: { version: string; estimate: string; requiredAt: string | null } | null;
}

export interface TerminalError {
  code: string;
  message: string;
  /** What the cashier should do about it. */
  hint?: string;
  declineCode?: string | null;
  /** Suggest opening system settings (Bluetooth / permissions). */
  openSettings?: boolean;
}

export type CollectResult =
  | { ok: true }
  | { ok: false; error: TerminalError; outcome: 'canceled' | 'declined' | 'failed' };

export type ConfirmResult =
  | { ok: true }
  /** declined: definitely not paid. uncertain: MUST be verified with the server. */
  | { ok: false; error: TerminalError; outcome: 'declined' | 'uncertain' | 'canceled' };

export interface UpdateState {
  installing: boolean;
  progress: number | null;
  error: TerminalError | null;
}

export interface TerminalApi {
  backend: 'stripe' | 'mock';
  readerMode: ReaderMode;
  initialized: boolean;
  initError: TerminalError | null;
  connectionStatus: ConnectionStatus;
  connectedReader: ReaderInfo | null;
  discoveredReaders: ReaderInfo[];
  discoveryError: TerminalError | null;
  connectError: TerminalError | null;
  lastDisconnect: { reason: string; at: number } | null;
  /** Prompt to relay from the reader ("Insert, tap or swipe", "Remove card"). */
  readerPrompt: string | null;
  update: UpdateState;
  lowBattery: boolean;
  lastConnectedSerial: string | null;

  requestPermissions(): Promise<TerminalError | null>;
  discover(): Promise<void>;
  cancelDiscovery(): Promise<void>;
  connect(key: string): Promise<boolean>;
  disconnect(): Promise<void>;
  reconnectLast(): Promise<void>;
  installUpdate(): Promise<void>;

  collect(clientSecret: string | null): Promise<CollectResult>;
  confirm(): Promise<ConfirmResult>;
  cancelCollect(): Promise<boolean>;
  refundInPerson(p: { chargeId: string; amountCents: number; currency: string }): Promise<{ ok: true } | { ok: false; error: TerminalError }>;

  /** Simulated reader only: choose the Stripe test card used for the next payment. */
  setSimulatedCard?(cardNumber: string): Promise<void>;
  /** Mock only: decide the next outcome. */
  mockNextOutcome?: 'approve' | 'decline';
  setMockNextOutcome?(o: 'approve' | 'decline'): void;
}
