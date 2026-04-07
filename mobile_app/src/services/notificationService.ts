/**
 * Push Notification Service
 * Expo Push Notifications ашиглан мэдэгдэл хүлээн авах, бүртгэх
 *
 * 3 төрлийн мэдэгдэл:
 *   1. Trading Signal — шинэ сигнал үүсэхэд
 *   2. News Alert — эдийн засгийн мэдээний өмнө (impact шүүлтүүртэй)
 *   3. Security Alert — өөр төхөөрөмжөөс нэвтрэхэд
 */

import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import Constants from "expo-constants";
import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import axios from "axios";
import { API_BASE_URL } from "../config/api";

const PUSH_TOKEN_KEY = "@push_token";
const PUSH_TOKEN_LAST_SYNC_KEY = "@push_token_last_sync";
const PUSH_HEALTHCHECK_TOKEN_KEY = "@push_healthcheck_token";
const PUSH_TOKEN_SYNC_INTERVAL_MS = 30 * 60 * 1000; // 30 minutes
const AUTH_TOKEN_KEY = "userToken";
let pushInitInFlight: Promise<string | null> | null = null;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function sendPushHealthcheckPing(): Promise<boolean> {
  try {
    const userToken = await AsyncStorage.getItem(AUTH_TOKEN_KEY);
    if (!userToken) return false;

    const response = await axios.post(
      `${API_BASE_URL}/notifications/test`,
      {},
      {
        headers: {
          Authorization: `Bearer ${userToken}`,
          "Content-Type": "application/json",
        },
        timeout: 10000,
      }
    );

    return Boolean(response.data?.success);
  } catch {
    return false;
  }
}

// ==================== NOTIFICATION CONFIGURATION ====================

// Configure how notifications are displayed when app is in foreground
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

// ==================== DEVICE ID ====================

/**
 * Unique device identifier авах (security alert-д хэрэглэнэ)
 */
export async function getDeviceId(): Promise<string> {
  try {
    // Try stored device ID first
    const stored = await AsyncStorage.getItem("@device_id");
    if (stored) return stored;

    // Generate a stable ID from device info
    const deviceId = `${Platform.OS}_${Device.modelName ?? "unknown"}_${Date.now()}`;
    await AsyncStorage.setItem("@device_id", deviceId);
    return deviceId;
  } catch {
    return `${Platform.OS}_unknown_${Date.now()}`;
  }
}

// ==================== PERMISSION REQUEST ====================

/**
 * Notification зөвшөөрөл асуух (App эхлэхэд дуудна — login шаардахгүй)
 */
export async function requestNotificationPermission(): Promise<boolean> {
  try {
    if (!Device.isDevice) {
      return false;
    }

    const { status: existingStatus } =
      await Notifications.getPermissionsAsync();
    if (existingStatus === "granted") return true;

    const { status } = await Notifications.requestPermissionsAsync();
    return status === "granted";
  } catch (error) {
    console.error("[ERROR] Request notification permission failed:", error);
    return false;
  }
}

// ==================== PUSH TOKEN REGISTRATION ====================

/**
 * Expo Push Token авах
 * @returns {string | null} ExponentPushToken[xxx] format
 */
export async function getExpoPushToken(): Promise<string | null> {
  try {
    // Physical device шаардлагатай (simulator дээр ажиллахгүй)
    if (!Device.isDevice) {
      return null;
    }

    // Permission шалгах / авах
    const { status: existingStatus } =
      await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;

    if (existingStatus !== "granted") {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }

    if (finalStatus !== "granted") {
      return null;
    }

    // EAS project ID may not always be available in all runtime modes.
    const projectId =
      Constants.expoConfig?.extra?.eas?.projectId ??
      Constants.easConfig?.projectId;

    const tokenData = projectId
      ? await Notifications.getExpoPushTokenAsync({ projectId })
      : await Notifications.getExpoPushTokenAsync();

    const token = tokenData.data;

    // Android notification channels
    if (Platform.OS === "android") {
      await Notifications.setNotificationChannelAsync("default", {
        name: "Default",
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 250, 250, 250],
        lightColor: "#00C853",
        sound: "default",
      });

      await Notifications.setNotificationChannelAsync("signals", {
        name: "Trading Signals",
        description: "High-confidence trading signal alerts",
        importance: Notifications.AndroidImportance.HIGH,
        vibrationPattern: [0, 250, 250, 250],
        lightColor: "#FFD700",
        sound: "default",
      });

      await Notifications.setNotificationChannelAsync("news", {
        name: "Market News",
        description: "Economic news alerts before events",
        importance: Notifications.AndroidImportance.HIGH,
        vibrationPattern: [0, 250, 250, 250],
        lightColor: "#FF5252",
        sound: "default",
      });

      await Notifications.setNotificationChannelAsync("security", {
        name: "Security Alerts",
        description: "Login and account security notifications",
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 500, 200, 500],
        lightColor: "#FF0000",
        sound: "default",
      });
    }

    return token;
  } catch (error) {
    console.error("[ERROR] Get push token failed:", error);
    return null;
  }
}

