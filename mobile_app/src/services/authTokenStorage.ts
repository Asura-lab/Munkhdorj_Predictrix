import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "userToken";
const REFRESH_TOKEN_KEY = "refreshToken";
const USER_DATA_KEY = "userData";

let secureStoreAvailable: boolean | null = null;

async function isSecureStoreAvailable(): Promise<boolean> {
  if (secureStoreAvailable !== null) {
    return secureStoreAvailable;
  }

  try {
    secureStoreAvailable = await SecureStore.isAvailableAsync();
  } catch {
    secureStoreAvailable = false;
  }

  return secureStoreAvailable;
}

export async function getAuthToken(): Promise<string | null> {
  return getStoredToken(TOKEN_KEY);
}

export async function setAuthToken(token: string): Promise<void> {
  await setStoredToken(TOKEN_KEY, token);
}

export async function clearAuthToken(): Promise<void> {
  await clearStoredToken(TOKEN_KEY);
}

export async function getRefreshToken(): Promise<string | null> {
  return getStoredToken(REFRESH_TOKEN_KEY);
}

export async function setRefreshToken(token: string): Promise<void> {
  await setStoredToken(REFRESH_TOKEN_KEY, token);
}

export async function clearRefreshToken(): Promise<void> {
  await clearStoredToken(REFRESH_TOKEN_KEY);
}

export async function getStoredUserData(): Promise<string | null> {
  return getStoredValue(USER_DATA_KEY, true);
}

export async function setStoredUserData(value: string): Promise<void> {
  await setStoredValue(USER_DATA_KEY, value);
}

export async function clearStoredUserData(): Promise<void> {
  await clearStoredValue(USER_DATA_KEY, true);
}

async function getStoredToken(storageKey: string): Promise<string | null> {
  const secureAvailable = await isSecureStoreAvailable();

  if (secureAvailable) {
    try {
      const token = await SecureStore.getItemAsync(storageKey);
      if (token) {
        return token;
      }
    } catch {
      // Fall back to legacy storage below.
    }
  }

  return AsyncStorage.getItem(storageKey);
}

async function setStoredToken(storageKey: string, token: string): Promise<void> {
  const secureAvailable = await isSecureStoreAvailable();

  if (secureAvailable) {
    await SecureStore.setItemAsync(storageKey, token);
    await AsyncStorage.removeItem(storageKey);
    return;
  }

  await AsyncStorage.setItem(storageKey, token);
}

async function clearStoredToken(storageKey: string): Promise<void> {
  const secureAvailable = await isSecureStoreAvailable();

  if (secureAvailable) {
    try {
      await SecureStore.deleteItemAsync(storageKey);
    } catch {
      // Continue clearing legacy token.
    }
  }

  await AsyncStorage.removeItem(storageKey);
}

async function getStoredValue(storageKey: string, migrateFromLegacy = false): Promise<string | null> {
  const secureAvailable = await isSecureStoreAvailable();

  if (secureAvailable) {
    try {
      const value = await SecureStore.getItemAsync(storageKey);
      if (value) {
        return value;
      }
    } catch {
      // Fall back to legacy storage below.
    }
  }

  const legacyValue = await AsyncStorage.getItem(storageKey);
  if (!legacyValue) {
    return null;
  }

  if (secureAvailable && migrateFromLegacy) {
    try {
      await SecureStore.setItemAsync(storageKey, legacyValue);
      await AsyncStorage.removeItem(storageKey);
    } catch {
      // Keep legacy value if migration fails.
    }
  }

  return legacyValue;
}

async function setStoredValue(storageKey: string, value: string): Promise<void> {
  const secureAvailable = await isSecureStoreAvailable();

  if (!secureAvailable) {
    await AsyncStorage.setItem(storageKey, value);
    return;
  }

  await SecureStore.setItemAsync(storageKey, value);
  await AsyncStorage.removeItem(storageKey);
}

async function clearStoredValue(storageKey: string, removeLegacy: boolean = false): Promise<void> {
  const secureAvailable = await isSecureStoreAvailable();

  if (secureAvailable) {
    try {
      await SecureStore.deleteItemAsync(storageKey);
    } catch {
      // Continue clearing legacy token.
    }
  }

  if (removeLegacy) {
    await AsyncStorage.removeItem(storageKey);
  }
}
