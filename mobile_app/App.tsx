import React, { useState, useEffect, useRef } from "react";
import {
  StatusBar,
  View,
  AppState,
  AppStateStatus,
} from "react-native";
import { NavigationContainer, NavigationContainerRef } from "@react-navigation/native";
import { createStackNavigator } from "@react-navigation/stack";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, useTheme } from "./src/context/ThemeContext";
import { getColors } from "./src/config/theme";
import LoginScreen from "./src/screens/LoginScreen";
import SignUpScreen from "./src/screens/SignUpScreen";
import EmailVerificationScreen from "./src/screens/EmailVerificationScreen";
import ForgotPasswordScreen from "./src/screens/ForgotPasswordScreen";
import MainTabs from "./src/navigation/MainTabs";
import SignalScreen from "./src/screens/SignalScreen";
import NotificationsScreen from "./src/screens/NotificationsScreen";
import {
  initializePushNotifications,
  setupNotificationListeners,
} from "./src/services/notificationService";
import { AlertProvider } from "./src/context/AlertContext";
import AppAlert from "./src/components/AppAlert";
import SplashScreen from "./src/screens/SplashScreen";

const Stack = createStackNavigator();
const queryClient = new QueryClient();

function AppContent() {
  const [showSplash, setShowSplash] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [userLoggedIn, setUserLoggedIn] = useState(false);
  const { isDark } = useTheme();
  const colors = getColors(isDark);
  const navigationRef = useRef<NavigationContainerRef<any>>(null);
  const pendingNotificationDataRef = useRef<any | null>(null);

  const navigateFromNotificationData = (data: any) => {
    if (!data) return;

    const nav = navigationRef.current;
    if (!nav || !nav.isReady()) {
      pendingNotificationDataRef.current = data;
      return;
    }

    try {
      if (data?.type === "signal" || data?.screen === "Signal") {
        nav.navigate("Main", { screen: "PredictionTab" });
      } else if (data?.screen === "News") {
        nav.navigate("Main", { screen: "NewsTab" });
      } else if (data?.screen === "Profile") {
        nav.navigate("Main", { screen: "ProfileTab" });
      }
    } catch (e) {
      console.log("[WARN] Navigation from notification failed:", e);
    }
  };

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener(
      "change",
      async (nextState: AppStateStatus) => {
        if (nextState !== "active") return;

        try {
          const token = await AsyncStorage.getItem("userToken");
          const isLoggedIn = !!token;
          setUserLoggedIn(isLoggedIn);

          if (isLoggedIn) {
            await initializePushNotifications();
          }
        } catch (err) {
          console.log("[WARN] App foreground push sync failed:", err);
        }
      }
    );

    return () => {
      subscription.remove();
    };
  }, []);

  // Initialize push notifications after auth check
  useEffect(() => {
    if (!isLoading && userLoggedIn) {
      initializePushNotifications().then((token) => {
        if (token) {
          console.log("[OK] Push notifications initialized");
        }
      });

      // Set up notification listeners
      const cleanup = setupNotificationListeners(
        // Foreground notification received
        (notification) => {
          console.log("[NOTIFICATION]", notification.request.content.title);
        },
        // User tapped a notification
        (response) => {
          const data = response.notification.request.content.data;
          navigateFromNotificationData(data);
        }
      );

      return cleanup;
    }
  }, [isLoading, userLoggedIn]);

  const checkAuthStatus = async () => {
    try {
      const token = await AsyncStorage.getItem("userToken");
      setUserLoggedIn(!!token);
    } catch (error) {
      setUserLoggedIn(false);
    } finally {
      setIsLoading(false);
    }
  };

  if (showSplash || isLoading) {
    return (
      <>
        <View
          style={{
            flex: 1,
            backgroundColor: colors.background,
          }}
        />
        {showSplash && (
          <SplashScreen onFinish={() => setShowSplash(false)} />
        )}
      </>
    );
  }

  return (
    <>
      <StatusBar
        barStyle={isDark ? "light-content" : "dark-content"}
        backgroundColor={colors.primary}
      />
      <AppAlert />
      <NavigationContainer
        ref={navigationRef}
        onReady={() => {
          if (pendingNotificationDataRef.current) {
            const pending = pendingNotificationDataRef.current;
            pendingNotificationDataRef.current = null;
            navigateFromNotificationData(pending);
          }
        }}
      >
        <Stack.Navigator
          initialRouteName={userLoggedIn ? "Main" : "Login"}
          screenOptions={{
            headerStyle: {
              backgroundColor: colors.primary,
            },
            headerTintColor: colors.textPrimary,
            headerTitleStyle: {
              fontWeight: "bold",
              fontSize: 20,
            },
            cardStyle: { backgroundColor: colors.background },
          }}
        >
          <Stack.Screen
            name="Login"
            component={LoginScreen}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="SignUp"
            component={SignUpScreen}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="EmailVerification"
            component={EmailVerificationScreen}
            options={{
              headerShown: false,
            }}
          />
          <Stack.Screen
            name="ForgotPassword"
            component={ForgotPasswordScreen}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="Main"
            component={MainTabs}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="Signal"
            component={SignalScreen as any}
            options={{
              headerShown: false,
            }}
          />
          <Stack.Screen
            name="Notifications"
            component={NotificationsScreen}
            options={{
              headerShown: false,
            }}
          />
        </Stack.Navigator>
      </NavigationContainer>
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AlertProvider>
          <AppContent />
        </AlertProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
