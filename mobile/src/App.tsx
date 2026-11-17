import React, { useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { colors } from './components/ui';
import { CashScreen } from './screens/CashScreen';
import { LoginScreen } from './screens/LoginScreen';
import { OrderScreen } from './screens/OrderScreen';
import { OrdersScreen } from './screens/OrdersScreen';
import { ReaderScreen } from './screens/ReaderScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { AppProvider, featureOn, useApp } from './state/AppState';
import { TerminalProvider } from './terminal/TerminalProvider';
import { useTerminal } from './terminal/context';

type Tab = 'new' | 'orders' | 'cash' | 'reader' | 'settings';

export default function App() {
  return (
    <SafeAreaProvider>
      <AppProvider>
        <Root />
      </AppProvider>
    </SafeAreaProvider>
  );
}

function Root() {
  const { ready, session, bbposConfig } = useApp();
  if (!ready) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.dark }}>
        <ActivityIndicator color="#fff" size="large" />
      </View>
    );
  }
  if (!session) {
    return (
      <>
        <StatusBar style="light" />
        <LoginScreen />
      </>
    );
  }
  return (
    <TerminalProvider config={bbposConfig}>
      <Shell />
    </TerminalProvider>
  );
}

function Shell() {
  const { session, pending, deviceState, cardViaBbpos } = useApp();
  const terminal = useTerminal();
  const [tab, setTab] = useState<Tab>('new');
  const tabs: [Tab, string][] = [
    ['new', 'New order'],
    ['orders', pending.length ? `Orders (${pending.length}!)` : 'Orders'],
    ...(featureOn(deviceState, 'cash_drawer') ? ([['cash', 'Cash']] as [Tab, string][]) : []),
    ...(cardViaBbpos ? ([['reader', terminal.connectedReader ? 'Reader ●' : 'Reader ○']] as [Tab, string][]) : []),
    ['settings', 'Settings'],
  ];

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.dark }} edges={['top', 'left', 'right']}>
      <StatusBar style="light" />
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 10 }}>
        <Text style={{ color: '#fff', fontSize: 18, fontWeight: '800', flex: 1 }} numberOfLines={1}>
          {session!.store.number} · {session!.store.name}
        </Text>
        <Text style={{ color: '#D1D5DB' }}>{session!.user.name}</Text>
      </View>
      {deviceState.revoked ? (
        <Text style={{ backgroundColor: colors.bad, color: '#fff', padding: 8, textAlign: 'center' }}>{deviceState.revoked}</Text>
      ) : null}
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        {tab === 'new' && <OrderScreen />}
        {tab === 'orders' && <OrdersScreen />}
        {tab === 'cash' && <CashScreen />}
        {tab === 'reader' && <ReaderScreen />}
        {tab === 'settings' && <SettingsScreen />}
      </View>
      <SafeAreaView edges={['bottom']} style={{ backgroundColor: colors.card, borderTopWidth: 1, borderColor: colors.line }}>
        <View style={{ flexDirection: 'row' }}>
          {tabs.map(([k, label]) => (
            <Pressable key={k} onPress={() => setTab(k)} style={{ flex: 1, alignItems: 'center', paddingVertical: 14 }}>
              <Text style={{ fontWeight: tab === k ? '800' : '500', color: tab === k ? colors.brand : colors.ink2 }}>{label}</Text>
            </Pressable>
          ))}
        </View>
      </SafeAreaView>
    </SafeAreaView>
  );
}
