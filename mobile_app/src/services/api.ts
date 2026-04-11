import axios, { AxiosInstance, InternalAxiosRequestConfig } from "axios";
import { Platform } from "react-native";
import { API_BASE_URL } from "../config/api";
import { getDeviceId } from "./notificationService";
import {
  clearAuthToken,
  clearRefreshToken,
  clearStoredUserData,
  getAuthToken,
  getRefreshToken,
  setAuthToken,
  setRefreshToken,
  setStoredUserData,
} from "./authTokenStorage";

export interface UserData {
  id: string;
  name: string;
  email: string;
  is_verified?: boolean;
}

export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  error?: string;
  token?: string;
  refresh_token?: string;
  user?: UserData;
  requiresVerification?: boolean;
}

const AUTH_REFRESH_PATH = "/auth/refresh";
const NON_REFRESHABLE_PATHS = [
  "/auth/login",
  "/auth/register",
  "/auth/verify-email",
  "/auth/resend-verification",
  "/auth/forgot-password",
  "/auth/verify-reset-code",
  "/auth/reset-password",
  AUTH_REFRESH_PATH,
];

let refreshInFlight: Promise<string | null> | null = null;

const clearSessionState = async () => {
  await clearAuthToken();
  await clearRefreshToken();
  await clearStoredUserData();
};

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const shouldAttemptTokenRefresh = (config: any) => {
  const requestUrl = String(config?.url || "");
  if (config?.__isRetryRequest) {
    return false;
  }

  return !NON_REFRESHABLE_PATHS.some((path) => requestUrl.includes(path));
};

const refreshAccessToken = async (): Promise<string | null> => {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    const refreshToken = await getRefreshToken();
    if (!refreshToken) {
      await clearSessionState();
      return null;
    }

    try {
      const response = await axios.post(
        `${API_BASE_URL}${AUTH_REFRESH_PATH}`,
        { refresh_token: refreshToken },
        {
          timeout: 10000,
          headers: {
            "Content-Type": "application/json",
          },
        }
      );

      const nextAccessToken = response.data?.token;
      const nextRefreshToken = response.data?.refresh_token;

      if (!nextAccessToken || !nextRefreshToken) {
        await clearSessionState();
        return null;
      }

      await setAuthToken(nextAccessToken);
      await setRefreshToken(nextRefreshToken);
      return nextAccessToken;
    } catch {
      await clearSessionState();
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
};

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000, // 30 секунд — Azure App Service (24/7, cold start байхгүй)
  headers: {
    "Content-Type": "application/json",
  },
});

// Request interceptor - Token автоматаар нэмэх
apiClient.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    const token = await getAuthToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: any) => {
    return Promise.reject(error);
  }
);

// Response interceptor - 401 token expiry болон retry зохицуулах
apiClient.interceptors.response.use(
  (response) => response,
  async (error: any) => {
    if (error?.response?.status === 401) {
      const config = error?.config || {};

      if (shouldAttemptTokenRefresh(config)) {
        config.__isRetryRequest = true;
        const nextAccessToken = await refreshAccessToken();
        if (nextAccessToken) {
          config.headers = config.headers || {};
          config.headers.Authorization = `Bearer ${nextAccessToken}`;
          return apiClient(config);
        }
      }

      // Access + refresh tokens are both invalid/expired.
      await clearSessionState();
      console.warn("[WARN] apiClient: 401 received, cleared session tokens.");
    }

    // Retry up to 3 times on network/timeout errors
    // Delays: 5s → 15s → 30s  (total 50s covered before giving up)
    const RETRY_DELAYS = [5000, 15000, 30000];
    const config = error?.config;
    if (
      config &&
      (config.__retryCount ?? 0) < RETRY_DELAYS.length &&
      (error.code === "ECONNABORTED" || error.code === "ERR_NETWORK" || !error.response)
    ) {
      const attempt: number = config.__retryCount ?? 0;
      config.__retryCount = attempt + 1;
      const delay = RETRY_DELAYS[attempt];
      console.warn(
        `[WARN] apiClient: Network error (${error.code}), retry ${config.__retryCount}/${RETRY_DELAYS.length} in ${delay / 1000}s...`
      );
      await new Promise((resolve) => setTimeout(resolve, delay));
      return apiClient(config);
    }

    return Promise.reject(error);
  }
);

