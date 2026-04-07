import axios, { AxiosInstance, InternalAxiosRequestConfig } from "axios";
import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE_URL } from "../config/api";
import { getDeviceId } from "./notificationService";

const TOKEN_STORAGE_KEY = "userToken";
const USER_STORAGE_KEY = "userData";
let unauthorizedHandled = false;

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
  user?: UserData;
  requiresVerification?: boolean;
}

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
    const token = await AsyncStorage.getItem(TOKEN_STORAGE_KEY);
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
      // Clear session only once for a burst of unauthorized responses.
      const hadAuthHeader = Boolean(error?.config?.headers?.Authorization);
      if (hadAuthHeader && !unauthorizedHandled) {
        unauthorizedHandled = true;
        await AsyncStorage.multiRemove([TOKEN_STORAGE_KEY, USER_STORAGE_KEY]);
      }
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
export const registerUser = async (name: string, email: string, password: string): Promise<ApiResponse> => {
  try {
    const response = await apiClient.post("/auth/register", {
      name,
      email,
      password,
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
      unauthorizedHandled = false;
      await AsyncStorage.setItem(TOKEN_STORAGE_KEY, response.data.token);
      await AsyncStorage.setItem(
        USER_STORAGE_KEY,
        JSON.stringify(response.data.user)
      );
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
      unauthorizedHandled = false;
      await AsyncStorage.setItem(TOKEN_STORAGE_KEY, response.data.token);
      await AsyncStorage.setItem(
        USER_STORAGE_KEY,
        JSON.stringify(response.data.user)
      );
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
    unauthorizedHandled = false;
    await AsyncStorage.removeItem(TOKEN_STORAGE_KEY);
    await AsyncStorage.removeItem(USER_STORAGE_KEY);
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
 * Сүүлийн auto-generated сигнал авах
 * @param {string} pair - Currency pair (default: EUR_USD)
 * @returns {Object} { success, signal }
 */
export const getLatestSignal = async (pair: string = "EUR/USD") => {
  try {
    const pairParam = pair.replace("/", "_");
    const response = await apiClient.get(`/signals/latest?pair=${pairParam}`);
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
 * Сүүлийн хэд хэдэн өндөр итгэлцэлтэй сигналуудыг авах
 * @param {string} pair - Currency pair (default: EUR/USD)
 * @param {number} limit - Хэдийг авах (default: 5)
 * @returns {Object} { success, signals[] }
 */
export const getRecentSignals = async (pair: string = "EUR/USD", limit: number = 5) => {
  try {
    const pairParam = pair.replace("/", "_");
    const response = await apiClient.get(`/signals/latest?pair=${pairParam}&limit=${limit}`);
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
    // Backend returns { status: "success", data: { ... } }
    // We want to return the inner data object
    return { success: true, data: response.data.data };
  } catch (error: any) {
    console.error("Market analysis авах алдаа:", error.message);
    return {
      success: false,
      error: error.response?.data?.error || error.message,
    };
  }
};

/**
 * Хэрэглэгчийн мэдээлэл шинэчлэх (нэр)
 */
export const updateUserProfile = async (name: string) => {
  try {
    const response = await apiClient.put("/auth/update", { name });
    const updatedUser = response.data?.user;
    if (updatedUser) {
      await AsyncStorage.setItem(USER_STORAGE_KEY, JSON.stringify(updatedUser));
    }
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
      old_password: oldPassword,
      new_password: newPassword,
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
 * @param {string} type - 'upcoming' | 'past' | 'outlook'
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
