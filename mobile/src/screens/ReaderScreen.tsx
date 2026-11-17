/** Card reader: choose hardware/simulated/mock, scan for the WisePad 3 over Bluetooth, connect, update. */
import React from 'react';
import { Text, View } from 'react-native';
import { Banner, Button, Card, Chip, H2, Line, Muted, Row, colors } from '../components/ui';
import { useApp } from '../state/AppState';
import { openSystemSettings } from '../terminal/StripeTerminalBridge';
import { useReaderMode } from '../terminal/TerminalProvider';
import { useTerminal } from '../terminal/context';

const MODE_LABEL = { hardware: 'WisePad 3 (Bluetooth)', simulated: 'Stripe simulated reader', mock: 'Mock (no Stripe, no money)' } as const;

export function ReaderScreen() {
  const t = useTerminal();
  const { mode, setMode, available, nativeAvailable } = useReaderMode();
  const { bbposUser, bbposConfig, bbposError } = useApp();
  const err = t.initError ?? t.discoveryError ?? t.connectError;
  const scanning = t.connectionStatus === 'discovering';

  return (
    <View>
      {!bbposUser ? <Banner tone="warn">Sign in to bbpos (Settings tab above) before connecting a reader.</Banner> : null}
      {bbposError ? <Banner tone="bad">{`bbpos: ${bbposError}`}</Banner> : null}
      {!nativeAvailable ? (
        <Banner tone="warn">
          This build does not include the Stripe Terminal native module (e.g. Expo Go), so only the mock reader is available. Install a development build to use a WisePad 3.
        </Banner>
      ) : null}
      {bbposConfig ? (
        <Card style={{ marginBottom: 12 }}>
          <Line label="bbpos business" value={bbposConfig.business.name} />
          <Line label="Stripe mode" value={bbposConfig.stripe.mode.toUpperCase()} color={bbposConfig.stripe.mode === 'live' ? colors.ok : colors.warn} />
          <Line label="Terminal location" value={bbposConfig.stripe.terminalLocationName ?? 'NOT CONFIGURED'} />
        </Card>
      ) : null}

      <Card>
        <H2>Reader mode</H2>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
          {(['hardware', 'simulated', 'mock'] as const).map((m) => (
            <Chip key={m} label={MODE_LABEL[m]} selected={mode === m} onPress={available.includes(m) ? () => setMode(m) : undefined} tone={available.includes(m) ? undefined : 'bad'} />
          ))}
        </View>
        <Muted>Unavailable modes are crossed out: simulated needs bbpos in Stripe test mode; mock also needs “allow mock payments” in bbpos admin.</Muted>
      </Card>

      <Card style={{ marginTop: 12 }}>
        <H2>Connection</H2>
        {err ? (
          <Banner tone="bad">
            <Text style={{ color: colors.bad, fontWeight: '700' }}>{err.message}</Text>
            {err.hint ? <Text style={{ color: colors.bad }}>{err.hint}</Text> : null}
            {err.openSettings ? <Button title="Open Settings" variant="secondary" size="sm" style={{ marginTop: 6 }} onPress={() => void openSystemSettings()} /> : null}
          </Banner>
        ) : null}
        {t.lastDisconnect && !t.connectedReader ? <Banner tone="warn">{t.lastDisconnect.reason}</Banner> : null}
        {t.connectedReader ? (
          <>
            <Line label="Connected" value={t.connectedReader.label ?? t.connectedReader.serialNumber ?? 'Reader'} bold color={colors.ok} />
            <Line label="Serial" value={t.connectedReader.serialNumber ?? '—'} />
            <Line
              label="Battery"
              value={t.connectedReader.batteryLevel !== null ? `${Math.round(t.connectedReader.batteryLevel * 100)}%${t.connectedReader.isCharging ? ' (charging)' : ''}` : '—'}
            />
            <Line label="Software" value={t.connectedReader.softwareVersion ?? '—'} />
            {t.lowBattery ? <Banner tone="warn">Reader battery low — charge it via USB-C.</Banner> : null}
            {t.connectedReader.availableUpdate ? (
              <Banner tone="warn">
                <Text>Reader update {t.connectedReader.availableUpdate.version} available (~{t.connectedReader.availableUpdate.estimate}).</Text>
                <Button title={t.update.installing ? `Installing ${Math.round((t.update.progress ?? 0) * 100)}%` : 'Install update'} size="sm" busy={t.update.installing} onPress={() => void t.installUpdate()} style={{ marginTop: 6 }} />
              </Banner>
            ) : null}
            <Button title="Disconnect" variant="secondary" style={{ marginTop: 12 }} onPress={() => void t.disconnect()} />
          </>
        ) : (
          <>
            <Row>
              <Button title={scanning ? 'Scanning…' : 'Scan for readers'} busy={scanning} disabled={!bbposUser && t.backend !== 'mock'} onPress={() => void t.discover()} style={{ flex: 1 }} />
              {scanning ? <Button title="Stop" variant="secondary" onPress={() => void t.cancelDiscovery()} /> : null}
              {t.lastConnectedSerial ? <Button title="Reconnect last" variant="secondary" onPress={() => void t.reconnectLast()} /> : null}
            </Row>
            <Muted style={{ marginTop: 8 }}>Turn the WisePad 3 on (hold the power button), keep it close, and make sure it is not connected to another phone.</Muted>
            {t.discoveredReaders.map((r) => (
              <Row key={r.key} style={{ justifyContent: 'space-between', paddingVertical: 8, borderBottomWidth: 1, borderColor: colors.line }}>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontWeight: '700' }}>{r.label ?? r.serialNumber}</Text>
                  <Muted>
                    {r.serialNumber} {r.batteryLevel !== null ? `· ${Math.round(r.batteryLevel * 100)}%` : ''}
                  </Muted>
                </View>
                <Button title={t.connectionStatus === 'connecting' ? 'Connecting…' : 'Connect'} busy={t.connectionStatus === 'connecting'} onPress={() => void t.connect(r.key)} />
              </Row>
            ))}
          </>
        )}
      </Card>
    </View>
  );
}
