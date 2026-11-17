import type { ExpoConfig } from 'expo/config';

/**
 * Restaurant POS for phones and tablets (Android + iOS).
 *
 * Orders, menu, promotions, cash and devices come from the Pizza platform API.
 * Card payments go through the bbpos service (Stripe Terminal + BBPOS WisePad 3).
 *
 * The Stripe Terminal SDK is a native module, so card readers need a DEVELOPMENT BUILD
 * (`expo run:*` or EAS Build). Expo Go runs the app with the mock reader only.
 */
const name = process.env.APP_DISPLAY_NAME ?? 'Pizza POS';
const bundleId = process.env.APP_BUNDLE_ID ?? 'com.example.pizzapos';

const config: ExpoConfig = {
  name,
  slug: 'pizza-pos',
  scheme: 'pizzapos',
  version: '1.0.0',
  orientation: 'default',
  userInterfaceStyle: 'light',
  ios: {
    bundleIdentifier: bundleId,
    supportsTablet: true,
    infoPlist: {
      // HTTPS everywhere in production; local-network HTTP is allowed for development servers.
      NSAppTransportSecurity: { NSAllowsArbitraryLoads: false, NSAllowsLocalNetworking: true },
    },
  },
  android: {
    package: bundleId,
  },
  plugins: [
    'expo-dev-client',
    'expo-secure-store',
    [
      '@stripe/stripe-terminal-react-native',
      {
        bluetoothBackgroundMode: true,
        locationWhenInUsePermission: 'Location access is required by Stripe to accept card payments with your card reader.',
        bluetoothPeripheralPermission: 'Bluetooth is used to connect to your BBPOS WisePad 3 card reader.',
        bluetoothAlwaysUsagePermission: 'Bluetooth is used to connect to your BBPOS WisePad 3 card reader.',
      },
    ],
    [
      'expo-build-properties',
      {
        // Stripe Terminal RN SDK beta.34 needs iOS 16+, Android compileSdk 35+ and Kotlin 2.2+.
        ios: { deploymentTarget: '16.4' },
        android: {
          compileSdkVersion: 36,
          targetSdkVersion: 36,
          minSdkVersion: 26,
          kotlinVersion: '2.2.0',
          // Only for development builds that talk to http://<LAN IP>. Production must use HTTPS.
          usesCleartextTraffic: process.env.ALLOW_CLEARTEXT === 'true',
        },
      },
    ],
  ],
  ...(process.env.EAS_OWNER ? { owner: process.env.EAS_OWNER } : {}),
  extra: {
    // Set by `eas init` (copy the printed id into .env as EAS_PROJECT_ID).
    ...(process.env.EAS_PROJECT_ID ? { eas: { projectId: process.env.EAS_PROJECT_ID } } : {}),
    pizzaApiUrl: process.env.EXPO_PUBLIC_PIZZA_API_URL ?? 'https://pos.amrt.ca',
    bbposApiUrl: process.env.EXPO_PUBLIC_BBPOS_API_URL ?? '',
  },
};

export default config;
