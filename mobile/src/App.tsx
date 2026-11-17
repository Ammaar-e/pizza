import React from 'react';
import { ActivityIndicator, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { PosWebView } from './bridge/PosWebView';
import { Segmented, Sheet, colors } from './components/ui';
import { ReaderScreen } from './screens/ReaderScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { AppProvider, useApp } from './state/AppState';
import { TerminalProvider } from './terminal/TerminalProvider';

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
  const { ready, bbposConfig } = useApp();
  if (!ready) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#0f172a' }}>
        <ActivityIndicator color="#fff" size="large" />
      </View>
    );
  }
  return (
    <TerminalProvider config={bbposConfig}>
      <SafeAreaView style={{ flex: 1, backgroundColor: '#0f172a' }}>
        <StatusBar style="light" />
        <PosWebView />
        <NativePanel />
      </SafeAreaView>
    </TerminalProvider>
  );
}

/** Card reader and settings, opened from the "Card reader" button in the POS header. */
function NativePanel() {
  const { panel, setPanel } = useApp();
  return (
    <Sheet visible={panel !== null} title="Card reader & payments" onClose={() => setPanel(null)}>
      <View style={{ maxWidth: 820, width: '100%', alignSelf: 'center' }}>
        <Segmented<'reader' | 'settings'>
          value={panel ?? 'reader'}
          onChange={setPanel}
          options={[
            ['reader', 'WisePad 3 reader'],
            ['settings', 'bbpos & server'],
          ]}
        />
        <View style={{ height: 16, backgroundColor: colors.bg }} />
        {panel === 'settings' ? <SettingsScreen /> : <ReaderScreen />}
      </View>
    </Sheet>
  );
}
