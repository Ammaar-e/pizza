/**
 * Card payment for a Pizza order through bbpos + Stripe Terminal (BBPOS WisePad 3).
 *
 *   1. The Pizza server has already priced the order (menu, promotions, tax): the app charges exactly
 *      the amount due, as ONE custom-amount line with no extra tax, plus an optional tip.
 *   2. bbpos creates the PaymentIntent (idempotency key is stable across retries of this sale).
 *   3. The reader collects and confirms (or the mock reader in test mode).
 *   4. bbpos verifies the outcome with Stripe (`/confirm`). Only SUCCEEDED continues.
 *   5. The app records the payment on the Pizza order with `reference = bbpos transaction id`.
 *      The Pizza server re-verifies the transaction with bbpos server-to-server (status, amount,
 *      currency, not reused on another order) before marking the order paid.
 *
 * If step 5 cannot reach the Pizza server, the verified transaction is kept in a durable
 * "pending record" queue and retried, so an approved card is never lost and never charged twice.
 *
 * Dependencies are injected so the flow can be unit-tested without React Native.
 */
import type { ChargeRequest, ChargeResponse, PaymentStatus, ReaderMode } from './bbposApi';
import { ApiError } from './http';

export interface Attempt {
  orderId: string;
  orderNumber: number;
  idempotencyKey: string;
  amountCents: number;
  tipCents: number;
  transactionId?: string;
}

export interface PendingRecord {
  orderId: string;
  orderNumber: number;
  transactionId: string;
  amountCents: number;
  tipCents: number;
  cardLabel: string;
  createdAt: string;
  lastError?: string;
}

export interface KV {
  getJson<T>(key: string): Promise<T | null>;
  setJson(key: string, value: unknown): Promise<void>;
  remove(key: string): Promise<void>;
}

export interface ReaderPort {
  readerMode: ReaderMode;
  readerSerial: string | null;
  collect(clientSecret: string | null): Promise<{ ok: true } | { ok: false; outcome: 'canceled' | 'declined' | 'failed'; error: { code: string; message: string; declineCode?: string | null } }>;
  confirm(): Promise<{ ok: true } | { ok: false; outcome: 'declined' | 'uncertain' | 'canceled'; error: { code: string; message: string; declineCode?: string | null } }>;
  /** Mock reader only: what the server should decide. */
  mockOutcome?: 'approve' | 'decline';
}

export interface BbposPort {
  createCharge(req: ChargeRequest): Promise<ChargeResponse>;
  confirm(id: string): Promise<PaymentStatus>;
  cancel(id: string): Promise<PaymentStatus>;
  attemptFailed(id: string, body: { code: string; message: string; declineCode?: string | null; stage: 'collect' | 'confirm' }): Promise<PaymentStatus>;
  mockComplete(id: string, outcome: 'approve' | 'decline'): Promise<PaymentStatus>;
}

export interface PizzaPort {
  post<T = any>(path: string, body: unknown, headers?: Record<string, string>): Promise<T>;
}

export type Step = 'preparing' | 'awaiting_card' | 'processing' | 'verifying' | 'recording';

export type CardResult =
  | { kind: 'paid'; order: any; payment: { id: string; status: string }; cardLabel: string }
  | { kind: 'declined'; message: string }
  | { kind: 'canceled' }
  | { kind: 'failed'; message: string }
  /** Payment state unknown: the cashier must "Check again" (never assume either way). */
  | { kind: 'uncertain'; message: string; transactionId: string }
  /** Card approved and verified by bbpos, but the Pizza server could not record it yet. */
  | { kind: 'record_pending'; message: string; pending: PendingRecord };

const ATTEMPT_KEY = (orderId: string) => `card.attempt.${orderId}`;
export const PENDING_KEY = 'card.pendingRecords';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function cardLabel(s: Pick<PaymentStatus, 'cardBrand' | 'last4' | 'mock'>): string {
  if (s.mock) return 'MOCK card (test, no money moved)';
  return [s.cardBrand?.toUpperCase(), s.last4 ? `•••• ${s.last4}` : null].filter(Boolean).join(' ') || 'Card';
}

export class CardFlow {
  constructor(
    private deps: {
      kv: KV;
      bbpos: BbposPort;
      pizza: PizzaPort;
      newKey: () => string;
      onStep?: (s: Step) => void;
      verifyDelaysMs?: number[];
    },
  ) {}

