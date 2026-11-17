/**
 * New order: menu → customizer → cart with a live server quote (pricing, tax, promotions,
 * delivery eligibility) → send to kitchen, optionally taking payment immediately.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FlatList, Pressable, ScrollView, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { PromptSheet, usePrompt } from '../components/PromptSheet';
import { Banner, Button, Card, Chip, H2, Line, Muted, Row, Segmented, colors } from '../components/ui';
import { money, uuid } from '../lib/format';
import { ApiError, describeError } from '../lib/http';
import { pizza } from '../lib/pizzaApi';
import { describeLine, estimateUnit, lineToApi } from '../lib/pricing';
import type { CartLine, Menu, MenuProduct, Order, Quote } from '../lib/types';
import { useApp } from '../state/AppState';
import { Customizer, newLine } from './Customizer';
import { PaymentSheet } from './PaymentSheet';

type OrderType = 'pickup' | 'delivery' | 'dine_in';

export function OrderScreen() {
  const { session, device } = useApp();
  const store = session!.store;
  const { width } = useWindowDimensions();
  const wide = width >= 900;

  const [menu, setMenu] = useState<Menu | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [editing, setEditing] = useState<{ product: MenuProduct; line?: CartLine } | null>(null);
  const [orderType, setOrderType] = useState<OrderType>('pickup');
  const [phoneOrder, setPhoneOrder] = useState(false);
  const [customer, setCustomer] = useState({ name: '', phone: '' });
  const [address, setAddress] = useState({ line1: '', city: store.city ?? '', postal_code: '', instructions: '' });
  const [notes, setNotes] = useState('');
  const [codes, setCodes] = useState<string[]>([]);
  const [promoIds, setPromoIds] = useState<number[]>([]);
  const [promoInput, setPromoInput] = useState('');
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [payOrder, setPayOrder] = useState<Order | null>(null);
  const [showCart, setShowCart] = useState(false);
  const prompt = usePrompt();

  const loadMenu = useCallback(async () => {
    try {
      const m = await pizza.get<Menu>(`/stores/${store.id}/menu/`);
      setMenu(m);
      setCategory((c) => c ?? m.categories[0]?.code ?? null);
      setError(null);
    } catch (e) {
      setError(`Menu could not be loaded: ${describeError(e)}`);
    }
  }, [store.id]);
  useEffect(() => {
    void loadMenu();
  }, [loadMenu]);

  // Server-side pricing and promotions for the current cart (debounced).
  useEffect(() => {
    if (!cart.length) {
      setQuote(null);
      setQuoteError(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        setQuote(
          await pizza.post<Quote>(`/stores/${store.id}/quote/`, {
            order_type: orderType,
            items: cart.map(lineToApi),
            promo_codes: codes,
            promotion_ids: promoIds,
            customer: customer.phone ? { phone: customer.phone } : {},
            delivery_address: orderType === 'delivery' ? address : undefined,
          }),
        );
        setQuoteError(null);
      } catch (e) {
        setQuote(null);
        setQuoteError(describeError(e));
      }
    }, 350);
    return () => clearTimeout(t);
  }, [cart, orderType, codes, promoIds, customer.phone, address, store.id]);

  const products = useMemo(() => menu?.categories.find((c) => c.code === category)?.products ?? [], [menu, category]);
  const estimate = cart.reduce((s, l) => s + estimateUnit(l) * l.quantity, 0);

  function tapProduct(p: MenuProduct) {
    if (!p.is_available) return;
    if (p.sizes.length || p.crusts.length || p.modifier_groups.length) setEditing({ product: p });
    else setCart([...cart, newLine(p)]);
  }

  function reset() {
    setCart([]);
    setCodes([]);
    setPromoIds([]);
    setQuote(null);
    setCustomer({ name: '', phone: '' });
    setAddress({ line1: '', city: store.city ?? '', postal_code: '', instructions: '' });
    setNotes('');
    setPhoneOrder(false);
    setOrderType('pickup');
    setShowCart(false);
  }

  async function submit(payNow: boolean) {
    if (!cart.length) return;
    setError(null);
    setNotice(null);
    setSubmitting(true);
    const order = {
      store_id: store.id,
      order_type: orderType,
      entry_channel: phoneOrder ? 'phone' : 'walk_in',
      items: cart.map(lineToApi),
      customer: customer.name || customer.phone ? customer : undefined,
      delivery_address: orderType === 'delivery' ? address : undefined,
      special_instructions: notes,
      promo_codes: codes,
      promotion_ids: promoIds,
    };
    const ref = `${device?.id ?? 'app'}-${uuid()}`;
    try {
      let r: Order;
      try {
        r = await pizza.post<Order>('/orders/', order, { 'Idempotency-Key': ref });
      } catch (e) {
        if (!(e instanceof ApiError && e.code === 'manager_authorization_required')) throw e;
        const code = await prompt.ask({ title: 'Manager authorization', message: 'This promotion requires a manager code.', label: 'Manager code', secure: true });
        if (!code) throw e;
        r = await pizza.post<Order>('/orders/', { ...order, manager_code: code }, { 'Idempotency-Key': ref });
      }
      reset();
      if (payNow) setPayOrder(r);
      else setNotice(`Order #${r.order_number} sent to the kitchen — unpaid`);
    } catch (e) {
      setError(e instanceof ApiError && e.isNetwork ? 'Cannot reach the central server — the order was NOT sent. Check the connection and try again.' : describeError(e));
    } finally {
      setSubmitting(false);
    }
  }

  const menuPane = (
    <View style={{ flex: 1 }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0, paddingVertical: 8 }} contentContainerStyle={{ paddingHorizontal: 12 }}>
        {menu?.categories.map((c) => <Chip key={c.code} label={c.name} selected={c.code === category} onPress={() => setCategory(c.code)} />)}
      </ScrollView>
      <FlatList
        key={wide ? 'w' : 'n'}
        data={products}
        numColumns={wide ? 3 : 2}
        keyExtractor={(p) => String(p.id)}
        contentContainerStyle={{ padding: 8, paddingBottom: 120 }}
        renderItem={({ item: p }) => (
          <Pressable
            onPress={() => tapProduct(p)}
            style={({ pressed }) => ({
              flex: 1,
              margin: 6,
              minHeight: 96,
              padding: 14,
              borderRadius: 14,
              backgroundColor: colors.card,
              borderWidth: 1,
              borderColor: colors.line,
              opacity: !p.is_available ? 0.4 : pressed ? 0.7 : 1,
            })}
          >
            <Text style={{ fontSize: 17, fontWeight: '700', color: colors.ink }}>{p.name}</Text>
            <Muted>{p.is_available ? (p.sizes[0] ? `from ${money(Math.min(...p.sizes.map((s) => s.price_cents)))}` : money(p.price_cents)) : 'Unavailable (86)'}</Muted>
          </Pressable>
        )}
        ListEmptyComponent={<Muted style={{ padding: 16 }}>{menu ? 'No products in this category.' : 'Loading menu…'}</Muted>}
      />
    </View>
  );

  const cartPane = (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 12, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <Segmented<OrderType> value={orderType} onChange={setOrderType} options={[['pickup', 'Pickup'], ['delivery', 'Delivery'], ['dine_in', 'Dine in']]} />
      <Row style={{ marginTop: 8 }}>
        <Chip label={phoneOrder ? '☎ Phone order' : 'Walk-in'} selected={phoneOrder} onPress={() => setPhoneOrder(!phoneOrder)} />
      </Row>
      <Row>
        <TextInput placeholder="Customer name" value={customer.name} onChangeText={(t) => setCustomer({ ...customer, name: t })} style={inputStyle} />
        <TextInput placeholder="Phone" keyboardType="phone-pad" value={customer.phone} onChangeText={(t) => setCustomer({ ...customer, phone: t })} style={inputStyle} />
      </Row>
      {orderType === 'delivery' && (
        <View>
          <TextInput placeholder="Street address" value={address.line1} onChangeText={(t) => setAddress({ ...address, line1: t })} style={[inputStyle, { marginTop: 8 }]} />
          <Row>
            <TextInput placeholder="City" value={address.city} onChangeText={(t) => setAddress({ ...address, city: t })} style={inputStyle} />
            <TextInput placeholder="Postal code" autoCapitalize="characters" value={address.postal_code} onChangeText={(t) => setAddress({ ...address, postal_code: t })} style={inputStyle} />
          </Row>
          <TextInput placeholder="Delivery instructions" value={address.instructions} onChangeText={(t) => setAddress({ ...address, instructions: t })} style={[inputStyle, { marginTop: 8 }]} />
        </View>
      )}

      <H2 style={{ marginTop: 16 }}>Cart</H2>
      {cart.length === 0 ? <Muted>Tap products to add them.</Muted> : null}
      {cart.map((l, idx) => {
        const ql = quote?.lines?.find((x) => x.line === idx);
        return (
          <Pressable key={l.key} onPress={() => setEditing({ product: l.product, line: l })} style={{ paddingVertical: 8, borderBottomWidth: 1, borderColor: colors.line }}>
            <Row style={{ justifyContent: 'space-between' }}>
              <Text style={{ fontWeight: '700', fontSize: 16, flex: 1 }}>
                {l.quantity} × {l.product.sizes.find((s) => s.code === l.size)?.name ?? ''} {l.product.name}
              </Text>
              <Text style={{ fontWeight: '600' }}>{money(ql?.line_total_cents ?? estimateUnit(l) * l.quantity)}</Text>
              <Button title="✕" variant="ghost" size="sm" onPress={() => setCart(cart.filter((x) => x.key !== l.key))} />
            </Row>
            {describeLine(l).map((d, i) => (
              <Muted key={i}>{d}</Muted>
            ))}
            {ql?.issues?.map((iss, i) => (
              <Text key={i} style={{ color: colors.bad }}>
                {iss.message}
              </Text>
            ))}
          </Pressable>
        );
      })}

      <Row style={{ marginTop: 12 }}>
        <TextInput placeholder="Promo code" autoCapitalize="characters" value={promoInput} onChangeText={setPromoInput} style={inputStyle} />
        <Button
          title="Apply"
          variant="secondary"
          disabled={!promoInput.trim()}
          onPress={() => {
            setCodes([...new Set([...codes, promoInput.trim().toUpperCase()])]);
            setPromoInput('');
          }}
        />
      </Row>
      {quote?.promotions?.applied?.map((a) => (
        <Row key={a.promotion_id} style={{ justifyContent: 'space-between' }}>
          <Text style={{ color: colors.ok, flex: 1 }}>
            🏷 {a.name}
            {a.code ? ` (${a.code})` : ''}
          </Text>
          <Text style={{ color: colors.ok }}>−{money(a.amount_cents)}</Text>
          {a.code || promoIds.includes(a.promotion_id) ? (
            <Button
              title="✕"
              variant="ghost"
              size="sm"
              onPress={() => {
                setCodes(codes.filter((c) => c !== a.code));
                setPromoIds(promoIds.filter((i) => i !== a.promotion_id));
              }}
            />
          ) : null}
        </Row>
      ))}
      {quote?.promotions?.rejected
        ?.filter((r) => r.code || promoIds.includes(r.promotion_id))
        .map((r, i) => (
          <Text key={i} style={{ color: colors.bad }}>
            {r.code ?? r.name}: {r.message ?? r.reason}
          </Text>
        ))}
      {quote?.promotions?.available?.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginTop: 6 }}>
          {quote.promotions.available.map((a) => (
            <Chip key={a.promotion_id} label={`+ ${a.name}`} tone="ok" onPress={() => setPromoIds([...promoIds, a.promotion_id])} />
          ))}
        </View>
      ) : null}
      {quote?.delivery && quote.delivery.eligible === false ? <Banner tone="warn">{quote.delivery.message ?? 'Address is outside the delivery area'}</Banner> : null}

      <TextInput placeholder="Order notes" value={notes} onChangeText={setNotes} style={[inputStyle, { marginTop: 8 }]} />

      <Card style={{ marginTop: 12 }}>
        {quote ? (
          <>
            <Line label="Subtotal" value={money(quote.subtotal_cents)} />
            {quote.discount_cents > 0 ? <Line label="Discounts" value={`−${money(quote.discount_cents)}`} color={colors.ok} /> : null}
            {quote.charges.map((c) => (
              <Line key={c.code} label={c.name} value={money(c.amount_cents)} />
            ))}
            <Line label="Tax" value={money(quote.tax_cents)} />
            <Line label="Total" value={money(quote.total_cents)} bold />
          </>
        ) : (
          <Line label={cart.length ? 'Estimated subtotal' : 'Total'} value={money(estimate)} bold />
        )}
        {quoteError ? <Text style={{ color: colors.bad, marginTop: 6 }}>{quoteError}</Text> : null}
      </Card>
      <Row style={{ marginTop: 12 }}>
        <Button title="Send unpaid" variant="secondary" size="lg" style={{ flex: 1 }} busy={submitting} disabled={!cart.length} onPress={() => submit(false)} />
        <Button title={`Pay ${quote ? money(quote.total_cents) : ''}`} size="lg" style={{ flex: 1.4 }} busy={submitting} disabled={!cart.length} onPress={() => submit(true)} />
      </Row>
      {cart.length ? <Button title="Clear cart" variant="ghost" size="sm" onPress={reset} style={{ marginTop: 8 }} /> : null}
    </ScrollView>
  );

  return (
    <View style={{ flex: 1 }}>
      {error ? (
        <View style={{ paddingHorizontal: 12, paddingTop: 8 }}>
          <Banner tone="bad">{error}</Banner>
          {!menu ? <Button title="Retry" variant="secondary" size="sm" onPress={loadMenu} /> : null}
        </View>
      ) : null}
      {notice ? (
        <View style={{ paddingHorizontal: 12, paddingTop: 8 }}>
          <Banner tone="ok">{notice}</Banner>
        </View>
      ) : null}
      {wide ? (
        <View style={{ flex: 1, flexDirection: 'row' }}>
          <View style={{ flex: 1.6 }}>{menuPane}</View>
          <View style={{ flex: 1, borderLeftWidth: 1, borderColor: colors.line, backgroundColor: colors.card }}>{cartPane}</View>
        </View>
      ) : showCart ? (
        <View style={{ flex: 1, backgroundColor: colors.card }}>
          <Button title="← Back to menu" variant="ghost" size="sm" onPress={() => setShowCart(false)} style={{ alignSelf: 'flex-start' }} />
          {cartPane}
        </View>
      ) : (
        <View style={{ flex: 1 }}>
          {menuPane}
          {cart.length ? (
            <View style={{ position: 'absolute', left: 12, right: 12, bottom: 12 }}>
              <Button
                title={`View cart · ${cart.reduce((s, l) => s + l.quantity, 0)} items · ${money(quote?.total_cents ?? estimate)}`}
                size="xl"
                onPress={() => setShowCart(true)}
              />
            </View>
          ) : null}
        </View>
      )}

      {editing ? (
        <Customizer
          product={editing.product}
          initial={editing.line}
          onClose={() => setEditing(null)}
          onSave={(l) => {
            setCart(editing.line ? cart.map((x) => (x.key === l.key ? l : x)) : [...cart, l]);
            setEditing(null);
          }}
        />
      ) : null}
      {payOrder ? (
        <PaymentSheet
          order={payOrder}
          onClose={() => setPayOrder(null)}
          onPaid={(o) => setNotice(`Order #${o.order_number} paid (${o.payment_status})`)}
        />
      ) : null}
      <PromptSheet {...prompt.sheet} />
    </View>
  );
}

const inputStyle = {
  flex: 1,
  minWidth: 0,
  borderWidth: 1,
  borderColor: colors.line,
  borderRadius: 10,
  paddingHorizontal: 10,
  paddingVertical: 10,
  fontSize: 16,
  backgroundColor: '#fff',
  marginTop: 8,
} as const;
