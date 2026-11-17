import { describe, expect, it, vi } from 'vitest';
import { CardFlow, PENDING_KEY, type BbposPort, type KV, type ReaderPort } from '../src/lib/cardFlow';
import { ApiError } from '../src/lib/http';
import type { ChargeRequest, PaymentStatus } from '../src/lib/bbposApi';

function memoryKV(): KV & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    getJson: async <T,>(k: string) => (data.has(k) ? (structuredClone(data.get(k)) as T) : null),
    setJson: async (k, v) => void data.set(k, structuredClone(v)),
    remove: async (k) => void data.delete(k),
  };
}

const ok = (over: Partial<PaymentStatus> = {}): PaymentStatus => ({
  transactionId: 'tx_1',
  reference: 'R-1',
  status: 'SUCCEEDED',
  amountCents: 2681,
  currency: 'CAD',
  paidAt: null,
  cardBrand: 'visa',
  last4: '4242',
  failureCode: null,
  failureMessage: null,
  declineCode: null,
  mock: false,
  ...over,
});

function setup(opts: { confirm?: () => Promise<PaymentStatus>; pizzaPost?: (...a: any[]) => Promise<any>; mock?: boolean } = {}) {
  const kv = memoryKV();
  const charges: ChargeRequest[] = [];
  const bbpos: BbposPort = {
    createCharge: vi.fn(async (req: ChargeRequest) => {
      charges.push(req);
      return {
        transactionId: 'tx_1',
        reference: 'R-1',
        status: 'REQUIRES_PAYMENT' as const,
        amountCents: req.expectedTotalCents,
        currency: 'CAD',
        clientSecret: opts.mock ? null : 'pi_secret',
        paymentIntentId: opts.mock ? null : 'pi_1',
        livemode: false,
        mock: Boolean(opts.mock),
      };
    }),
    confirm: vi.fn(opts.confirm ?? (async () => ok())),
    cancel: vi.fn(async () => ok({ status: 'CANCELED' })),
    attemptFailed: vi.fn(async () => ok({ status: 'REQUIRES_PAYMENT' })),
    mockComplete: vi.fn(async () => ok({ mock: true })),
  };
  const pizza = {
    post: vi.fn(opts.pizzaPost ?? (async () => ({ id: 'o1', order_number: 7, payment_status: 'paid', payment: { id: 'p1', status: 'captured' } }))),
  };
  let n = 0;
  const flow = new CardFlow({ kv, bbpos, pizza, newKey: () => `key-${++n}`, verifyDelaysMs: [0, 0] });
  const reader: ReaderPort = {
    readerMode: opts.mock ? 'mock' : 'hardware',
    readerSerial: 'WP3-1',
    collect: vi.fn(async () => ({ ok: true as const })),
    confirm: vi.fn(async () => ({ ok: true as const })),
    mockOutcome: 'approve',
  };
  return { kv, bbpos, pizza, flow, reader, charges };
}

const order = { id: 'o1', order_number: 7 };

