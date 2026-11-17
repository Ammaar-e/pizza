/** Today's orders for the store: status, payment, kitchen actions, refunds and receipts. */
import React, { useCallback, useEffect, useState } from 'react';
import { FlatList, Pressable, RefreshControl, Text, View } from 'react-native';
import { PromptSheet, usePrompt } from '../components/PromptSheet';
import { Banner, Button, Card, Chip, H2, Line, Muted, Row, Sheet, colors } from '../components/ui';
import { money, time, titleCase } from '../lib/format';
import { describeError } from '../lib/http';
import { pizza } from '../lib/pizzaApi';
import type { Order, Page } from '../lib/types';
import { useApp } from '../state/AppState';
import { PaymentSheet } from './PaymentSheet';

const FILTERS: [string, string][] = [
  ['open', 'Open'],
  ['unpaid', 'Unpaid'],
  ['all', 'All today'],
];

export function OrdersScreen() {
  const { session, pending, retryPending } = useApp();
  const store = session!.store;
  const [orders, setOrders] = useState<Order[]>([]);
  const [filter, setFilter] = useState('open');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await pizza.get<Page<Order>>(`/orders/?store=${store.id}&date=today&page_size=200`);
      setOrders(r.results);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [store.id]);

  useEffect(() => {
    void load();
    const t = setInterval(load, 20_000); // safety-net polling
    return () => clearInterval(t);
  }, [load]);

  const shown = orders.filter((o) =>
    filter === 'all' ? true : filter === 'unpaid' ? ['unpaid', 'partially_paid'].includes(o.payment_status) : !['completed', 'cancelled', 'rejected'].includes(o.status),
  );

  return (
    <View style={{ flex: 1 }}>
      <View style={{ padding: 12 }}>
        {pending.length ? (
          <Banner tone="warn">
            <Text style={{ color: colors.warn, fontWeight: '700' }}>
              {pending.length} approved card payment(s) not yet saved to their order — retrying automatically.
            </Text>
            {pending.map((p) => (
              <Muted key={p.transactionId}>
                Order #{p.orderNumber} · {money(p.amountCents + p.tipCents)} · {p.cardLabel}
                {p.lastError ? ` — ${p.lastError}` : ''}
              </Muted>
            ))}
            <Button title="Retry now" variant="secondary" size="sm" style={{ marginTop: 8 }} onPress={() => void retryPending().then(load)} />
          </Banner>
        ) : null}
        <Banner tone="bad">{error}</Banner>
        <Row>
          {FILTERS.map(([k, label]) => (
            <Chip key={k} label={label} selected={filter === k} onPress={() => setFilter(k)} />
          ))}
        </Row>
      </View>
      <FlatList
        data={shown}
        keyExtractor={(o) => o.id}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 40 }}
        ListEmptyComponent={<Muted style={{ padding: 16 }}>No orders.</Muted>}
        renderItem={({ item: o }) => (
          <Pressable onPress={() => setSelected(o.id)}>
            <Card style={{ marginBottom: 8, flexDirection: 'row', alignItems: 'center' }}>
              <Text style={{ fontSize: 22, fontWeight: '800', width: 70 }}>#{o.order_number}</Text>
              <View style={{ flex: 1 }}>
                <Text style={{ fontWeight: '700' }}>
                  {titleCase(o.status)} · {titleCase(o.order_type)} {o.source !== 'pos' ? `· ${titleCase(o.source)}` : ''}
                </Text>
                <Muted>
                  {time(o.placed_at)} {o.customer_name ? `· ${o.customer_name}` : ''} {o.item_count ? `· ${o.item_count} items` : ''}
                </Muted>
              </View>
              <View style={{ alignItems: 'flex-end' }}>
                <Text style={{ fontWeight: '700' }}>{money(o.total_cents)}</Text>
                <Text style={{ color: o.payment_status === 'paid' ? colors.ok : colors.warn, fontWeight: '600' }}>{titleCase(o.payment_status)}</Text>
              </View>
            </Card>
          </Pressable>
        )}
      />
      {selected ? (
        <OrderDetail
          id={selected}
          onClose={() => {
            setSelected(null);
            void load();
          }}
        />
      ) : null}
    </View>
  );
}

function OrderDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [o, setO] = useState<Order | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);
  const [receipt, setReceipt] = useState<string | null>(null);
  const prompt = usePrompt();

  const load = useCallback(async () => {
    try {
      setO(await pizza.get<Order>(`/orders/${id}/`));
    } catch (e) {
      setError(describeError(e));
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(ok);
      await load();
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function cancel() {
    const reason = await prompt.ask({ title: 'Cancel order', label: 'Reason', confirm: 'Cancel order' });
    if (!reason) return;
    const mgr = await prompt.ask({ title: 'Manager code', message: 'Leave blank if not required.', label: 'Manager code', secure: true, confirm: 'Continue' });
    await act(() => pizza.post(`/orders/${id}/cancel/`, { reason, manager_code: mgr || undefined }), 'Order cancelled');
  }

  async function refund(paymentId: string, max: number) {
    const amt = await prompt.ask({ title: 'Refund', message: `Up to ${money(max)}`, label: 'Amount (e.g. 5.00)', numeric: false, initial: (max / 100).toFixed(2) });
    if (!amt) return;
    const reason = await prompt.ask({ title: 'Refund reason', label: 'Reason' });
    if (!reason) return;
    const mgr = await prompt.ask({ title: 'Manager code', message: 'Required by most store policies.', label: 'Manager code', secure: true });
    await act(
      () => pizza.post(`/payments/${paymentId}/refund/`, { amount_cents: Math.round(Number(amt.replace(',', '.')) * 100), reason, manager_code: mgr || undefined }),
      'Refund submitted. Interac refunds stay pending until processed with the card present in bbpos.',
    );
  }

  async function showReceipt() {
    try {
      const r = await pizza.get<{ text?: string; lines?: string[] } | string>(`/orders/${id}/receipt/`);
      setReceipt(typeof r === 'string' ? r : (r.text ?? (r.lines ?? []).join('\n') ?? JSON.stringify(r, null, 2)));
    } catch (e) {
      setError(describeError(e));
    }
  }

  const due = o ? o.total_cents - (o.amount_paid_cents ?? 0) : 0;
  const terminal = o && ['completed', 'cancelled', 'rejected'].includes(o.status);

  return (
    <Sheet visible title={o ? `Order #${o.order_number}` : 'Order'} onClose={onClose}>
      <Banner tone="bad">{error}</Banner>
      <Banner tone="ok">{notice}</Banner>
      {!o ? (
        <Muted>Loading…</Muted>
      ) : (
        <>
          <Muted>
            {titleCase(o.status)} · {titleCase(o.order_type)} · {titleCase(o.payment_status)} · {time(o.placed_at)}
          </Muted>
          {o.customer_snapshot?.name || o.customer_snapshot?.phone ? (
            <Text style={{ marginTop: 4 }}>
              {o.customer_snapshot?.name} {o.customer_snapshot?.phone}
            </Text>
          ) : null}
          <Card style={{ marginTop: 12 }}>
            {o.items?.map((it) => (
              <View key={it.id} style={{ marginBottom: 6 }}>
                <Line label={`${it.quantity} × ${it.size_name ? `${it.size_name} ` : ''}${it.name}`} value={money(it.line_total_cents)} />
                {it.crust_name ? <Muted>{it.crust_name} crust</Muted> : null}
                {it.modifiers?.filter((m) => !m.is_default || m.quantity > 1 || m.placement !== 'whole').map((m, i) => (
                  <Muted key={i}>
                    {m.quantity > 1 ? 'Extra ' : '+ '}
                    {m.name}
                    {m.placement !== 'whole' ? ` (${m.placement})` : ''}
                  </Muted>
                ))}
                {it.removed?.map((r) => <Muted key={r}>No {r}</Muted>)}
              </View>
            ))}
            <View style={{ borderTopWidth: 1, borderColor: colors.line, marginTop: 6, paddingTop: 6 }}>
              <Line label="Subtotal" value={money(o.subtotal_cents)} />
              {o.discount_cents ? <Line label="Discounts" value={`−${money(o.discount_cents)}`} color={colors.ok} /> : null}
              <Line label="Tax" value={money(o.tax_cents)} />
              {o.tip_cents ? <Line label="Tips" value={money(o.tip_cents)} /> : null}
              <Line label="Total" value={money(o.total_cents)} bold />
              <Line label="Paid" value={money(o.amount_paid_cents ?? 0)} />
            </View>
          </Card>

          <H2 style={{ marginTop: 16 }}>Payments</H2>
          {o.payments?.length ? null : <Muted>None yet.</Muted>}
          {o.payments?.map((p) => (
            <Row key={p.id} style={{ justifyContent: 'space-between', paddingVertical: 6 }}>
              <Text style={{ flex: 1 }}>
                {titleCase(p.method)}
                {p.card_last4 ? ` ${p.card_brand?.toUpperCase() ?? ''} •••• ${p.card_last4}` : ''} · {titleCase(p.status)}
              </Text>
              <Text style={{ fontWeight: '600' }}>{money(p.amount_cents)}</Text>
              {['captured', 'partially_refunded'].includes(p.status) && p.method !== 'provider' ? (
                <Button title="Refund" variant="secondary" size="sm" onPress={() => refund(p.id, p.amount_cents - (p.refunded_cents ?? 0))} />
              ) : null}
            </Row>
          ))}

          <View style={{ gap: 8, marginTop: 16 }}>
            {due > 0 && !terminal && o.source === 'pos' ? <Button title={`Take payment · ${money(due)}`} size="lg" onPress={() => setPaying(true)} /> : null}
            {o.status === 'pending_review' ? (
              <Row>
                <Button title="Accept" variant="success" style={{ flex: 1 }} onPress={() => act(() => pizza.post(`/orders/${id}/accept/`, {}), 'Accepted')} />
                <Button title="Reject" variant="danger" style={{ flex: 1 }} onPress={() => act(() => pizza.post(`/orders/${id}/reject/`, { reason: 'ITEM_UNAVAILABLE' }), 'Rejected')} />
              </Row>
            ) : null}
            {['accepted', 'preparing'].includes(o.status) ? <Button title="Mark ready" variant="secondary" onPress={() => act(() => pizza.post(`/orders/${id}/status/`, { status: 'ready' }), 'Ready')} /> : null}
            {o.status === 'ready' && o.order_type === 'delivery' ? (
              <Button title="Out for delivery" variant="secondary" onPress={() => act(() => pizza.post(`/orders/${id}/status/`, { status: 'out_for_delivery' }), 'Out for delivery')} />
            ) : null}
            {['ready', 'out_for_delivery'].includes(o.status) ? (
              <Button title="Complete" variant="secondary" onPress={() => act(() => pizza.post(`/orders/${id}/status/`, { status: 'completed' }), 'Completed')} />
            ) : null}
            <Button title="Receipt" variant="secondary" onPress={showReceipt} />
            {!terminal ? <Button title="Cancel order" variant="danger" onPress={cancel} /> : null}
          </View>
          {receipt ? (
            <Card style={{ marginTop: 12 }}>
              <Text style={{ fontFamily: 'Courier', fontSize: 13 }}>{receipt}</Text>
            </Card>
          ) : null}
        </>
      )}
      {paying && o ? (
        <PaymentSheet
          order={o}
          onClose={() => {
            setPaying(false);
            void load();
          }}
          onPaid={() => void load()}
        />
      ) : null}
      <PromptSheet {...prompt.sheet} />
    </Sheet>
  );
}
