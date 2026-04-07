/**
 * API Configuration
 * Backend server холболтын тохиргоо
 */

import { Platform } from "react-native";
import Constants from "expo-constants";

// ─── Environment switch ───────────────────────────────────────────────────────
// __DEV__ = true  → local backend by default
// __DEV__ = false → Fly.io production backend (release build)
//
// Local backend URL can be overridden with:
// EXPO_PUBLIC_LOCAL_API_URL=http://<your-ip>:5000   (optional)
//
// To force any custom URL in any environment, set:
// EXPO_PUBLIC_API_BASE_URL=https://example.com

const PRODUCTION_URL = 'https://predictrix-api.fly.dev';

// Android emulator uses 10.0.2.2 to reach host machine; iOS uses localhost.
const DEFAULT_LOCAL_URL = Platform.OS === 'android'
  ? 'http://10.0.2.2:5000'
  : 'http://localhost:5000';

const normalizeUrl = (value?: string): string =>
  (value ?? '').trim().replace(/\/+$/, '');

const EXPLICIT_API_BASE_URL = normalizeUrl(process.env.EXPO_PUBLIC_API_BASE_URL);
const EXPLICIT_LOCAL_API_URL = normalizeUrl(process.env.EXPO_PUBLIC_LOCAL_API_URL);

const getExpoHostIp = (): string | null => {
  const candidates: Array<string | undefined> = [
    (Constants.expoConfig as any)?.hostUri,
    (Constants.expoGoConfig as any)?.debuggerHost,
    (Constants.manifest2 as any)?.extra?.expoClient?.hostUri,
    (Constants as any)?.manifest?.debuggerHost,
    (Constants as any)?.manifest?.hostUri,
  ];

  for (const candidate of candidates) {
    if (!candidate) continue;
    const host = candidate.split(',')[0].split(':')[0].trim();
    if (host && host !== 'localhost' && host !== '127.0.0.1') {
      return host;
    }
  }

  return null;
};

const getLocalApiUrl = (): string => {
  if (EXPLICIT_LOCAL_API_URL) {
    return EXPLICIT_LOCAL_API_URL;
  }

  const hostIp = getExpoHostIp();
  if (hostIp) {
    return `http://${hostIp}:5000`;
  }

  return DEFAULT_LOCAL_URL;
};

const getApiUrl = () => {
  if (EXPLICIT_API_BASE_URL) {
    return EXPLICIT_API_BASE_URL;
  }

  if (__DEV__) {
    return getLocalApiUrl();
  }

  return PRODUCTION_URL;
};

export const API_BASE_URL = getApiUrl();
export const API_URL = API_BASE_URL; // Export API_URL directly as well for convenience

export const API_ENDPOINTS = {
  // Authentication
  LOGIN: `${API_BASE_URL}/auth/login`,
  REGISTER: `${API_BASE_URL}/auth/register`,
  VERIFY: `${API_BASE_URL}/auth/verify`,
  ME: `${API_BASE_URL}/auth/me`,
  UPDATE: `${API_BASE_URL}/auth/update`,
  CHANGE_PASSWORD: `${API_BASE_URL}/auth/change-password`,

  // Notifications
  NOTIFICATION_REGISTER: `${API_BASE_URL}/notifications/register`,
  NOTIFICATION_UNREGISTER: `${API_BASE_URL}/notifications/unregister`,
  NOTIFICATION_PREFERENCES: `${API_BASE_URL}/notifications/preferences`,
  NOTIFICATION_TEST: `${API_BASE_URL}/notifications/test`,

  // Health check
  HEALTH: `${API_BASE_URL}/health`,
};

export default {
  API_BASE_URL,
  API_ENDPOINTS,
};
