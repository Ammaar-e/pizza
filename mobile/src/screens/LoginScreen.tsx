/**
 * POS sign-in: store number + employee username + password, or PIN on a registered device.
 * Also handles device enrollment (one-time code) and registration by a manager,
 * mirroring the web POS activation flow.
 */
import React, { useEffect, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import { Banner, Button, Card, Field, H1, Muted, Row, colors } from '../components/ui';
import { describeError } from '../lib/http';
import { pizza } from '../lib/pizzaApi';
import type { PosSession } from '../lib/types';
import { APP_VERSION, useApp } from '../state/AppState';

type Mode = 'login' | 'enroll' | 'register' | 'server';

export function LoginScreen() {
  const { device, setDevice, setSession } = useApp();
  const registered = device && !device.pending ? device : null;
  const [mode, setMode] = useState<Mode>('login');
  const [storeNumber, setStoreNumber] = useState(device?.store_number ?? '');
  const [username, setUsername] = useState('');
  const [secret, setSecret] = useState('');
  const [usePin, setUsePin] = useState(Boolean(registered));
  const [code, setCode] = useState('');
  const [deviceName, setDeviceName] = useState('Front counter tablet');
  const [server, setServer] = useState(pizza.baseUrl);
  const [pendingSession, setPendingSession] = useState<PosSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setUsePin(Boolean(registered)), [registered]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  const login = () =>
    run(async () => {
      const r = await pizza.postAnon<{ tokens: { access: string; refresh: string }; user: PosSession['user']; store: PosSession['store'] }>(
        '/auth/pos/login/',
        { store_number: storeNumber.trim(), username: username.trim(), ...(usePin ? { pin: secret } : { password: secret }) },
      );
      const s: PosSession = { access: r.tokens.access, refresh: r.tokens.refresh, user: r.user, store: r.store };
      setSecret('');
      if (registered && registered.store === r.store.id) {
        await setSession(s);
      } else {
        // Not a registered device of this store: offer registration (managers) or limited mode.
        await pizza.setSession(s); // needed to call POST /devices/
        setPendingSession(s);
        setMode('register');
      }
    });

  const enroll = () =>
    run(async () => {
      const r = await pizza.postAnon<{ device_token: string; device: { id: number; name: string; store: number; store_number: string } }>(
        '/device-auth/enroll/',
        { store_number: storeNumber.trim(), enrollment_code: code.trim().toUpperCase(), software_version: APP_VERSION },
      );
      await setDevice({ token: r.device_token, id: r.device.id, name: r.device.name, store: r.device.store, store_number: r.device.store_number });
      setMode('login');
      setUsePin(true);
      setNotice(`Enrolled as “${r.device.name}”. Staff can now sign in with their PIN.`);
    });

  const register = () =>
    run(async () => {
      if (!pendingSession) return;
      const d = await pizza.post<{ id: number; name: string; status: string; device_token: string }>('/devices/', {
        store: pendingSession.store.id,
        device_type: 'pos',
        name: deviceName.trim() || 'POS tablet',
      });
      await setDevice({
        token: d.device_token,
        id: d.id,
        name: d.name,
        store: pendingSession.store.id,
        store_number: pendingSession.store.number,
        pending: d.status === 'pending',
      });
      await setSession(pendingSession);
    });

  const checkApproval = () =>
    run(async () => {
      if (!device?.pending) return;
      try {
        const me = await pizza.deviceMe(device.token);
        if (me.status === 'active') {
          await setDevice({ ...device, pending: false });
          setNotice('Device approved. Staff can now sign in with their PIN.');
        } else setNotice('Still awaiting approval by a manager or headquarters.');
      } catch (e) {
        await setDevice(null);
        throw e;
      }
    });

  const saveServer = () =>
    run(async () => {
      await pizza.setBaseUrl(server);
      setServer(pizza.baseUrl);
      const h = await pizza.health();
      if (!h.ok) throw new Error(`Saved, but the server did not respond correctly: ${h.status || ''} ${h.detail}`);
      setNotice('Server reachable.');
      setMode('login');
    });

  const canRegister = pendingSession?.user.permissions.includes('org.manage_devices');

  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1, backgroundColor: colors.dark }}>
      <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: 20 }} keyboardShouldPersistTaps="handled">
        <Card style={{ width: '100%', maxWidth: 460 }}>
          <Muted style={{ color: colors.brand, fontWeight: '700', letterSpacing: 2 }}>PIZZA NETWORK</Muted>
          <H1 style={{ marginBottom: 4 }}>
            {mode === 'enroll' ? 'Enroll this tablet' : mode === 'register' ? 'Register this tablet' : mode === 'server' ? 'Server' : 'POS sign-in'}
          </H1>
          {registered && mode === 'login' ? <Muted style={{ marginBottom: 8 }}>Registered device: {registered.name}</Muted> : null}
          <View style={{ height: 12 }} />
          <Banner tone="bad">{error}</Banner>
          <Banner tone="ok">{notice}</Banner>

          {mode === 'login' && (
            <>
              <Field label="Store number" value={storeNumber} onChangeText={setStoreNumber} keyboardType="number-pad" autoCorrect={false} />
              <Field label="Employee username" value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} />
              <Field
                label={usePin ? 'PIN' : 'Password'}
                value={secret}
                onChangeText={setSecret}
                secureTextEntry
                keyboardType={usePin ? 'number-pad' : 'default'}
                onSubmitEditing={login}
              />
              <Button title={busy ? 'Signing in…' : 'Sign in'} size="xl" busy={busy} disabled={!storeNumber || !username || !secret} onPress={login} />
              <View style={{ height: 12 }} />
              {registered ? <Button title={`Use ${usePin ? 'password' : 'PIN'} instead`} variant="ghost" size="sm" onPress={() => setUsePin(!usePin)} /> : null}
              {device?.pending ? (
                <Banner tone="warn">
                  <Muted>Device “{device.name}” is awaiting approval.</Muted>
                  <Button title="Check approval status" variant="secondary" size="sm" onPress={checkApproval} style={{ marginTop: 8 }} />
                </Banner>
              ) : null}
              <Row style={{ justifyContent: 'space-between', marginTop: 4 }}>
                {!device ? <Button title="Enroll with a code" variant="ghost" size="sm" onPress={() => setMode('enroll')} /> : <View />}
                <Button title="Server settings" variant="ghost" size="sm" onPress={() => setMode('server')} />
              </Row>
              <Muted style={{ textAlign: 'center', marginTop: 8, fontSize: 12 }}>
                {pizza.baseUrl} · v{APP_VERSION}
              </Muted>
            </>
          )}

          {mode === 'enroll' && (
            <>
              <Muted style={{ marginBottom: 12 }}>
                Enter the store number and the one-time enrollment code issued in the franchise portal (Devices → Enrollment codes).
              </Muted>
              <Field label="Store number" value={storeNumber} onChangeText={setStoreNumber} keyboardType="number-pad" />
              <Field label="Enrollment code" value={code} onChangeText={setCode} autoCapitalize="characters" placeholder="XXXXX-XXXXX" />
              <Button title="Enroll" size="xl" busy={busy} disabled={!storeNumber || !code} onPress={enroll} />
              <Button title="Back to sign in" variant="ghost" onPress={() => setMode('login')} style={{ marginTop: 8 }} />
            </>
          )}

          {mode === 'register' && pendingSession && (
            <>
              <Muted style={{ marginBottom: 12 }}>
                Store {pendingSession.store.number} · {pendingSession.store.name}
              </Muted>
              {canRegister ? (
                <>
                  <Field label="Device name" value={deviceName} onChangeText={setDeviceName} />
                  <Button title="Register this tablet" size="xl" busy={busy} onPress={register} />
                </>
              ) : (
                <Banner tone="warn">
                  This tablet is not registered to this store. A manager must sign in once to register it. You can continue in limited mode (no cash drawer).
                </Banner>
              )}
              <Button
                title="Continue without registering"
                variant="secondary"
                size="lg"
                style={{ marginTop: 8 }}
                onPress={() => run(() => setSession(pendingSession))}
              />
              <Button
                title="Cancel"
                variant="ghost"
                style={{ marginTop: 4 }}
                onPress={() =>
                  run(async () => {
                    await pizza.setSession(null);
                    setPendingSession(null);
                    setMode('login');
                  })
                }
              />
            </>
          )}

          {mode === 'server' && (
            <>
              <Field
                label="Pizza platform URL"
                value={server}
                onChangeText={setServer}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                hint="Example: https://pos.amrt.ca (production) or http://192.168.1.10:8000 (development)."
              />
              <Button title="Save & test" size="lg" busy={busy} onPress={saveServer} />
              <Button title="Back" variant="ghost" onPress={() => setMode('login')} style={{ marginTop: 8 }} />
            </>
          )}
        </Card>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