describe('CardFlow', () => {
  it('charges the exact Pizza total as one untaxed line plus tip, then records it with the bbpos transaction id', async () => {
    const { flow, reader, charges, pizza, kv } = setup();
    const steps: string[] = [];
    const r = await flow.charge(order, 2681, 300, reader, (s) => steps.push(s));
    expect(r.kind).toBe('paid');
    expect(charges[0]).toMatchObject({
      lines: [{ unitPriceCents: 2681, quantity: 1, taxRateIds: [] }],
      tip: { kind: 'fixed', amountCents: 300 },
      expectedTotalCents: 2981,
      readerMode: 'hardware',
      readerSerialNumber: 'WP3-1',
    });
    expect(pizza.post).toHaveBeenCalledWith(
      '/orders/o1/payments/',
      { method: 'card', amount_cents: 2681, tip_cents: 300, reference: 'tx_1' },
      { 'Idempotency-Key': 'bbpos:tx_1' },
    );
    expect(steps).toEqual(['preparing', 'awaiting_card', 'processing', 'verifying', 'recording']);
    expect(await flow.pending()).toEqual([]);
    expect([...kv.data.keys()].filter((k) => k.startsWith('card.attempt'))).toEqual([]);
  });

  it('reuses the idempotency key when the same sale is retried after a decline', async () => {
    const { flow, reader, charges } = setup();
    (reader.collect as any).mockResolvedValueOnce({ ok: false, outcome: 'declined', error: { code: 'DECLINED_BY_READER', message: 'Declined' } });
    expect((await flow.charge(order, 1000, 0, reader)).kind).toBe('declined');
    expect((await flow.charge(order, 1000, 0, reader)).kind).toBe('paid');
    expect(charges[0].idempotencyKey).toBe(charges[1].idempotencyKey);
    // A different tip is a different sale.
    await flow.charge(order, 1000, 100, reader);
    expect(charges[2].idempotencyKey).not.toBe(charges[0].idempotencyKey);
  });

  it('never assumes success: an uncertain reader result is verified with bbpos', async () => {
    const { flow, reader, bbpos } = setup({ confirm: async () => ok({ status: 'PROCESSING' }) });
    (reader.confirm as any).mockResolvedValueOnce({ ok: false, outcome: 'uncertain', error: { code: 'REQUEST_TIMED_OUT', message: 'timeout' } });
    const r = await flow.charge(order, 1000, 0, reader);
    expect(r.kind).toBe('uncertain');
    expect(bbpos.confirm).toHaveBeenCalledTimes(2);
    (bbpos.confirm as any).mockResolvedValue(ok());
    expect((await flow.recheck('o1'))?.kind).toBe('paid');
  });

  it('a bbpos decline after confirmation is reported as declined and nothing is recorded', async () => {
    const { flow, reader, pizza } = setup({ confirm: async () => ok({ status: 'DECLINED', failureMessage: 'Insufficient funds' }) });
    const r = await flow.charge(order, 1000, 0, reader);
    expect(r).toEqual({ kind: 'declined', message: 'Insufficient funds' });
    expect(pizza.post).not.toHaveBeenCalled();
  });

  it('cancelling on the reader cancels the bbpos transaction', async () => {
    const { flow, reader, bbpos } = setup();
    (reader.collect as any).mockResolvedValueOnce({ ok: false, outcome: 'canceled', error: { code: 'CANCELED', message: 'x' } });
    expect((await flow.charge(order, 1000, 0, reader)).kind).toBe('canceled');
    expect(bbpos.cancel).toHaveBeenCalledWith('tx_1');
  });

  it('mock mode completes through bbpos /mock-complete without the card SDK', async () => {
    const { flow, reader, bbpos } = setup({ mock: true });
    const r = await flow.charge(order, 1000, 0, reader);
    expect(r.kind).toBe('paid');
    expect(reader.confirm).not.toHaveBeenCalled();
    expect(bbpos.mockComplete).toHaveBeenCalledWith('tx_1', 'approve');
  });

  it('keeps an approved payment in the pending queue when the Pizza server is unreachable, and retries it', async () => {
    let up = false;
    const { flow, reader, kv } = setup({
      pizzaPost: async () => {
        if (!up) throw new ApiError(0, 'NETWORK', 'offline');
        return { id: 'o1', payment: { id: 'p1', status: 'captured' } };
      },
    });
    const r = await flow.charge(order, 1000, 0, reader);
    expect(r.kind).toBe('record_pending');
    expect(((await kv.getJson<any[]>(PENDING_KEY)) ?? []).map((p) => p.transactionId)).toEqual(['tx_1']);
    expect(await flow.retryPending()).toBe(1);
    up = true;
    expect(await flow.retryPending()).toBe(0);
  });

  it('drops a pending record that the Pizza server says is already used on another order', async () => {
    const { flow, reader } = setup({
      pizzaPost: async () => {
        throw new ApiError(409, 'reference_reused', 'already recorded');
      },
    });
    const r = await flow.charge(order, 1000, 0, reader);
    expect(r.kind).toBe('failed');
    expect(await flow.pending()).toEqual([]);
  });
});