  /** Reuse the idempotency key for the same order, amount and tip, so a retry never creates a second charge. */
  async attemptFor(order: { id: string; order_number: number }, amountCents: number, tipCents: number): Promise<Attempt> {
    const existing = await this.deps.kv.getJson<Attempt>(ATTEMPT_KEY(order.id));
    if (existing && existing.amountCents === amountCents && existing.tipCents === tipCents) return existing;
    const a: Attempt = { orderId: order.id, orderNumber: order.order_number, idempotencyKey: this.deps.newKey(), amountCents, tipCents };
    await this.deps.kv.setJson(ATTEMPT_KEY(order.id), a);
    return a;
  }

  async clearAttempt(orderId: string) {
    await this.deps.kv.remove(ATTEMPT_KEY(orderId));
  }

  async charge(
    order: { id: string; order_number: number },
    amountCents: number,
    tipCents: number,
    reader: ReaderPort,
    onStep: (s: Step) => void = this.deps.onStep ?? (() => undefined),
  ): Promise<CardResult> {
    const { bbpos } = this.deps;
    const attempt = await this.attemptFor(order, amountCents, tipCents);
    onStep('preparing');
    let charge: ChargeResponse;
    try {
      charge = await bbpos.createCharge({
        idempotencyKey: attempt.idempotencyKey,
        lines: [{ id: 'order', name: `Order #${order.order_number}`, unitPriceCents: amountCents, quantity: 1, taxRateIds: [] }],
        tip: tipCents > 0 ? { kind: 'fixed', amountCents: tipCents } : null,
        expectedTotalCents: amountCents + tipCents,
        readerSerialNumber: reader.readerSerial,
        readerMode: reader.readerMode,
      });
    } catch (e) {
      return { kind: 'failed', message: describe(e, 'Could not start the card payment') };
    }
    attempt.transactionId = charge.transactionId;
    await this.deps.kv.setJson(ATTEMPT_KEY(order.id), attempt);

    if (charge.status === 'SUCCEEDED') return this.verifyAndRecord(attempt, onStep); // resumed an already-paid sale
    if (charge.status === 'REFUNDED' || charge.status === 'PARTIALLY_REFUNDED') {
      return { kind: 'failed', message: 'This card sale was refunded in bbpos. Start a new payment.' };
    }

    onStep('awaiting_card');
    const collected = await reader.collect(charge.mock ? null : charge.clientSecret);
    if (!collected.ok) {
      if (collected.outcome === 'canceled') {
        await bbpos.cancel(charge.transactionId).catch(() => undefined);
        await this.clearAttempt(order.id);
        return { kind: 'canceled' };
      }
      await bbpos.attemptFailed(charge.transactionId, { ...collected.error, stage: 'collect' }).catch(() => undefined);
      return collected.outcome === 'declined' ? { kind: 'declined', message: collected.error.message } : { kind: 'failed', message: collected.error.message };
    }

    onStep('processing');
    if (charge.mock) {
      try {
        await bbpos.mockComplete(charge.transactionId, reader.mockOutcome ?? 'approve');
      } catch (e) {
        return { kind: 'failed', message: describe(e, 'Mock payment failed') };
      }
    } else {
      const confirmed = await reader.confirm();
      if (!confirmed.ok) {
        if (confirmed.outcome === 'declined') {
          await bbpos.attemptFailed(charge.transactionId, { ...confirmed.error, stage: 'confirm' }).catch(() => undefined);
          return { kind: 'declined', message: confirmed.error.message };
        }
        if (confirmed.outcome === 'canceled') return { kind: 'canceled' };
        // uncertain → fall through to server verification, never assume the outcome
      }
    }
    return this.verifyAndRecord(attempt, onStep);
  }

  /** "Check again" after an uncertain outcome: re-verify the last card attempt for this order. */
  async recheck(orderId: string, onStep?: (s: Step) => void): Promise<CardResult | null> {
    const attempt = await this.deps.kv.getJson<Attempt>(ATTEMPT_KEY(orderId));
    if (!attempt?.transactionId) return null;
    return this.verifyAndRecord(attempt, onStep);
  }

