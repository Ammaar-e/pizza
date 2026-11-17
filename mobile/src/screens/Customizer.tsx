/** Pizza/product customizer: size, crust, included toppings (remove), extra toppings with halves, quantity. */
import React, { useMemo, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { Button, H2, Muted, Row, Sheet, colors } from '../components/ui';
import { money, uuid } from '../lib/format';
import { estimateUnit } from '../lib/pricing';
import type { CartLine, LineMod, MenuProduct, Placement } from '../lib/types';

export function newLine(p: MenuProduct): CartLine {
  return {
    key: uuid(),
    product: p,
    size: p.sizes.find((s) => s.code === 'L')?.code ?? p.sizes[0]?.code,
    crust: p.default_crust ?? p.crusts[0]?.code,
    quantity: 1,
    modifiers: [],
    removed: [],
    special_instructions: '',
  };
}

function Tile({ selected, onPress, title, sub, disabled, tone }: { selected?: boolean; onPress?: () => void; title: string; sub?: string; disabled?: boolean; tone?: 'ok' | 'bad' }) {
  const border = tone === 'bad' ? colors.bad : tone === 'ok' ? colors.ok : selected ? colors.brand : colors.line;
  return (
    <Pressable
      disabled={disabled}
      onPress={onPress}
      style={{
        borderWidth: 2,
        borderColor: border,
        backgroundColor: selected ? colors.brandSoft : colors.card,
        borderRadius: 12,
        padding: 10,
        minWidth: 120,
        flexGrow: 1,
        opacity: disabled ? 0.4 : 1,
        marginRight: 8,
        marginBottom: 8,
      }}
    >
      <Text style={{ fontWeight: '700', color: colors.ink, textDecorationLine: tone === 'bad' ? 'line-through' : 'none' }}>{title}</Text>
      {sub ? <Text style={{ color: colors.muted, fontSize: 13 }}>{sub}</Text> : null}
    </Pressable>
  );
}

export function Customizer({ product, initial, onClose, onSave }: { product: MenuProduct; initial?: CartLine; onClose: () => void; onSave: (l: CartLine) => void }) {
  const p = product;
  const defaults = new Set(p.default_modifiers.map((d) => d.code));
  const [line, setLine] = useState<CartLine>(initial ?? newLine(p));
  const unit = useMemo(() => estimateUnit(line), [line]);

  const modState = (code: string) => line.modifiers.find((m) => m.code === code);
  const setMod = (code: string, next: LineMod | null) => {
    const rest = line.modifiers.filter((m) => m.code !== code);
    setLine({ ...line, modifiers: next ? [...rest, next] : rest });
  };
  const toggleDefault = (code: string) => {
    const removed = line.removed.includes(code) ? line.removed.filter((c) => c !== code) : [...line.removed, code];
    setLine({ ...line, removed, modifiers: line.modifiers.filter((m) => m.code !== code) });
  };

  return (
    <Sheet
      visible
      title={`${p.name} · ${money(unit * line.quantity)}`}
      onClose={onClose}
      footer={
        <Row>
          <Button title="−" variant="secondary" size="lg" onPress={() => setLine({ ...line, quantity: Math.max(1, line.quantity - 1) })} />
          <Text style={{ fontSize: 22, fontWeight: '800', width: 40, textAlign: 'center' }}>{line.quantity}</Text>
          <Button title="+" variant="secondary" size="lg" onPress={() => setLine({ ...line, quantity: line.quantity + 1 })} />
          <Button
            title={`${initial ? 'Update' : 'Add'} · ${money(unit * line.quantity)}`}
            size="xl"
            style={{ flex: 1 }}
            onPress={() => onSave(line)}
          />
        </Row>
      }
    >
      {p.sizes.length > 0 && (
        <View style={{ marginBottom: 16 }}>
          <H2>Size</H2>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {p.sizes.map((s) => (
              <Tile key={s.code} selected={line.size === s.code} title={s.name} sub={money(s.price_cents)} onPress={() => setLine({ ...line, size: s.code })} />
            ))}
          </View>
        </View>
      )}
      {p.crusts.length > 0 && (
        <View style={{ marginBottom: 16 }}>
          <H2>Crust</H2>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {p.crusts.map((c) => (
              <Tile
                key={c.code}
                selected={line.crust === c.code}
                title={c.name}
                sub={c.price_cents ? `+${money(c.price_cents)}` : undefined}
                onPress={() => setLine({ ...line, crust: c.code })}
              />
            ))}
          </View>
        </View>
      )}
      {p.default_modifiers.length > 0 && (
        <View style={{ marginBottom: 16 }}>
          <H2>Included — tap to remove</H2>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {p.default_modifiers.map((d) => {
              const removed = line.removed.includes(d.code);
              return (
                <Tile key={d.code} disabled={!d.removable} tone={removed ? 'bad' : 'ok'} title={`${removed ? 'No ' : ''}${d.name}`} onPress={() => toggleDefault(d.code)} />
              );
            })}
          </View>
        </View>
      )}
      {p.modifier_groups.map((g) => {
        const count = g.modifiers.filter((m) => modState(m.code) || (defaults.has(m.code) && !line.removed.includes(m.code))).length;
        return (
          <View key={g.code} style={{ marginBottom: 16 }}>
            <H2>
              {g.name}{' '}
              <Text style={{ fontSize: 14, fontWeight: '400', color: colors.muted }}>
                ({count}
                {g.max_select ? ` / max ${g.max_select}` : ''}
                {g.min_select ? `, min ${g.min_select}` : ''})
              </Text>
            </H2>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
              {g.modifiers.map((m) => {
                const st = modState(m.code);
                const isDefault = defaults.has(m.code) && !line.removed.includes(m.code);
                const halves = p.allow_half_and_half && g.allow_half;
                return (
                  <View key={m.code} style={{ minWidth: 150, flexGrow: 1, marginRight: 8, marginBottom: 8 }}>
                    <Tile
                      disabled={!m.is_available}
                      selected={Boolean(st)}
                      tone={!st && isDefault ? 'ok' : undefined}
                      title={`${m.name}${m.is_available ? '' : ' (86)'}`}
                      sub={isDefault ? 'included' : m.price_cents ? `+${money(m.price_cents)}` : undefined}
                      onPress={() => setMod(m.code, st ? null : { code: m.code, placement: 'whole', quantity: isDefault ? 2 : 1 })}
                    />
                    {st ? (
                      <Row style={{ flexWrap: 'wrap', marginTop: -4 }}>
                        {halves
                          ? (['left', 'whole', 'right'] as Placement[]).map((pl) => (
                              <Button
                                key={pl}
                                size="sm"
                                variant={st.placement === pl ? 'primary' : 'secondary'}
                                title={pl === 'whole' ? 'Whole' : pl === 'left' ? 'Left ½' : 'Right ½'}
                                onPress={() => setMod(m.code, { ...st, placement: pl })}
                              />
                            ))
                          : null}
                        {g.max_quantity_per_option > 1 ? (
                          <Button size="sm" variant={st.quantity > 1 ? 'dark' : 'secondary'} title="Extra" onPress={() => setMod(m.code, { ...st, quantity: st.quantity > 1 ? 1 : 2 })} />
                        ) : null}
                      </Row>
                    ) : null}
                  </View>
                );
              })}
            </View>
          </View>
        );
      })}
      <H2>Special instructions</H2>
      <TextInput
        value={line.special_instructions}
        onChangeText={(t) => setLine({ ...line, special_instructions: t })}
        placeholder="e.g. well done"
        style={{ borderWidth: 1, borderColor: colors.line, borderRadius: 10, padding: 12, fontSize: 16, backgroundColor: '#fff' }}
      />
      <Muted style={{ marginTop: 8 }}>Prices shown are estimates; the server quote in the cart is authoritative.</Muted>
    </Sheet>
  );
}