/**
 * Push token-ийг backend-руу илгээж бүртгүүлэх
 */
export async function registerPushTokenWithServer(
  pushToken: string
): Promise<boolean> {
  try {
    const userToken = await AsyncStorage.getItem(AUTH_TOKEN_KEY);
    if (!userToken) {
      return false;
    }

    const deviceId = await getDeviceId();

    const response = await axios.post(
      `${API_BASE_URL}/notifications/register`,
      {
        push_token: pushToken,
        platform: Platform.OS,
        device_id: deviceId,
      },
      {
        headers: {
          Authorization: `Bearer ${userToken}`,
          "Content-Type": "application/json",
        },
        timeout: 10000,
      }
    );

    if (response.data?.success) {
      await AsyncStorage.setItem("@push_token", pushToken);
      console.log("[OK] Push token registered with server");
      return true;
    }
    return false;
  } catch (error: any) {
    const serverMessage =
      error?.response?.data?.error || error?.response?.data?.message;
    console.error(
      "[ERROR] Register push token with server failed:",
      serverMessage || error.message
    );
    return false;
  }
}

/**
 * Push token-ийг серверээс устгах
 */
export async function unregisterPushTokenFromServer(): Promise<boolean> {
  try {
    const userToken = await AsyncStorage.getItem(AUTH_TOKEN_KEY);
    if (!userToken) return false;

    const response = await axios.post(
      `${API_BASE_URL}/notifications/unregister`,
      {},
      {
        headers: {
          Authorization: `Bearer ${userToken}`,
          "Content-Type": "application/json",
        },
        timeout: 10000,
      }
    );

    if (response.data?.success) {
      await AsyncStorage.removeItem("@push_token");
      console.log("[OK] Push token unregistered from server");
      return true;
    }
    return false;
  } catch (error: any) {
    console.error("[ERROR] Unregister push token failed:", error.message);
    return false;
  }
}

// ==================== NOTIFICATION PREFERENCES ====================

export type NewsImpactFilter = "high" | "medium" | "all";

export interface NotificationPreferences {
  notifications_enabled: boolean;
  signal_notifications: boolean;
  news_notifications: boolean;
  news_impact_filter: NewsImpactFilter;
  security_notifications: boolean;
  signal_threshold: number; // 0.9 - 1.0 (user's personal confidence threshold)
}

/**
 * Мэдэгдлийн тохиргоо серверээс авах
 */
export async function getNotificationPreferences(): Promise<NotificationPreferences> {
  const defaults: NotificationPreferences = {
    notifications_enabled: true,
    signal_notifications: true,
    news_notifications: true,
    news_impact_filter: "high",
    security_notifications: true,
    signal_threshold: 0.9,
  };

  try {
    const userToken = await AsyncStorage.getItem(AUTH_TOKEN_KEY);
    if (!userToken) {
      return defaults;
    }

    const response = await axios.get(
      `${API_BASE_URL}/notifications/preferences`,
      {
        headers: { Authorization: `Bearer ${userToken}` },
        timeout: 10000,
      }
    );

    if (response.data?.success) {
      return { ...defaults, ...response.data.preferences };
    }
  } catch (error: any) {
    if (error?.response?.status !== 401) {
      console.error("[ERROR] Get notification preferences failed:", error.message);
    }
  }

  return defaults;
}

/**
 * Мэдэгдлийн тохиргоо серверт хадгалах
 */
