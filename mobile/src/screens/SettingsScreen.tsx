/** Settings: servers, bbpos sign-in for card payments, device status, sign-out. */
import React, { useState } from 'react';
import { ScrollView } from 'react-native';
import { Banner, Button, Card, Field, H2, Line, Muted, Row, colors } from '../components/ui';
import { bbpos } from '../lib/bbposApi';
import { describeError } from '../lib/http';
import { pizza } from '../lib/pizzaApi';
import { APP_VERSION, useApp } from '../state/AppState';

export function SettingsScreen() {
  const app = useApp();
  const { session, device, deviceState, bbposUser, bbposConfig, bbposError, cardViaBbpos } = app;
  const [pizzaUrl, setPizzaUrl] = useState(pizza.baseUrl);
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
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 60, maxWidth: 800, width: '100%', alignSelf: 'center' }} keyboardShouldPersistTaps="handled">
      <Banner tone="bad">{error}</Banner>
      <Banner tone="ok">{notice}</Banner>

      <Card>
        <H2>Signed in</H2>
        <Line label="Employee" value={`${session?.user.name ?? ''} (${session?.user.username ?? ''})`} />
        <Line label="Role" value={session?.user.role ?? '—'} />
        <Line label="Store" value={`${session?.store.number} · ${session?.store.name}`} />
        <Button title="Sign out" variant="secondary" style={{ marginTop: 12 }} onPress={() => void app.logout()} />
      </Card>

      <Card style={{ marginTop: 12 }}>
        <H2>Card payments (bbpos)</H2>
        {!cardViaBbpos ? (
          <Banner tone="warn">
            This store's card processor is not set to bbpos. In the franchise portal open Stores → this store → Payments, choose “bbpos”, set the bbpos API URL and the bbpos service account.
          </Banner>
        ) : null}
        <Field label="bbpos API URL" value={bbposUrl} onChangeText={setBbposUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" hint="Where this tablet reaches bbpos, e.g. https://pos-api.example.com or http://192.168.1.10:4000" />
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
        {bbposUser ? (
          <>
            <Line label="bbpos user" value={`${bbposUser.name} (${bbposUser.role})`} />
            <Line label="bbpos business" value={bbposUser.businessName} />
            {bbposConfig ? (
              <>
                <Line label="Stripe mode" value={bbposConfig.stripe.mode.toUpperCase()} color={bbposConfig.stripe.mode === 'live' ? colors.ok : colors.warn} />
                <Line label="Terminal location" value={bbposConfig.stripe.terminalLocationName ?? 'not configured'} />
                <Line label="Currency" value={bbposConfig.business.currency} />
                {!bbposConfig.pricing.allowCustomAmounts ? (
                  <Banner tone="bad">bbpos has “custom amounts” disabled. Enable it in bbpos admin → Settings → Pricing; the POS charges each order as one custom amount.</Banner>
                ) : null}
                {session && bbposConfig.business.currency !== session.store.currency ? (
                  <Banner tone="bad">{`Currency mismatch: store uses ${session.store.currency}, bbpos uses ${bbposConfig.business.currency}.`}</Banner>
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
            <Muted style={{ marginVertical: 8 }}>Sign in with a bbpos account that has the Cashier role (or higher) for this restaurant.</Muted>
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

      <Card style={{ marginTop: 12 }}>
        <H2>This device</H2>
        <Line label="Registered as" value={device ? `${device.name}${device.pending ? ' (awaiting approval)' : ''}` : 'Not registered'} />
        <Line label="Server status" value={deviceState.status ?? '—'} />
        <Line label="Version status" value={deviceState.versionStatus ?? '—'} />
        <Line label="App version" value={APP_VERSION} />
        {deviceState.revoked ? <Banner tone="bad">{deviceState.revoked}</Banner> : null}
        {device?.pending ? (
          <Button
            title="Check approval"
            variant="secondary"
            style={{ marginTop: 12 }}
            busy={busy === 'approval'}
            onPress={() =>
              run('approval', async () => {
                const me = await pizza.deviceMe(device.token);
                if (me.status === 'active') {
                  await app.setDevice({ ...device, pending: false });
                  setNotice('Device approved.');
                } else setNotice(`Device status: ${me.status}. Ask a manager or HQ to approve it in the portal (Devices).`);
              })
            }
          />
        ) : null}
      </Card>

      <Card style={{ marginTop: 12 }}>
        <H2>Pizza platform server</H2>
        <Field label="Server URL" value={pizzaUrl} onChangeText={setPizzaUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
        <Row>
          <Button
            title="Test connection"
            variant="secondary"
            busy={busy === 'test'}
            onPress={() =>
              run('test', async () => {
                const h = await pizza.health();
                if (h.ok) setNotice(`${pizza.baseUrl}: ${h.detail}`);
                else setError(`${pizza.baseUrl}: ${h.status ? `HTTP ${h.status}` : ''} ${h.detail}${h.status === 502 ? ' — the API container is down; see deploy/TROUBLESHOOTING.md' : ''}`);
              })
            }
          />
          <Button
            title="Save (signs out)"
            variant="secondary"
            busy={busy === 'url'}
            onPress={() =>
              run('url', async () => {
                await pizza.setBaseUrl(pizzaUrl);
                await app.logout();
              })
            }
          />
        </Row>
      </Card>
    </ScrollView>
  );
}
