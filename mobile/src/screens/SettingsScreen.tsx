/** Native settings: POS server address, bbpos sign-in for card payments, queued card records. */
import React, { useState } from 'react';
import { View } from 'react-native';
import { Banner, Button, Card, Field, H2, Line, Muted, Row, colors } from '../components/ui';
import { bbpos } from '../lib/bbposApi';
import { money } from '../lib/format';
import { describeError, fetchWithTimeout } from '../lib/http';
import { APP_VERSION, useApp } from '../state/AppState';

export function SettingsScreen() {
  const app = useApp();
  const { bbposUser, bbposConfig, bbposError, pending } = app;
  const [server, setServer] = useState(app.serverUrl);
  const [bbposUrl, setBbposUrl] = useState(bbpos.baseUrl);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <View>
      <Banner tone="bad">{error}</Banner>
      <Banner tone="ok">{notice}</Banner>

      <Card>
        <H2>Card payments (bbpos)</H2>
        <Muted style={{ marginBottom: 8 }}>
          The tablet charges cards on the WisePad 3 through bbpos. Sign in once per tablet with a bbpos account that has the Cashier role
          (or higher). The URL is filled in from the store's payment settings in the franchise portal.
        </Muted>
        <Field label="bbpos API URL" value={bbposUrl} onChangeText={setBbposUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
        <Button
          title="Save URL"
          variant="secondary"
          busy={busy === 'bburl'}
          onPress={() =>
            run('bburl', async () => {
              await bbpos.setBaseUrl(bbposUrl);
              setBbposUrl(bbpos.baseUrl);
              await app.reloadBbpos();
              setNotice('bbpos URL saved');
            })
          }
        />
        <View style={{ height: 12 }} />
        {bbposUser ? (
          <>
            <Line label="Signed in as" value={`${bbposUser.name} (${bbposUser.role})`} />
            <Line label="Business" value={bbposUser.businessName} />
            {bbposConfig ? (
              <>
                <Line label="Stripe mode" value={bbposConfig.stripe.mode.toUpperCase()} color={bbposConfig.stripe.mode === 'live' ? colors.ok : colors.warn} />
                <Line label="Terminal location" value={bbposConfig.stripe.terminalLocationName ?? 'not configured'} />
                <Line label="Currency" value={bbposConfig.business.currency} />
                {!bbposConfig.pricing.allowCustomAmounts ? (
                  <Banner tone="bad">bbpos has “custom amounts” disabled. Enable it in bbpos admin → Settings → Pricing.</Banner>
                ) : null}
              </>
            ) : null}
            {bbposError ? <Banner tone="bad">{bbposError}</Banner> : null}
            <Row style={{ marginTop: 12 }}>
              <Button title="Refresh" variant="secondary" busy={busy === 'bbref'} onPress={() => run('bbref', app.reloadBbpos)} />
              <Button title="Sign out of bbpos" variant="secondary" busy={busy === 'bbout'} onPress={() => run('bbout', app.bbposLogout)} />
            </Row>
          </>
        ) : (
          <>
            <Field label="bbpos email" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" />
            <Field label="bbpos password" value={password} onChangeText={setPassword} secureTextEntry />
            <Button
              title="Sign in to bbpos"
              busy={busy === 'bblogin'}
              disabled={!email || !password || !bbpos.baseUrl}
              onPress={() =>
                run('bblogin', async () => {
                  await app.bbposLogin(email.trim(), password);
                  setPassword('');
                  setNotice('Signed in to bbpos');
                })
              }
            />
          </>
        )}
      </Card>

      {pending.length ? (
        <Card style={{ marginTop: 12 }}>
          <H2>Approved card payments waiting to be saved</H2>
          {pending.map((p) => (
            <Muted key={p.transactionId}>
              Order #{p.orderNumber} · {money(p.amountCents + p.tipCents)} · {p.cardLabel}
              {p.lastError ? ` — ${p.lastError}` : ''}
            </Muted>
          ))}
          <Button title="Retry now" variant="secondary" style={{ marginTop: 8 }} busy={busy === 'retry'} onPress={() => run('retry', app.retryPending)} />
        </Card>
      ) : null}

      <Card style={{ marginTop: 12 }}>
        <H2>POS server</H2>
        <Field
          label="Server URL"
          value={server}
          onChangeText={setServer}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          hint="The app opens <server>/pos. Production: https://pos.amrt.ca"
        />
        <Row>
          <Button
            title="Test"
            variant="secondary"
            busy={busy === 'test'}
            onPress={() =>
              run('test', async () => {
                const res = await fetchWithTimeout(`${server.replace(/\/+$/, '')}/healthz/`, {}, 10_000);
                if (res.ok) setNotice('Server reachable, API and database OK');
                else setError(`Server answered HTTP ${res.status}${res.status === 502 ? ' — the API container is down (deploy/TROUBLESHOOTING.md)' : ''}`);
              })
            }
          />
          <Button
            title="Save & reload POS"
            busy={busy === 'save'}
            onPress={() =>
              run('save', async () => {
                await app.setServerUrl(server);
                app.setPanel(null);
              })
            }
          />
        </Row>
        <Muted style={{ marginTop: 8 }}>App version {APP_VERSION}</Muted>
      </Card>
    </View>
  );
}
