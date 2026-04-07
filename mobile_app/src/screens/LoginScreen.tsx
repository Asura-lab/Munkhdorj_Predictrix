import React, { useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StatusBar,
} from "react-native";
import { useTheme } from "../context/ThemeContext";
import { useAlert } from "../context/AlertContext";
import { getColors } from "../config/theme";
import { loginUser } from "../services/api";
import { initializePushNotifications } from "../services/notificationService";
import { NavigationProp } from "@react-navigation/native";
import { Sun, Moon, Eye, EyeOff } from 'lucide-react-native';

interface LoginScreenProps {
  navigation: NavigationProp<any>;
}

/**
 * Login Screen - Professional minimal design
 */
const LoginScreen: React.FC<LoginScreenProps> = ({ navigation }) => {
  const { isDark, toggleTheme } = useTheme();
  const colors = getColors(isDark);
  const { showAlert } = useAlert();

  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [showPassword, setShowPassword] = useState<boolean>(false);

  const handleLogin = async () => {
    if (!email.trim() || !password.trim()) {
      showAlert("Алдаа", "Имэйл болон нууц үг оруулна уу");
      return;
    }

    if (!email.includes("@")) {
      showAlert("Алдаа", "Зөв имэйл хаяг оруулна уу");
      return;
    }

    setLoading(true);

    try {
      const result = await loginUser(email, password);

      if (result.success) {
        // Register/refresh push token right after login so alerts work even when app is closed.
        try {
          await initializePushNotifications();
        } catch {
          // Push init failures are non-blocking for login flow.
        }

        navigation.reset({
          index: 0,
          routes: [{ name: "Main" }],
        });
      } else {
        if (result.requiresVerification) {
          showAlert(
            "Имэйл баталгаажуулах",
            "Та имэйл хаягаа баталгаажуулах шаардлагатай",
            [
              {
                text: "Баталгаажуулах",
                onPress: () =>
                  navigation.navigate("EmailVerification", { email }),
              },
              { text: "Болих", style: "cancel" },
            ]
          );
        } else {
          showAlert("Алдаа", result.error || "Нэвтрэх амжилтгүй боллоо");
        }
      }
    } catch (error: any) {
      console.error("Login error:", error);
      let errorMessage = "Нэвтрэх явцад алдаа гарлаа.";

      if (error.message === "Network Error") {
        errorMessage = "Сүлжээний алдаа! Backend server холбогдохгүй байна.";
      } else if (error.code === "ECONNABORTED") {
        errorMessage = "Хүлээх хугацаа дууслаа.";
      }

      showAlert("Алдаа", errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const styles = createStyles(colors);

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={styles.container}
    >
      <StatusBar barStyle={isDark ? "light-content" : "dark-content"} backgroundColor={colors.background} />
      
      <View style={styles.content}>
        {/* Theme Toggle */}
        <TouchableOpacity onPress={toggleTheme} style={styles.themeToggle}>
          {isDark ? (
            <Sun size={22} color={colors.textSecondary} />
          ) : (
            <Moon size={22} color={colors.textSecondary} />
          )}
        </TouchableOpacity>

        {/* Header */}
        <View style={styles.headerContainer}>
          <Text style={styles.title}>PREDICTRIX</Text>
          <Text style={styles.subtitle}>AI Trading Assistant</Text>
        </View>

        {/* Form */}
        <View style={styles.formContainer}>
          {/* Email */}
          <View style={styles.inputWrapper}>
            <Text style={styles.inputLabel}>EMAIL</Text>
            <View style={styles.inputContainer}>
              <TextInput
                style={styles.input}
                placeholder="Enter your email"
                placeholderTextColor={colors.placeholderText}
                value={email}
                onChangeText={setEmail}
                keyboardType="email-address"
                autoCapitalize="none"
                autoComplete="email"
              />
            </View>
          </View>

          {/* Password */}
          <View style={styles.inputWrapper}>
            <Text style={styles.inputLabel}>PASSWORD</Text>
            <View style={styles.inputContainer}>
              <TextInput
                style={styles.input}
                placeholder="Enter password"
                placeholderTextColor={colors.placeholderText}
                value={password}
                onChangeText={setPassword}
                secureTextEntry={!showPassword}
                autoCapitalize="none"
              />
              <TouchableOpacity
                onPress={() => setShowPassword(!showPassword)}
                style={styles.toggleButton}
              >
                {showPassword ? <EyeOff size={18} color={colors.textSecondary} /> : <Eye size={18} color={colors.textSecondary} />}
              </TouchableOpacity>
            </View>
          </View>

          {/* Forgot Password */}
          <TouchableOpacity
            onPress={() => navigation.navigate("ForgotPassword")}
            style={styles.forgotButton}
          >
            <Text style={styles.forgotText}>Forgot password?</Text>
          </TouchableOpacity>

          {/* Login Button */}
          <TouchableOpacity
            style={[styles.loginButton, loading && styles.disabledButton]}
            onPress={handleLogin}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#FFFFFF" size="small" />
            ) : (
              <Text style={styles.loginButtonText}>Sign In</Text>
            )}
          </TouchableOpacity>

          {/* Divider */}
          <View style={styles.divider}>
            <View style={styles.dividerLine} />
            <Text style={styles.dividerText}>OR</Text>
            <View style={styles.dividerLine} />
          </View>

          {/* Sign Up */}
          <TouchableOpacity
            style={styles.signupButton}
            onPress={() => navigation.navigate("SignUp")}
          >
            <Text style={styles.signupButtonText}>Create Account</Text>
          </TouchableOpacity>
        </View>

        {/* Footer */}
        <Text style={styles.footerText}>
          For research purposes only
        </Text>
      </View>
    </KeyboardAvoidingView>
  );
};

