/**
 * API Configuration
 * Backend server холболтын тохиргоо
 */

import { Platform } from "react-native";

// ─── Environment switch ───────────────────────────────────────────────────────
// __DEV__ = true  → local backend (npm start / expo go)
// __DEV__ = false → production URL (default: Fly, overridable for Azure release)

const DEFAULT_PRODUCTION_URL = 'https://predictrix-api.fly.dev';
const ENV_PRODUCTION_URL = (globalThis as any)?.process?.env?.EXPO_PUBLIC_API_BASE_URL?.trim();
const PRODUCTION_URL = ENV_PRODUCTION_URL || DEFAULT_PRODUCTION_URL;

// Physical device: set EXPO_PUBLIC_LOCAL_API_HOST to your PC's LAN IP (e.g. 192.168.1.x)
// Android emulator: 10.0.2.2 reaches the host machine automatically
// iOS simulator / web: localhost works fine
const LOCAL_HOST = (globalThis as any)?.process?.env?.EXPO_PUBLIC_LOCAL_API_HOST?.trim();
const LOCAL_URL = LOCAL_HOST
  ? `http://${LOCAL_HOST}:5000`
  : Platform.OS === 'android'
    ? 'http://10.0.2.2:5000'
    : 'http://localhost:5000';

const getApiUrl = () => {
  if (__DEV__) {
    return LOCAL_URL;
  }
  return PRODUCTION_URL;
};

export const API_URL = getApiUrl(); // Export API_URL directly as well for convenience
export const API_BASE_URL = getApiUrl();

export const API_ENDPOINTS = {
  // Authentication
  LOGIN: `${API_BASE_URL}/auth/login`,
  REGISTER: `${API_BASE_URL}/auth/register`,
  VERIFY_EMAIL: `${API_BASE_URL}/auth/verify-email`,
  REFRESH: `${API_BASE_URL}/auth/refresh`,
  LOGOUT: `${API_BASE_URL}/auth/logout`,
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