// ==================== AUTH ENDPOINTS ====================

/**
 * Бүртгүүлэх (Имэйл баталгаажуулалттай)
 */
export interface RegisterConsentPayload {
  accepted: boolean;
  terms_version: string;
  privacy_version: string;
  locale: "mn" | "en";
  accepted_at: string;
}

export const registerUser = async (
  name: string,
  email: string,
  password: string,
  consent: RegisterConsentPayload,
): Promise<ApiResponse> => {
  try {
    const response = await apiClient.post("/auth/register", {
      name,
      email,
      password,
      consent,
    });
    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Имэйл баталгаажуулах
 */
export const verifyEmail = async (email: string, code: string): Promise<ApiResponse> => {
  try {
    const response = await apiClient.post("/auth/verify-email", {
      email,
      code,
    });

    // Token-ийг хадгалах
    if (response.data.token) {
      await setAuthToken(response.data.token);
      if (response.data.refresh_token) {
        await setRefreshToken(response.data.refresh_token);
      }
      await setStoredUserData(JSON.stringify(response.data.user));
    }

    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Баталгаажуулалтын код дахин илгээх
 */
export const resendVerificationCode = async (email: string): Promise<ApiResponse> => {
  try {
    const response = await apiClient.post("/auth/resend-verification", {
      email,
    });
    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Нэвтрэх
 */
export const loginUser = async (email: string, password: string): Promise<ApiResponse> => {
  try {
    const deviceId = await getDeviceId();
    const response = await apiClient.post("/auth/login", {
      email,
      password,
      device_id: deviceId,
      platform: Platform.OS,
    });

    // Token хадгалах
    if (response.data.token) {
      await setAuthToken(response.data.token);
      if (response.data.refresh_token) {
        await setRefreshToken(response.data.refresh_token);
      }
      await setStoredUserData(JSON.stringify(response.data.user));
    }

    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
      requiresVerification:
        error.response?.data?.requires_verification || false,
    };
  }
};

/**
 * Нууц үг мартсан
 */
export const forgotPassword = async (email: string) => {
  try {
    const response = await apiClient.post("/auth/forgot-password", { email });
    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Сэргээх код шалгах
 */
export const verifyResetCode = async (email: string, code: string) => {
  try {
    const response = await apiClient.post("/auth/verify-reset-code", {
      email,
      code,
    });
    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Нууц үг сэргээх
 */
export const resetPassword = async (email: string, code: string, newPassword: string) => {
  try {
    const response = await apiClient.post("/auth/reset-password", {
      email,
      code,
      new_password: newPassword,
    });
    return { success: true, data: response.data };
  } catch (error: any) {
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Гарах
 */
export const logoutUser = async () => {
  try {
    const refreshToken = await getRefreshToken();
    if (refreshToken) {
      try {
        await apiClient.post("/auth/logout", {
          refresh_token: refreshToken,
          all_devices: false,
        });
      } catch {
        // Ignore server logout failure; local logout must still succeed.
      }
    }

    await clearSessionState();
    return { success: true };
  } catch (error: any) {
    return { success: false, error: error.message };
  }
};

// ==================== API STATUS ====================

/**
 * API холболтыг шалгах (Health check)
 */
export const checkApiStatus = async () => {
  try {
    const response = await apiClient.get("/health");
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("API холболт амжилтгүй:", error.message);
    return { success: false, error: error.message };
  }
};

// ==================== LIVE RATES ENDPOINTS ====================

/**
 * Бодит цагийн EUR/USD ханш авах
 * @returns {Object} { success, data: { pair, rate, bid, ask, spread, time, source } }
 */
export const getLiveRates = async () => {
  try {
    const response = await apiClient.get("/rates/live");
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Live rates авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

// ==================== SIGNAL ENDPOINTS ====================

/**
 * Get live signal from current production model
 * @param {number} minConfidence - Minimum confidence threshold (default: 80)
 * @param {string} pair - Currency pair (default: "EUR/USD")
 * @returns Signal object with entry, SL, TP, confidence
 */
export const getSignal = async (minConfidence: number = 80, pair: string = "EUR/USD") => {
  try {
    const response = await apiClient.get(
      `/signal?min_confidence=${minConfidence}&pair=${pair}`
    );
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Signal авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

// ==================== SIGNAL STORAGE ENDPOINTS ====================

/**
 * Signal хадгалах (таамаг гарвал database-д хадгална)
 * @param {Object} signalData - Signal object
 * @returns {Object} { success, signal_id }
 */
export const saveSignal = async (signalData: any) => {
  try {
    const response = await apiClient.post("/signal/save", signalData);
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Signal хадгалах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Signal түүх авах
 * @param {Object} options - { pair, limit, signal_type, min_confidence }
 * @returns {Object} { success, signals }
 */
interface SignalHistoryOptions {
  pair?: string;
  limit?: number;
  signal_type?: string;
  min_confidence?: number;
}

export const getSignalsHistory = async (options: SignalHistoryOptions = {}) => {
  try {
    const params = new URLSearchParams();
    if (options.pair) params.append("pair", options.pair);
    if (options.limit) params.append("limit", String(options.limit));
    if (options.signal_type) params.append("signal_type", options.signal_type);
    if (options.min_confidence)
      params.append("min_confidence", String(options.min_confidence));

    const response = await apiClient.get(`/signals/history?${params.toString()}`);
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Signal түүх авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Signal статистик авах
 * @param {string} pair - Currency pair (default: EUR_USD)
 * @returns {Object} { success, stats }
 */
export const getSignalsStats = async (pair = "EUR_USD") => {
  try {
    const response = await apiClient.get(`/signals/stats?pair=${pair}`);
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Signal stats авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * DB-д хадгалагдсан сүүлийн сигнал авах (auto + manual)
 * @param {string} pair - Currency pair (default: EUR_USD)
 * @param {number} minConfidence - Minimum confidence (0-100)
 * @returns {Object} { success, signal }
 */
export const getLatestSignal = async (
  pair: string = "EUR/USD",
  minConfidence?: number
) => {
  try {
    const pairParam = pair.replace("/", "_");
    const minConfidenceParam =
      typeof minConfidence === "number" ? `&min_confidence=${minConfidence}` : "";
    const response = await apiClient.get(
      `/signals/latest?pair=${pairParam}${minConfidenceParam}`
    );
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Latest signal авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * DB-д хадгалагдсан сүүлийн сигналуудыг босгоор шүүж авах
 * @param {string} pair - Currency pair (default: EUR/USD)
 * @param {number} limit - Хэдийг авах (default: 5)
 * @param {number} minConfidence - Minimum confidence (0-100)
 * @returns {Object} { success, signals[] }
 */
export const getRecentSignals = async (
  pair: string = "EUR/USD",
  limit: number = 5,
  minConfidence?: number
) => {
  try {
    const pairParam = pair.replace("/", "_");
    const minConfidenceParam =
      typeof minConfidence === "number" ? `&min_confidence=${minConfidence}` : "";
    const response = await apiClient.get(
      `/signals/latest?pair=${pairParam}&limit=${limit}${minConfidenceParam}`
    );
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Recent signals авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Market Analysis авах
 * @param {string} pair - Currency pair (e.g. "EUR/USD")
 * @returns {Object} { success, data }
 */
export const getMarketAnalysis = async (pair: string) => {
  try {
    const response = await apiClient.get(`/api/market-analysis?pair=${pair}`);
    let payload = response.data || {};

    if (response.status === 202 || String(payload.status || "").toLowerCase() === "pending") {
      const jobId = String(payload.job_id || "").trim();
      if (!jobId) {
        return {
          success: false,
          error: "Analysis job id олдсонгүй",
          statusCode: 503,
        };
      }

      const maxPollAttempts = 8;
      for (let attempt = 0; attempt < maxPollAttempts; attempt += 1) {
        const pollResponse = await apiClient.get(`/api/market-analysis/status/${jobId}`);
        const pollPayload = pollResponse.data || {};
        const pollStatus = String(pollPayload.status || "").toLowerCase();
        const jobStatus = String(pollPayload.job_status || pollStatus).toLowerCase();

        if (pollStatus === "success" && pollPayload.data) {
          payload = pollPayload;
          break;
        }

        if ((pollResponse.status === 202 || pollStatus === "pending" || jobStatus === "queued" || jobStatus === "running") && attempt < maxPollAttempts - 1) {
          const retryAfterSeconds = Number(pollPayload.poll_after_seconds || 2) || 2;
          await wait(Math.max(1, retryAfterSeconds) * 1000);
          continue;
        }

        return {
          success: false,
          error: pollPayload.message || pollPayload.error || "Market analysis бэлэн болоогүй байна",
          statusCode: pollResponse.status,
          retryAfter: Number(pollPayload.retry_after || pollPayload.poll_after_seconds || 0) || undefined,
        };
      }
    }

    if (!payload?.data) {
      return {
        success: false,
        error: payload?.message || "Market analysis өгөгдөл хоосон байна",
        statusCode: response.status,
      };
    }

    const analysisData = payload.data || {};

    const analysisMeta = {
      analysisSource: payload.analysis_source || (payload.cached ? (payload.stale ? "cache-stale" : "cache-fresh") : "fresh-generated"),
      cached: Boolean(payload.cached),
      stale: Boolean(payload.stale),
      generatedAt: payload.generated_at || null,
    };

    return {
      success: true,
      data: {
        ...analysisData,
        __meta: analysisMeta,
      },
    };
  } catch (error: any) {
    console.error("Market analysis авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
      statusCode: error.response?.status,
      retryAfter: Number(error.response?.data?.retry_after || 0) || undefined,
    };
  }
};

/**
 * Хэрэглэгчийн мэдээлэл шинэчлэх (нэр)
 */
export const updateUserProfile = async (name: string) => {
  try {
    const response = await apiClient.put("/auth/update", { name });
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Profile шинэчлэх алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Нууц үг солих
 */
export const changeUserPassword = async (oldPassword: string, newPassword: string) => {
  try {
    const response = await apiClient.put("/auth/change-password", {
      oldPassword,
      newPassword,
    });
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Нууц үг солих алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Мэдээ авах
 * @param {string} type - 'upcoming' | 'history' | 'past' | 'outlook'
 */
export const getNews = async (type: string = "upcoming") => {
  try {
    const normalizedType = type === "past" ? "history" : type;
    const response = await apiClient.get(`/api/news?type=${normalizedType}`);
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Мэдээ авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * AI мэдээ дүн шинжилгээ хийх
 */
export const analyzeNewsEvent = async (eventData: any) => {
  try {
    const response = await apiClient.post("/api/news/analyze", eventData);
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("Мэдээ дүн шинжилгээ алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

// ==================== IN-APP NOTIFICATIONS ====================

export interface InAppNotification {
  _id: string;
  type: string;
  title: string;
  body: string;
  data: any;
  created_at: string;
  is_read: boolean;
}

/**
 * In-app мэдэгдлүүдийг серверээс авах
 * @param limit - Хамгийн ихдээ хэдийг авах (default: 20)
 * @param type - Шүүлтүүр: 'signal' | 'news' | 'system' (optional)
 */
export const getInAppNotifications = async (limit: number = 20, type?: string): Promise<ApiResponse<{ notifications: InAppNotification[], count: number }>> => {
  try {
    let url = `/notifications/in-app?limit=${limit}`;
    if (type) url += `&type=${type}`;
    const response = await apiClient.get(url);
    return { success: true, data: response.data };
  } catch (error: any) {
    console.error("In-app мэдэгдэл авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

export const getUnreadNotificationCount = async (): Promise<ApiResponse<{ unread_count: number }>> => {
  try {
    const response = await apiClient.get('/notifications/in-app/unread-count');
    return { success: true, data: response.data };
  } catch (error: any) {
    return { success: false, error: error.response?.data?.error || error.message };
  }
};

export const markNotificationsRead = async (ids?: string[]): Promise<ApiResponse<{ modified: number }>> => {
  try {
    const response = await apiClient.post('/notifications/in-app/mark-read', { ids: ids || [] });
    return { success: true, data: response.data };
  } catch (error: any) {
    return { success: false, error: error.response?.data?.error || error.message };
  }
};

export default {
  // Auth
  registerUser,
  verifyEmail,
  resendVerificationCode,
  loginUser,
  forgotPassword,
  verifyResetCode,
  resetPassword,
  logoutUser,
  // API
  checkApiStatus,
  getLiveRates,
  getSignal,
  // Signal storage
  saveSignal,
  getSignalsHistory,
  getSignalsStats,
  getMarketAnalysis,
  updateUserProfile,
  changeUserPassword,
  getNews,
  analyzeNewsEvent,
  getInAppNotifications,
  getUnreadNotificationCount,
  markNotificationsRead,
};