const createStyles = (colors: any) =>
  StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    themeToggle: {
      alignSelf: 'flex-end',
      padding: 8,
      marginBottom: 8,
    },
    content: {
      flex: 1,
      justifyContent: "center",
      padding: 24,
    },
    headerContainer: {
      alignItems: "center",
      marginBottom: 36,
    },
    title: {
      fontSize: 28,
      fontWeight: "700",
      color: colors.textPrimary,
      letterSpacing: 3,
    },
    subtitle: {
      fontSize: 13,
      color: colors.textSecondary,
      marginTop: 8,
      letterSpacing: 1,
    },
    formContainer: {
      backgroundColor: colors.card,
      borderRadius: 16,
      padding: 24,
    },
    inputWrapper: {
      marginBottom: 20,
    },
    inputLabel: {
      fontSize: 11,
      fontWeight: '600',
      color: colors.textSecondary,
      marginBottom: 8,
      letterSpacing: 1,
    },
    inputContainer: {
      flexDirection: "row",
      alignItems: "center",
      backgroundColor: colors.background,
      borderRadius: 8,
      height: 50,
      paddingHorizontal: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    input: {
      flex: 1,
      fontSize: 15,
      color: colors.textPrimary,
    },
    toggleButton: {
      paddingHorizontal: 8,
    },
    forgotButton: {
      alignSelf: 'flex-end',
      marginBottom: 24,
    },
    forgotText: {
      fontSize: 13,
      color: colors.textSecondary,
    },
    loginButton: {
      backgroundColor: colors.success,
      borderRadius: 8,
      height: 50,
      justifyContent: "center",
      alignItems: "center",
    },
    disabledButton: {
      opacity: 0.6,
    },
    loginButtonText: {
      color: '#FFFFFF',
      fontSize: 15,
      fontWeight: "600",
      letterSpacing: 1,
    },
    divider: {
      flexDirection: 'row',
      alignItems: 'center',
      marginVertical: 24,
    },
    dividerLine: {
      flex: 1,
      height: 1,
      backgroundColor: colors.border,
    },
    dividerText: {
      fontSize: 12,
      color: colors.textSecondary,
      marginHorizontal: 16,
    },
    signupButton: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 8,
      height: 50,
      justifyContent: "center",
      alignItems: "center",
    },
    signupButtonText: {
      color: colors.textPrimary,
      fontSize: 15,
      fontWeight: "500",
    },
    footerText: {
      color: colors.textSecondary,
      fontSize: 12,
      textAlign: "center",
      marginTop: 32,
    },
  });

export default LoginScreen;
