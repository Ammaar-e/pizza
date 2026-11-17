/** Cash drawer for this tablet's register: open with a float, record movements, close with a count. */
import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { Banner, Button, Card, Chip, H2, Line, Muted, Row, Sheet, colors } from '../components/ui';
import { money, time, uuid } from '../lib/format';
import { ApiError, describeError } from '../lib/http';
import { can, pizza } from '../lib/pizzaApi';
import { useApp } from '../state/AppState';

const DENOMS = [10000, 5000, 2000, 1000, 500, 200, 100, 25, 10, 5];
const KINDS: [string, string][] = [
  ['drop', 'Drop to safe'],
  ['addition', 'Addition'],
  ['paid_out', 'Paid-out'],
  ['petty_expense', 'Petty expense'],
  ['correction', 'Correction'],
];
const cents = (s: string) => Math.round(Number(s.replace(',', '.')) * 100) || 0;

interface CashSession {
  id: string;
  register_name?: string;
  employee_name?: string;
  opened_at: string;
  opening_float_cents: number;
  expected_now_cents: number | null;
}
interface LedgerRow {
  id: string;
  kind: string;
  amount_cents: number;
  reason?: string;
  order_number?: number;
  created_at: string;
  user_name?: string;
}

export function CashScreen() {
  const { device } = useApp();
  const [session, setSession] = useState<CashSession | null>(null);
  const [ledger, setLedger] = useState<LedgerRow[]>([]);
  const [float, setFloat] = useState('');
  const [form, setForm] = useState({ kind: 'drop', amount: '', reason: '', category: '', mgr: '' });
  const [closing, setClosing] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await pizza.get<CashSession>('/cash-sessions/current/').catch((e) => {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      });
      setSession(s);
      setLedger(s ? await pizza.get<LedgerRow[]>(`/cash-sessions/${s.id}/transactions/`) : []);
      setError(null);
    } catch (e) {
      setError(describeError(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
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

  if (!device || device.pending) {
    return (
      <View style={{ padding: 16 }}>
        <Banner tone="warn">
          {device?.pending
            ? `This tablet (“${device.name}”) is awaiting approval in the franchise portal (Devices). Once approved, tap “Check approval” in Settings.`
            : 'The cash drawer needs a registered, approved device. Sign out and sign in as a manager on this tablet to register it.'}
        </Banner>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 60, maxWidth: 900, width: '100%', alignSelf: 'center' }} keyboardShouldPersistTaps="handled">
      <Banner tone="bad">{error}</Banner>
      <Banner tone="ok">{notice}</Banner>
      {report ? (
        <Card style={{ marginBottom: 12 }}>
          <H2>Closing report</H2>
          <Text style={{ fontFamily: 'Courier', fontSize: 13 }}>{report}</Text>
        </Card>
      ) : null}
      {!session ? (
        <Card>
          <H2>Open register</H2>
          <Muted style={{ marginBottom: 8 }}>Count the starting float. The register is assigned to you for this session.</Muted>
          <TextInput placeholder="Opening float (e.g. 150.00)" keyboardType="decimal-pad" value={float} onChangeText={setFloat} style={input} />
          <Button
            title="Open register"
            size="xl"
            style={{ marginTop: 12 }}
            disabled={!can('cash.manage_session')}
            onPress={() => run(() => pizza.post('/cash-sessions/open/', { opening_float_cents: cents(float) }), 'Register opened')}
          />
          {!can('cash.manage_session') ? <Muted style={{ marginTop: 6 }}>Your role cannot open a register.</Muted> : null}
        </Card>
      ) : (
        <>
          <Card>
            <H2>{session.register_name ?? 'Register'} · open</H2>
            <Muted>
              Responsible: {session.employee_name} · opened {time(session.opened_at)}
            </Muted>
            <Line label="Opening float" value={money(session.opening_float_cents)} />
            <Line
              label="Expected in drawer now"
              value={session.expected_now_cents === null ? 'Blind count (revealed after closing)' : money(session.expected_now_cents)}
              bold
            />
          </Card>
          <Card style={{ marginTop: 12 }}>
            <H2>Record cash movement</H2>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
              {KINDS.map(([k, l]) => (
                <Chip key={k} label={l} selected={form.kind === k} onPress={() => setForm({ ...form, kind: k })} />
              ))}
            </View>
            <TextInput placeholder={form.kind === 'correction' ? 'Amount (negative to remove)' : 'Amount (e.g. 20.00)'} keyboardType="numbers-and-punctuation" value={form.amount} onChangeText={(t) => setForm({ ...form, amount: t })} style={input} />
            <TextInput placeholder="Reason" value={form.reason} onChangeText={(t) => setForm({ ...form, reason: t })} style={input} />
            {form.kind === 'petty_expense' ? <TextInput placeholder="Expense category" value={form.category} onChangeText={(t) => setForm({ ...form, category: t })} style={input} /> : null}
            <TextInput placeholder="Manager code (if required)" secureTextEntry keyboardType="number-pad" value={form.mgr} onChangeText={(t) => setForm({ ...form, mgr: t })} style={input} />
            <Row style={{ marginTop: 12 }}>
              <Button
                title="Record"
                disabled={!form.amount}
                onPress={() =>
                  run(
                    () =>
                      pizza.post(
                        `/cash-sessions/${session.id}/transactions/`,
                        { kind: form.kind, amount_cents: cents(form.amount), reason: form.reason, expense_category: form.category, manager_code: form.mgr || undefined },
                        { 'Idempotency-Key': uuid() },
                      ),
                    'Recorded',
                  ).then(() => setForm({ ...form, amount: '', reason: '', mgr: '' }))
                }
              />
              <View style={{ flex: 1 }} />
              <Button title="Close & count register" variant="danger" onPress={() => setClosing(true)} />
            </Row>
          </Card>
          <Card style={{ marginTop: 12 }}>
            <H2>Drawer ledger</H2>
            {ledger.length === 0 ? <Muted>No movements yet.</Muted> : null}
            {ledger.map((t) => (
              <Row key={t.id} style={{ paddingVertical: 4 }}>
                <Muted style={{ width: 50 }}>{time(t.created_at)}</Muted>
                <Text style={{ flex: 1 }}>
                  {KINDS.find(([k]) => k === t.kind)?.[1] ?? t.kind} {t.order_number ? `· Order #${t.order_number}` : t.reason ? `· ${t.reason}` : ''}
                </Text>
                <Text style={{ color: t.amount_cents < 0 ? colors.bad : colors.ok, fontWeight: '600' }}>{money(t.amount_cents)}</Text>
              </Row>
            ))}
          </Card>
        </>
      )}
      {closing && session ? (
        <CloseSheet
          sessionId={session.id}
          onClose={() => setClosing(false)}
          onClosed={(r) => {
            setClosing(false);
            setReport(r);
            void load();
          }}
        />
      ) : null}
    </ScrollView>
  );
}

function CloseSheet({ sessionId, onClose, onClosed }: { sessionId: string; onClose: () => void; onClosed: (report: string) => void }) {
  const [counts, setCounts] = useState<Record<number, string>>({});
  const [explanation, setExplanation] = useState('');
  const [need, setNeed] = useState<{ variance_cents: number; threshold_cents: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const total = DENOMS.reduce((s, d) => s + d * Number(counts[d] || 0), 0);

  async function submit() {
    setError(null);
    try {
      const denominations = Object.fromEntries(DENOMS.filter((d) => Number(counts[d] || 0) > 0).map((d) => [String(d), Number(counts[d])]));
      const r = await pizza.post<Record<string, unknown>>(`/cash-sessions/${sessionId}/close/`, { counted_cash_cents: total, denominations, explanation });
      const v = r as { expected_cash_cents?: number; counted_cash_cents?: number; variance_cents?: number; status?: string };
      onClosed(
        [
          `Status:   ${v.status ?? 'closed'}`,
          `Expected: ${money(v.expected_cash_cents)}`,
          `Counted:  ${money(v.counted_cash_cents ?? total)}`,
          `Variance: ${money(v.variance_cents)}`,
        ].join('\n'),
      );
    } catch (e) {
      if (e instanceof ApiError && e.code === 'explanation_required') setNeed((e.errors?.[0] as unknown as typeof need) ?? { variance_cents: 0, threshold_cents: 0 });
      else setError(describeError(e));
    }
  }

  return (
    <Sheet visible title="Close register — count the drawer" onClose={onClose} footer={<Button title={`Close register · counted ${money(total)}`} size="lg" disabled={Boolean(need && !explanation.trim())} onPress={submit} />}>
      <Banner tone="bad">{error}</Banner>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {DENOMS.map((d) => (
          <Row key={d} style={{ width: 200, borderWidth: 1, borderColor: colors.line, borderRadius: 10, paddingHorizontal: 10, backgroundColor: '#fff' }}>
            <Text style={{ width: 70, fontWeight: '700' }}>{money(d)}</Text>
            <TextInput keyboardType="number-pad" placeholder="0" value={counts[d] ?? ''} onChangeText={(t) => setCounts({ ...counts, [d]: t })} style={{ flex: 1, padding: 10, fontSize: 17, textAlign: 'right' }} />
          </Row>
        ))}
      </View>
      {need ? (
        <View style={{ marginTop: 12 }}>
          <Banner tone="warn">{`The count differs from expected by ${money(need.variance_cents)} (allowed ±${money(need.threshold_cents)}). Explain the discrepancy; a manager must approve this closing.`}</Banner>
          <TextInput placeholder="Explanation" multiline value={explanation} onChangeText={setExplanation} style={[input, { height: 90 }]} />
        </View>
      ) : null}
    </Sheet>
  );
}

const input = {
  borderWidth: 1,
  borderColor: colors.line,
  borderRadius: 10,
  paddingHorizontal: 12,
  paddingVertical: 10,
  fontSize: 16,
  backgroundColor: '#fff',
  marginTop: 8,
} as const;
