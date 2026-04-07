import React, { useState, useRef } from "react";
import {
  View,
  Text,
  Image,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StatusBar,
} from "react-native";
import { useTheme } from "../context/ThemeContext";
import { useAlert } from "../context/AlertContext";
import { getColors } from "../config/theme";
import {
  forgotPassword,
  verifyResetCode,
  resetPassword,
} from "../services/api";
import { ChevronLeft, Sun, Moon, Eye, EyeOff } from 'lucide-react-native';

/**
 * Forgot Password Screen - Professional minimal design
 * Step 1: Email
 * Step 2: Verify Code
 * Step 3: New Password
 */
const ForgotPasswordScreen = ({ navigation }: { navigation: any }) => {
  const { isDark, toggleTheme } = useTheme();
  const colors = getColors(isDark);
  const styles = createStyles(colors);
  const { showAlert } = useAlert();

  const [step, setStep] = useState(1); // 1: Email, 2: Code, 3: New Password
  const [email, setEmail] = useState("");
  const [code, setCode] = useState(["", "", "", "", "", ""]);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [verifiedCode, setVerifiedCode] = useState("");
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [demoCode, setDemoCode] = useState(null);

  const inputRefs = useRef<(TextInput | null)[]>([]);

  // Step 1: Имэйл илгээх
  const handleSendEmail = async () => {
    if (!email.trim() || !email.includes("@")) {
      showAlert("Алдаа", "Зөв имэйл хаяг оруулна уу");
      return;
    }

    setVerifiedCode("");
    setLoading(true);

    try {
      const result = await forgotPassword(email);

      if (result.success) {
        if (result.data.demo_mode) {
          setDemoCode(result.data.reset_code);
          showAlert(
            "Demo Mode",
            `Таны сэргээх код: ${result.data.reset_code}\n\n(Имэйл тохиргоо хийгдээгүй учир demo горимд ажиллаж байна)`,
            [{ text: "OK", onPress: () => setStep(2) }]
          );
        } else {
          showAlert(
            "Амжилттай",
            "Сэргээх код таны имэйл хаяг руу илгээгдлээ",
            [{ text: "OK", onPress: () => setStep(2) }]
          );
        }
      } else {
        showAlert("Алдаа", result.error || "Код илгээх амжилтгүй");
      }
    } catch (error: any) {
      showAlert("Алдаа", "Код илгээх явцад алдаа гарлаа");
    } finally {
      setLoading(false);
    }
  };

  // Step 2: Код шалгах
  const handleVerifyCode = async () => {
    const verificationCode = code.join("");

    if (verificationCode.length !== 6) {
      showAlert("Алдаа", "6 оронтой кодыг бүрэн оруулна уу");
      return;
    }

    setLoading(true);

    try {
      const result = await verifyResetCode(email, verificationCode);

      if (result.success) {
        setVerifiedCode(verificationCode);
        showAlert("Амжилттай", "Код баталгаажлаа", [
          { text: "OK", onPress: () => setStep(3) },
        ]);
      } else {
        setVerifiedCode("");
        showAlert("Алдаа", result.error || "Буруу код");
        setCode(["", "", "", "", "", ""]);
        inputRefs.current[0]?.focus();
      }
    } catch (error: any) {
      showAlert("Алдаа", "Код шалгах явцад алдаа гарлаа");
    } finally {
      setLoading(false);
    }
  };

  // Step 3: Нууц үг солих
  const handleResetPassword = async () => {
    if (!newPassword.trim() || !confirmPassword.trim()) {
      showAlert("Алдаа", "Нууц үгээ оруулна уу");
      return;
    }

    if (newPassword.length < 6) {
      showAlert("Алдаа", "Нууц үг дор хаяж 6 тэмдэгттэй байх ёстой");
      return;
    }

    if (newPassword !== confirmPassword) {
      showAlert("Алдаа", "Нууц үг таарахгүй байна");
      return;
    }

    setLoading(true);

    try {
      const codeToUse = verifiedCode || code.join("");
      const result = await resetPassword(email, codeToUse, newPassword);

      if (result.success) {
        showAlert("Амжилттай!", "Таны нууц үг амжилттай солигдлоо", [
          {
            text: "OK",
            onPress: () => navigation.navigate("Login"),
          },
        ]);
      } else {
        showAlert("Алдаа", result.error || "Нууц үг солих амжилтгүй");
      }
    } catch (error: any) {
      showAlert("Алдаа", "Нууц үг солих явцад алдаа гарлаа");
    } finally {
      setLoading(false);
    }
  };

  const handleCodeChange = (text: string, index: number) => {
    if (text && !/^\d+$/.test(text)) return;

    const newCode = [...code];
    newCode[index] = text;
    setCode(newCode);

    if (text && index < 5) {
      inputRefs.current[index + 1]?.focus();
    }
  };

  const handleKeyPress = (e: any, index: number) => {
    if (e.nativeEvent.key === "Backspace" && !code[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const renderStep1 = () => (
    <View style={styles.formContainer}>
      <View style={styles.inputWrapper}>
        <Text style={styles.inputLabel}>ИМЭЙЛ ХАЯГ</Text>
        <View style={styles.inputContainer}>
          <TextInput
            style={styles.input}
            placeholder="Имэйл хаягаа оруулна уу"
            placeholderTextColor={colors.textSecondary}
            value={email}
            onChangeText={setEmail}
            keyboardType="email-address"
            autoCapitalize="none"
            autoComplete="email"
          />
        </View>
      </View>

      <TouchableOpacity
        style={[styles.primaryButton, loading && styles.disabledButton]}
        onPress={handleSendEmail}
        disabled={loading}
      >
        {loading ? (
          <ActivityIndicator color="#FFFFFF" size="small" />
        ) : (
          <Text style={styles.primaryButtonText}>Код авах</Text>
        )}
      </TouchableOpacity>
    </View>
  );

  const renderStep2 = () => (
    <View style={styles.formContainer}>
      <Text style={styles.instructionText}>
        {email} хаяг руу илгээсэн 6 оронтой кодыг оруулна уу
      </Text>
      
      <View style={styles.codeContainer}>
        {code.map((digit, index) => (
          <TextInput
            key={index}
            ref={(ref) => (inputRefs.current[index] = ref)}
            style={[
              styles.codeInput,
              digit ? styles.codeInputFilled : null,
            ]}
            value={digit}
            onChangeText={(text) => handleCodeChange(text, index)}
            onKeyPress={(e) => handleKeyPress(e, index)}
            keyboardType="number-pad"
            maxLength={1}
            selectTextOnFocus
          />
        ))}
      </View>

      <TouchableOpacity
        style={[styles.primaryButton, loading && styles.disabledButton]}
        onPress={handleVerifyCode}
        disabled={loading}
      >
        {loading ? (
          <ActivityIndicator color="#FFFFFF" size="small" />
        ) : (
          <Text style={styles.primaryButtonText}>Баталгаажуулах</Text>
        )}
      </TouchableOpacity>

      <TouchableOpacity 
        style={styles.resendButton} 
        onPress={handleSendEmail}
        disabled={loading}
      >
        <Text style={styles.resendText}>Код дахин авах</Text>
      </TouchableOpacity>
    </View>
  );

  const renderStep3 = () => (
    <View style={styles.formContainer}>
      <View style={styles.inputWrapper}>
        <Text style={styles.inputLabel}>ШИНЭ НУУЦ ҮГ</Text>
        <View style={styles.inputContainer}>
          <TextInput
            style={styles.input}
            placeholder="Доод тал нь 6 оронтой"
            placeholderTextColor={colors.textSecondary}
            value={newPassword}
            onChangeText={setNewPassword}
            secureTextEntry={!showNewPassword}
          />
          <TouchableOpacity onPress={() => setShowNewPassword(!showNewPassword)} style={styles.toggleButton}>
            {showNewPassword ? <EyeOff size={18} color={colors.textSecondary} /> : <Eye size={18} color={colors.textSecondary} />}
          </TouchableOpacity>
        </View>
      </View>

      <View style={styles.inputWrapper}>
        <Text style={styles.inputLabel}>НУУЦ ҮГ ДАВТАХ</Text>
        <View style={styles.inputContainer}>
          <TextInput
            style={styles.input}
            placeholder="Нууц үгээ давтан оруулна уу"
            placeholderTextColor={colors.textSecondary}
            value={confirmPassword}
            onChangeText={setConfirmPassword}
            secureTextEntry={!showConfirmPassword}
          />
          <TouchableOpacity onPress={() => setShowConfirmPassword(!showConfirmPassword)} style={styles.toggleButton}>
            {showConfirmPassword ? <EyeOff size={18} color={colors.textSecondary} /> : <Eye size={18} color={colors.textSecondary} />}
          </TouchableOpacity>
        </View>
      </View>

      <TouchableOpacity
        style={[styles.primaryButton, loading && styles.disabledButton]}
        onPress={handleResetPassword}
        disabled={loading}
      >
        {loading ? (
          <ActivityIndicator color="#FFFFFF" size="small" />
        ) : (
          <Text style={styles.primaryButtonText}>Хадгалах</Text>
        )}
      </TouchableOpacity>
    </View>
  );

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={styles.container}
    >
      <StatusBar barStyle={isDark ? "light-content" : "dark-content"} backgroundColor={colors.background} />
      <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
        <View style={styles.content}>
          {/* Header */}
          <View style={styles.headerContainer}>
            <View style={styles.topRow}>
              <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backButton}>
                <ChevronLeft size={20} color={colors.textSecondary} />
                <Text style={styles.backText}>Буцах</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={toggleTheme} style={styles.themeToggle}>
                {isDark ? (
                  <Sun size={22} color={colors.textSecondary} />
                ) : (
                  <Moon size={22} color={colors.textSecondary} />
                )}
              </TouchableOpacity>
            </View>
            <Image source={require('../../assets/icon.png')} style={styles.appIcon} />
            <Text style={styles.title}>НУУЦ ҮГ СЭРГЭЭХ</Text>
            <Text style={styles.subtitle}>
              {step === 1 && "Имэйл хаягаа оруулна уу"}
              {step === 2 && "Баталгаажуулах код"}
              {step === 3 && "Шинэ нууц үг үүсгэх"}
            </Text>
          </View>

          {/* Steps Indicator */}
          <View style={styles.progressContainer}>
            {[1, 2, 3].map((s) => (
              <View
                key={s}
                style={[
                  styles.progressDot,
                  step >= s && styles.progressDotActive,
                  step === s && styles.progressDotCurrent,
                ]}
              />
            ))}
          </View>

          {step === 1 && renderStep1()}
          {step === 2 && renderStep2()}
          {step === 3 && renderStep3()}

        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
};

const createStyles = (colors: any) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  scrollContent: {
    flexGrow: 1,
  },
  content: {
    flex: 1,
    padding: 24,
    paddingTop: 50,
  },
  headerContainer: {
    marginBottom: 32,
  },
  topRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 24,
  },
  themeToggle: {
    padding: 8,
  },
  appIcon: {
    width: 160,
    height: 160,
    borderRadius: 40,
    marginBottom: 16,
    alignSelf: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.15,
    shadowRadius: 6,
  },
  backButton: {
    padding: 4,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
  },
  backText: {
    color: colors.textSecondary,
    fontSize: 14,
  },
  title: {
    fontSize: 24,
    fontWeight: "700",
    color: colors.textPrimary,
    letterSpacing: 2,
  },
  subtitle: {
    fontSize: 14,
    color: colors.textSecondary,
    marginTop: 8,
  },
  progressContainer: {
    flexDirection: "row",
    marginBottom: 32,
    gap: 8,
  },
  progressDot: {
    width: 40,
    height: 4,
    backgroundColor: colors.border,
    borderRadius: 2,
  },
  progressDotActive: {
    backgroundColor: colors.success,
    opacity: 0.5,
  },
  progressDotCurrent: {
    backgroundColor: colors.success,
    opacity: 1,
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
  primaryButton: {
    backgroundColor: colors.success,
    borderRadius: 8,
    height: 50,
    justifyContent: "center",
    alignItems: "center",
    marginTop: 8,
  },
  disabledButton: {
    opacity: 0.5,
  },
  primaryButtonText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: "600",
    letterSpacing: 1,
  },
  instructionText: {
    color: colors.textSecondary,
    fontSize: 14,
    marginBottom: 24,
    textAlign: 'center',
  },
  codeContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 24,
    gap: 8,
  },
  codeInput: {
    flex: 1,
    height: 50,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    backgroundColor: colors.background,
    color: colors.textPrimary,
    fontSize: 20,
    fontWeight: "bold",
    textAlign: "center",
  },
  codeInputFilled: {
    borderColor: colors.success,
    backgroundColor: colors.background,
  },
  resendButton: {
    marginTop: 16,
    alignItems: 'center',
  },
  resendText: {
    color: colors.textSecondary,
    fontSize: 13,
  },
});

export default ForgotPasswordScreen;
