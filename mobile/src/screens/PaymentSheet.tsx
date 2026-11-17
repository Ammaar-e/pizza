/**
 * Take payment for an order: cash (with change) or card.
 *
 * Card on a store configured for bbpos: tip on the tablet → bbpos PaymentIntent → WisePad 3
 * (tap/insert/swipe, PIN on the reader) → bbpos verifies with Stripe → the Pizza server verifies
 * with bbpos and marks the order paid. See src/lib/cardFlow.ts.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Text, TextInput, View } from 'react-native';
import { Banner, Button, Chip, H2, Line, Muted, Row, Segmented, Sheet, colors } from '../components/ui';
import type { CardResult, Step } from '../lib/cardFlow';
import { money, uuid } from '../lib/format';
import { describeError } from '../lib/http';
import { pizza } from '../lib/pizzaApi';
import type { Order } from '../lib/types';
import { featureOn, useApp } from '../state/AppState';
import { useTerminal } from '../terminal/context';

const STEP_TEXT: Record<Step, string> = {
  preparing: 'Preparing payment…',
  awaiting_card: 'Present card on the reader',
  processing: 'Processing…',
  verifying: 'Verifying with the bank…',
  recording: 'Saving payment to the order…',
};

export function PaymentSheet({ order, onClose, onPaid }: { order: Order; onClose: () => void; onPaid: (o: Order) => void }) {
  const { cardViaBbpos, bbposConfig, bbposUser, bbposError, cardFlow, deviceState, retryPending } = useApp();
  const terminal = useTerminal();
  const due = order.total_cents - (order.amount_paid_cents ?? 0);
  const [method, setMethod] = useState<'cash' | 'card'>(cardViaBbpos ? 'card' : 'cash');
  const [amountText, setAmountText] = useState((due / 100).toFixed(2));
  const [tendered, setTendered] = useState(0);
  const [tipCents, setTipCents] = useState(0);
  const [customTip, setCustomTip] = useState('');
  const [step, setStep] = useState<Step | null>(null);
  const [result, setResult] = useState<CardResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [change, setChange] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingCard, setPendingCard] = useState<string | null>(null); // server-driven terminal (non-bbpos)

  const amount = Math.round(Number(amountText.replace(',', '.')) * 100) || 0;
  const quick = useMemo(
    () => [amount, Math.ceil(amount / 500) * 500, Math.ceil(amount / 1000) * 1000, Math.ceil(amount / 2000) * 2000, 5000, 10000].filter((v, i, a) => v >= amount && a.indexOf(v) === i).slice(0, 5),
    [amount],
  );
  const tipsEnabled = Boolean(cardViaBbpos && bbposConfig?.pricing.tipsEnabled);
  const presets = bbposConfig?.pricing.tipPresetsPpm ?? [100_000, 150_000, 200_000];
  const readerReady = terminal.backend === 'mock' || Boolean(terminal.connectedReader);
  const cashAllowed = featureOn(deviceState, 'cash_drawer');

  useEffect(() => {
    if (customTip) setTipCents(Math.round(Number(customTip.replace(',', '.')) * 100) || 0);
  }, [customTip]);

  async function finishPaid(o: Order) {
    onPaid(o);
  }

  async function payCash() {
    setBusy(true);
    setError(null);
    try {
      const r = await pizza.post<Order & { payment: { status: string; change_cents: number; failure_reason?: string } }>(
        `/orders/${order.id}/payments/`,
        { method: 'cash', amount_cents: amount, tendered_cents: tendered || amount },
        { 'Idempotency-Key': uuid() },
      );
      if (r.payment.status === 'failed') setError(r.payment.failure_reason ?? 'Payment failed');
      else {
        setChange(r.payment.change_cents ?? 0);
        await finishPaid(r);
      }
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  async function payCardBbpos() {
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const r = await cardFlow.charge(order, amount, tipCents, {
        readerMode: terminal.readerMode,
        readerSerial: terminal.connectedReader?.serialNumber ?? null,
        collect: terminal.collect,
        confirm: terminal.confirm,
        mockOutcome: terminal.mockNextOutcome,
      }, setStep);
      handleResult(r);
    } finally {
      setStep(null);
      setBusy(false);
    }
  }

  function handleResult(r: CardResult) {
    setResult(r);
    if (r.kind === 'paid') void finishPaid(r.order as Order);
    if (r.kind === 'record_pending') void retryPending();
  }

  async function recheck() {
    setBusy(true);
    try {
      const r = await cardFlow.recheck(order.id, setStep);
      if (r) handleResult(r);
    } finally {
      setBusy(false);
    }
  }

  // Server-driven card terminal (store not on bbpos): same as the web POS.
  async function payCardServer() {
    setBusy(true);
    setError(null);
    try {
      const r = await pizza.post<Order & { payment: { id: string; status: string; failure_reason?: string } }>(
        `/orders/${order.id}/payments/`,
        { method: 'card', amount_cents: amount },
        { 'Idempotency-Key': uuid() },
      );
      if (r.payment.status === 'pending') setPendingCard(r.payment.id);
      else if (r.payment.status === 'failed') setError(r.payment.failure_reason ?? 'Card payment failed');
      else await finishPaid(r);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }
  async function confirmServerCard() {
    const r = await pizza.post<{ status: string }>(`/payments/${pendingCard}/confirm/`);
    if (r.status === 'captured') {
      setPendingCard(null);
      await finishPaid(await pizza.get<Order>(`/orders/${order.id}/`));
    } else if (r.status === 'failed') {
      setPendingCard(null);
      setError('Card declined');
    } else setError('Waiting for the card terminal…');
  }

  const paid = change !== null || result?.kind === 'paid';
  const cardBlocked = cardViaBbpos && (!bbposUser || !bbposConfig);

  return (
    <Sheet visible title={`Payment · Order #${order.order_number}`} onClose={step ? () => undefined : onClose}>
      <Row style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <Muted>{paid ? 'Amount paid' : 'Amount due'}</Muted>
        <Text style={{ fontSize: 34, fontWeight: '800' }}>{money(paid ? amount + (result?.kind === 'paid' ? tipCents : 0) : due)}</Text>
      </Row>
      <Banner tone="bad">{error}</Banner>

      {paid ? (
        <>
          <Banner tone="ok">
            <Text style={{ color: colors.ok, fontSize: 18, fontWeight: '700' }}>
              {result?.kind === 'paid' ? `Approved · ${result.cardLabel}` : 'Paid'}
              {change ? ` · Change due ${money(change)}` : ''}
            </Text>
          </Banner>
          <Button title="Done" size="xl" onPress={onClose} />
        </>
      ) : step ? (
        <View style={{ alignItems: 'center', padding: 24 }}>
          <ActivityIndicator size="large" color={colors.brand} />
          <Text style={{ fontSize: 22, fontWeight: '700', marginTop: 16 }}>{STEP_TEXT[step]}</Text>
          {terminal.readerPrompt ? <Text style={{ fontSize: 18, marginTop: 8 }}>{terminal.readerPrompt}</Text> : null}
          <Muted style={{ marginTop: 8 }}>
            {money(amount + tipCents)}
            {tipCents ? ` (incl. tip ${money(tipCents)})` : ''}
          </Muted>
          {step === 'awaiting_card' ? <Button title="Cancel" variant="secondary" style={{ marginTop: 16 }} onPress={() => void terminal.cancelCollect()} /> : null}
        </View>
      ) : (
        <>
          <Segmented<'cash' | 'card'> value={method} onChange={setMethod} options={[['cash', 'Cash'], ['card', 'Card']]} />
          <View style={{ height: 12 }} />
          <Text style={{ fontWeight: '600', marginBottom: 4 }}>Amount to apply (split payments: enter part of the total)</Text>
          <TextInput value={amountText} onChangeText={setAmountText} keyboardType="decimal-pad" style={inputStyle} />

          {method === 'cash' && (
            <View style={{ marginTop: 12 }}>
              {!cashAllowed ? <Banner tone="warn">Cash handling is disabled on this device by its configuration profile.</Banner> : null}
              <H2>Cash tendered</H2>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                {quick.map((q) => (
                  <Chip key={q} label={money(q)} selected={tendered === q} onPress={() => setTendered(q)} />
                ))}
              </View>
              <TextInput
                placeholder="Other amount (e.g. 40.00)"
                keyboardType="decimal-pad"
                onChangeText={(t) => setTendered(Math.round(Number(t.replace(',', '.')) * 100) || 0)}
                style={inputStyle}
              />
              {tendered > amount ? <Line label="Change" value={money(tendered - amount)} bold /> : null}
              <Button title={`Take ${money(amount)} cash`} size="xl" style={{ marginTop: 12 }} busy={busy} disabled={amount <= 0 || amount > due} onPress={payCash} />
              <Muted style={{ marginTop: 6 }}>Requires an open register on this device (Cash tab).</Muted>
            </View>
          )}

          {method === 'card' && cardViaBbpos && (
            <View style={{ marginTop: 12 }}>
              {cardBlocked ? (
                <Banner tone="warn">
                  {bbposError ?? 'Card payments are not set up on this tablet. Open Settings → Card payments and sign in to bbpos.'}
                </Banner>
              ) : !readerReady ? (
                <Banner tone="warn">No card reader connected. Open the Reader tab and connect the WisePad 3.</Banner>
              ) : (
                <Muted>
                  Reader: {terminal.connectedReader?.label ?? terminal.connectedReader?.serialNumber ?? 'mock'} ({terminal.readerMode}
                  {bbposConfig?.stripe.mode === 'test' ? ', TEST mode' : ''})
                </Muted>
              )}
              {tipsEnabled ? (
                <View style={{ marginTop: 12 }}>
                  <H2>Tip</H2>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                    <Chip label="No tip" selected={tipCents === 0 && !customTip} onPress={() => { setCustomTip(''); setTipCents(0); }} />
                    {presets.map((ppm) => {
                      const v = Math.round((amount * ppm) / 1_000_000);
                      return (
                        <Chip
                          key={ppm}
                          label={`${ppm / 10_000}% · ${money(v)}`}
                          selected={!customTip && tipCents === v && v > 0}
                          onPress={() => {
                            setCustomTip('');
                            setTipCents(v);
                          }}
                        />
                      );
                    })}
                  </View>
                  <TextInput placeholder="Custom tip (e.g. 3.00)" keyboardType="decimal-pad" value={customTip} onChangeText={setCustomTip} style={inputStyle} />
                </View>
              ) : null}
              {terminal.backend === 'mock' && terminal.setMockNextOutcome ? (
                <Row style={{ marginTop: 12 }}>
                  <Muted>Mock outcome:</Muted>
                  <Chip label="Approve" selected={terminal.mockNextOutcome === 'approve'} onPress={() => terminal.setMockNextOutcome!('approve')} />
                  <Chip label="Decline" selected={terminal.mockNextOutcome === 'decline'} onPress={() => terminal.setMockNextOutcome!('decline')} />
                </Row>
              ) : null}

              {result ? (
                <View style={{ marginTop: 12 }}>
                  {result.kind === 'declined' ? <Banner tone="bad">{`Declined: ${result.message}`}</Banner> : null}
                  {result.kind === 'failed' ? <Banner tone="bad">{result.message}</Banner> : null}
                  {result.kind === 'canceled' ? <Banner tone="warn">Payment cancelled.</Banner> : null}
                  {result.kind === 'uncertain' ? (
                    <Banner tone="warn">
                      <Text style={{ color: colors.warn }}>{result.message}</Text>
                      <Button title="Check again" variant="secondary" style={{ marginTop: 8 }} busy={busy} onPress={recheck} />
                    </Banner>
                  ) : null}
                  {result.kind === 'record_pending' ? <Banner tone="warn">{result.message}</Banner> : null}
                </View>
              ) : null}

              {result?.kind !== 'record_pending' && result?.kind !== 'uncertain' ? (
                <Button
                  title={`${result ? 'Try again · ' : 'Charge '}${money(amount + tipCents)}`}
                  size="xl"
                  style={{ marginTop: 12 }}
                  busy={busy}
                  disabled={cardBlocked || !readerReady || amount <= 0 || amount > due}
                  onPress={payCardBbpos}
                />
              ) : null}
            </View>
          )}

          {method === 'card' && !cardViaBbpos && (
            <View style={{ marginTop: 12 }}>
              {pendingCard ? (
                <>
                  <Banner>Present the card on the store terminal.</Banner>
                  <Button title="Check terminal status" size="xl" onPress={confirmServerCard} />
                </>
              ) : (
                <>
                  <Muted>This store is not configured for bbpos card readers; the card is charged on the store's server-driven terminal.</Muted>
                  <Button title={`Charge ${money(amount)}`} size="xl" style={{ marginTop: 12 }} busy={busy} disabled={amount <= 0} onPress={payCardServer} />
                </>
              )}
            </View>
          )}
        </>
      )}
    </Sheet>
  );
}

const inputStyle = {
  borderWidth: 1,
  borderColor: colors.line,
  borderRadius: 10,
  paddingHorizontal: 12,
  paddingVertical: 12,
  fontSize: 20,
  backgroundColor: '#fff',
  marginTop: 6,
} as const;