  /** Ask bbpos (which asks Stripe) for the authoritative result, then record it on the Pizza order. */
  async verifyAndRecord(attempt: Attempt, onStep: (s: Step) => void = this.deps.onStep ?? (() => undefined)): Promise<CardResult> {
    const { bbpos } = this.deps;
    const txId = attempt.transactionId!;
    onStep('verifying');
    let status: PaymentStatus | null = null;
    let lastErr = '';
    for (const delay of this.deps.verifyDelaysMs ?? [0, 1500, 3000, 6000]) {
      if (delay) await sleep(delay);
      try {
        status = await bbpos.confirm(txId);
        if (!['PROCESSING', 'PENDING', 'REQUIRES_PAYMENT'].includes(status.status)) break;
      } catch (e) {
        lastErr = describe(e, 'Could not verify the payment');
        if (e instanceof ApiError && !e.isNetwork && e.status < 500) break;
      }
    }
    if (!status || ['PROCESSING', 'PENDING'].includes(status.status)) {
      return { kind: 'uncertain', transactionId: txId, message: lastErr || 'The payment is still processing. Check again before taking another payment.' };
    }
    if (status.status === 'DECLINED') return { kind: 'declined', message: status.failureMessage ?? 'The card was declined.' };
    if (status.status === 'REQUIRES_PAYMENT') return { kind: 'declined', message: status.failureMessage ?? 'The card was not charged. Try again.' };
    if (status.status !== 'SUCCEEDED') return { kind: 'failed', message: status.failureMessage ?? `Payment ${status.status.toLowerCase()}` };

    const pending: PendingRecord = {
      orderId: attempt.orderId,
      orderNumber: attempt.orderNumber,
      transactionId: txId,
      amountCents: attempt.amountCents,
      tipCents: attempt.tipCents,
      cardLabel: cardLabel(status),
      createdAt: new Date().toISOString(),
    };
    await this.addPending(pending);
    await this.clearAttempt(attempt.orderId);
    onStep('recording');
    return this.record(pending);
  }

  /** Record a bbpos-verified card payment on the Pizza order. Safe to repeat. */
  async record(p: PendingRecord): Promise<CardResult> {
    try {
      const r = await this.deps.pizza.post<any>(
        `/orders/${p.orderId}/payments/`,
        { method: 'card', amount_cents: p.amountCents, tip_cents: p.tipCents, reference: p.transactionId },
        { 'Idempotency-Key': `bbpos:${p.transactionId}` },
      );
      if (r?.payment?.status === 'failed') {
        // The Pizza server refused the verification (amount/status mismatch). Keep it for a manager to resolve.
        const message = `Card approved in bbpos but the POS server refused it: ${r.payment.failure_reason ?? 'verification failed'}`;
        await this.updatePending(p.transactionId, message);
        return { kind: 'record_pending', message, pending: { ...p, lastError: message } };
      }
      await this.removePending(p.transactionId);
      return { kind: 'paid', order: r, payment: r.payment, cardLabel: p.cardLabel };
    } catch (e) {
      if (e instanceof ApiError && e.code === 'reference_reused') {
        await this.removePending(p.transactionId);
        return { kind: 'failed', message: 'This card transaction is already recorded on another order.' };
      }
      if (e instanceof ApiError && e.code === 'overpayment') {
        await this.removePending(p.transactionId);
        return { kind: 'failed', message: 'The order no longer has this balance due (already paid). Refund this card sale in the bbpos admin.' };
      }
      const message = `Card APPROVED. Saving it to the order failed and will retry automatically: ${describe(e, 'server unreachable')}`;
      await this.updatePending(p.transactionId, message);
      return { kind: 'record_pending', message, pending: { ...p, lastError: message } };
    }
  }

  async pending(): Promise<PendingRecord[]> {
    return (await this.deps.kv.getJson<PendingRecord[]>(PENDING_KEY)) ?? [];
  }

  /** Retry every pending record; returns how many are still pending. */
  async retryPending(): Promise<number> {
    for (const p of await this.pending()) await this.record(p);
    return (await this.pending()).length;
  }

  private async addPending(p: PendingRecord) {
    const all = (await this.pending()).filter((x) => x.transactionId !== p.transactionId);
    await this.deps.kv.setJson(PENDING_KEY, [...all, p]);
  }
  private async updatePending(txId: string, lastError: string) {
    const all = await this.pending();
    await this.deps.kv.setJson(PENDING_KEY, all.map((x) => (x.transactionId === txId ? { ...x, lastError } : x)));
  }
  private async removePending(txId: string) {
    const all = await this.pending();
    await this.deps.kv.setJson(PENDING_KEY, all.filter((x) => x.transactionId !== txId));
  }
}

function describe(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.describe();
  return e instanceof Error ? e.message : fallback;
}