export async function updateNotificationPreferences(
  preferences: Partial<NotificationPreferences>
): Promise<boolean> {
  try {
    const userToken = await AsyncStorage.getItem(AUTH_TOKEN_KEY);
    if (!userToken) return false;

    const response = await axios.put(
      `${API_BASE_URL}/notifications/preferences`,
      preferences,
      {
        headers: {
          Authorization: `Bearer ${userToken}`,
          "Content-Type": "application/json",
        },
        timeout: 10000,
      }
    );

    return response.data?.success ?? false;
  } catch (error: any) {
    if (error?.response?.status !== 401) {
      console.error(
        "[ERROR] Update notification preferences failed:",
        error.message
      );
    }
    return false;
  }
}

// ==================== NOTIFICATION LISTENERS ====================

/**
 * Мэдэгдлийн listener-уудыг тохируулах
 * @param onNotificationReceived - foreground-д мэдэгдэл ирэхэд
 * @param onNotificationResponse - хэрэглэгч мэдэгдэл дарахад
 */
export function setupNotificationListeners(
  onNotificationReceived?: (notification: Notifications.Notification) => void,
  onNotificationResponse?: (
    response: Notifications.NotificationResponse
  ) => void
): () => void {
  // Cold-start tap handling: app was closed and opened by tapping notification.
  Notifications.getLastNotificationResponseAsync()
    .then((response) => {
      if (response) {
        onNotificationResponse?.(response);
      }
    })
    .catch(() => {
      // Ignore non-critical startup read failures.
    });

  // Foreground notification listener
  const receivedSubscription =
    Notifications.addNotificationReceivedListener((notification) => {
      console.log("[NOTIFICATION] Received:", notification.request.content.title);
      onNotificationReceived?.(notification);
    });

  // User tapped notification listener
  const responseSubscription =
    Notifications.addNotificationResponseReceivedListener((response) => {
      const data = response.notification.request.content.data;
      console.log("[NOTIFICATION] Tapped:", data);
      onNotificationResponse?.(response);
    });

  // Return cleanup function
  return () => {
    receivedSubscription.remove();
    responseSubscription.remove();
  };
}

// ==================== INITIALIZATION ====================

/**
 * Push notification бүхэлд нь тохируулах (App startup дээр дуудна)
 */
export async function initializePushNotifications(): Promise<string | null> {
  if (pushInitInFlight) {
    return pushInitInFlight;
  }

  pushInitInFlight = (async () => {
    try {
      // Push setup is meaningful only for authenticated users.
      const userToken = await AsyncStorage.getItem(AUTH_TOKEN_KEY);
      if (!userToken) {
        return null;
      }

      const token = await getExpoPushToken();
      if (!token) {
        return null;
      }

      const savedToken = await AsyncStorage.getItem(PUSH_TOKEN_KEY);
      const lastSyncRaw = await AsyncStorage.getItem(PUSH_TOKEN_LAST_SYNC_KEY);
      const lastSync = lastSyncRaw ? Number(lastSyncRaw) : 0;
      const shouldResync =
        savedToken !== token ||
        !lastSync ||
        Number.isNaN(lastSync) ||
        Date.now() - lastSync > PUSH_TOKEN_SYNC_INTERVAL_MS;

      if (!shouldResync) {
        return token;
      }

      let ok = await registerPushTokenWithServer(token);
      if (!ok) {
        // One short retry helps on flaky mobile networks.
        await wait(1200);
        ok = await registerPushTokenWithServer(token);
      }

      if (ok) {
        await AsyncStorage.setItem(PUSH_TOKEN_KEY, token);
        await AsyncStorage.setItem(PUSH_TOKEN_LAST_SYNC_KEY, String(Date.now()));

        // One-time token-level healthcheck to verify delivery path end-to-end.
        const healthcheckToken = await AsyncStorage.getItem(PUSH_HEALTHCHECK_TOKEN_KEY);
        if (healthcheckToken !== token) {
          const pingOk = await sendPushHealthcheckPing();
          if (pingOk) {
            await AsyncStorage.setItem(PUSH_HEALTHCHECK_TOKEN_KEY, token);
          }
        }
      }

      return token;
    } catch (error) {
      console.error("[ERROR] Initialize push notifications failed:", error);
      return null;
    } finally {
      pushInitInFlight = null;
    }
  })();

  return pushInitInFlight;
}
