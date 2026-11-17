/**
 * Key/value storage. On iOS/Android values live in the Keychain/Keystore (expo-secure-store),
 * which is where tokens and device credentials belong. On web (developer preview only) values are
 * kept in memory and never persisted.
 */
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const memory = new Map<string, string>();
const isWeb = Platform.OS === 'web';

export const storage = {
  async get(key: string): Promise<string | null> {
    return isWeb ? (memory.get(key) ?? null) : SecureStore.getItemAsync(key);
  },
  async set(key: string, value: string): Promise<void> {
    if (isWeb) memory.set(key, value);
    else await SecureStore.setItemAsync(key, value, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
  },
  async remove(key: string): Promise<void> {
    if (isWeb) memory.delete(key);
    else await SecureStore.deleteItemAsync(key);
  },
  async getJson<T>(key: string): Promise<T | null> {
    const raw = await storage.get(key);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  },
  async setJson(key: string, value: unknown): Promise<void> {
    await storage.set(key, JSON.stringify(value));
  },
};
