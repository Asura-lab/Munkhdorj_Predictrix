import React, { useState, useEffect } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  Switch,
  Modal,
  ActivityIndicator,
  StatusBar,
  Platform,
  Linking,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useTheme } from "../context/ThemeContext";
import { useAlert } from "../context/AlertContext";
import { getColors } from "../config/theme";
import { logoutUser, updateUserProfile, changeUserPassword } from "../services/api";
import { NavigationProp } from "@react-navigation/native";
import {
  getNotificationPreferences,
  updateNotificationPreferences,
  initializePushNotifications,
  unregisterPushTokenFromServer,
  NewsImpactFilter,
} from "../services/notificationService";
import { ChevronRight, Check } from 'lucide-react-native';

interface UserData {
  name: string;
  email: string;
  [key: string]: any;
}

interface ProfileScreenProps {
  navigation: NavigationProp<any>;
}

const USER_DATA_PRIMARY_KEY = "userData";
const USER_DATA_LEGACY_KEY = "@user_data";

/**
 * Profile Screen - Хэрэглэгчийн профайл
 */
const ProfileScreen: React.FC<ProfileScreenProps> = ({ navigation }) => {
  const { isDark, themeMode, setTheme } = useTheme();
  const colors = getColors(isDark);
  const styles = createStyles(colors);
  const { showAlert } = useAlert();

  const [userData, setUserData] = useState<UserData | null>(null);
  const [editMode, setEditMode] = useState<boolean>(false);
  const [name, setName] = useState<string>("");
  const [email, setEmail] = useState<string>("");
  const [notifications, setNotifications] = useState<boolean>(true);
  const [signalNotifications, setSignalNotifications] = useState<boolean>(true);
  const [newsNotifications, setNewsNotifications] = useState<boolean>(true);
  const [newsImpactFilter, setNewsImpactFilter] = useState<NewsImpactFilter>("high");
  const [securityNotifications, setSecurityNotifications] = useState<boolean>(true);
  const [showImpactFilterModal, setShowImpactFilterModal] = useState<boolean>(false);
  const [signalThreshold, setSignalThreshold] = useState<number>(0.9);
  const [showSignalThresholdModal, setShowSignalThresholdModal] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [showPasswordModal, setShowPasswordModal] = useState<boolean>(false);
  const [oldPassword, setOldPassword] = useState<string>("");
  const [newPassword, setNewPassword] = useState<string>("");
  const [confirmPassword, setConfirmPassword] = useState<string>("");
  const [showDocumentModal, setShowDocumentModal] = useState<boolean>(false);
  const [currentDocument, setCurrentDocument] = useState<any>(null);
  const [showThemeModal, setShowThemeModal] = useState<boolean>(false);
  const [stats, setStats] = useState({
    daysUsed: 0,
    signalsReceived: 0,
    lastActive: null as string | null,
  });

  useEffect(() => {
    loadUserData();
    loadSettings();
    loadStats();
  }, []);

  const loadUserData = async () => {
    try {
      const data =
        (await AsyncStorage.getItem(USER_DATA_PRIMARY_KEY)) ||
        (await AsyncStorage.getItem(USER_DATA_LEGACY_KEY));
      if (data) {
        const parsed = JSON.parse(data);
        setUserData(parsed);
        setName(parsed.name);
        setEmail(parsed.email);
      }
    } catch (error: any) {
      console.error("Load user data error:", error);
    }
  };

  const loadSettings = async () => {
    try {
      // Load local notification master toggle
      const savedNotifications = await AsyncStorage.getItem(
        "@notification_settings"
      );

      if (savedNotifications !== null) {
        setNotifications(JSON.parse(savedNotifications));
      }

      // Load server-side notification preferences
      const prefs = await getNotificationPreferences();
      setSignalNotifications(prefs.signal_notifications ?? true);
      setNewsNotifications(prefs.news_notifications ?? true);
      setNewsImpactFilter(prefs.news_impact_filter ?? "high");
      setSecurityNotifications(prefs.security_notifications ?? true);
      setSignalThreshold(prefs.signal_threshold ?? 0.9);
      // Also sync to AsyncStorage for cross-screen access
      await AsyncStorage.setItem("@signal_threshold", String(prefs.signal_threshold ?? 0.9));
      if (prefs.notifications_enabled !== undefined) {
        setNotifications(prefs.notifications_enabled);
      }
    } catch (error: any) {
      console.error("Load settings error:", error);
    }
  };

  const loadStats = async () => {
    try {
      const createdAt = await AsyncStorage.getItem("@user_created_at");
      if (createdAt) {
        const daysDiff = Math.floor(
          (Date.now() - new Date(createdAt).getTime()) / (1000 * 60 * 60 * 24)
        );
        setStats((prev) => ({
          ...prev,
          daysUsed: daysDiff,
          lastActive: new Date().toLocaleDateString("mn-MN"),
        }));
      }
    } catch (error: any) {
      console.error("Load stats error:", error);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) {
      showAlert("Алдаа", "Нэр хоосон байж болохгүй");
      return;
    }

    setLoading(true);
    try {
      const result = await updateUserProfile(name);

      if (result.success) {
        // Update local storage
        const serverUser = result.data?.user || {};
        const updatedUser = {
          ...(userData || {}),
          ...serverUser,
          name,
          email: serverUser.email || userData?.email || email,
        };
        await AsyncStorage.setItem(USER_DATA_PRIMARY_KEY, JSON.stringify(updatedUser));
        await AsyncStorage.setItem(USER_DATA_LEGACY_KEY, JSON.stringify(updatedUser));
        setUserData(updatedUser as UserData);

        showAlert("Амжилттай", "Таны мэдээлэл шинэчлэгдлээ", [
          { text: "OK", onPress: () => setEditMode(false) },
        ]);
      } else {
        showAlert("Алдаа", result.error || "Мэдээлэл шинэчлэхэд алдаа гарлаа");
      }
    } catch (error: any) {
      showAlert("Алдаа", "Серверт холбогдох боломжгүй байна");
      console.error("Update error:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleChangePassword = () => {
    setShowPasswordModal(true);
  };

  const handlePasswordChange = async () => {
    if (!oldPassword || !newPassword || !confirmPassword) {
      showAlert("Алдаа", "Бүх талбарыг бөглөнө үү");
      return;
    }

    if (newPassword.length < 6) {
      showAlert("Алдаа", "Шинэ нууц үг дор хаяж 6 тэмдэгттэй байх ёстой");
      return;
    }

    if (newPassword !== confirmPassword) {
      showAlert("Алдаа", "Шинэ нууц үг таарахгүй байна");
      return;
    }

    setLoading(true);
    try {
      const result = await changeUserPassword(oldPassword, newPassword);

      if (result.success) {
        showAlert("Амжилттай", "Нууц үг амжилттай солигдлоо", [
          {
            text: "OK",
            onPress: () => {
              setShowPasswordModal(false);
              setOldPassword("");
              setNewPassword("");
              setConfirmPassword("");
            },
          },
        ]);
      } else {
        showAlert("Алдаа", result.error || "Нууц үг солихоо алдаа гарлаа");
      }
    } catch (error: any) {
      showAlert("Алдаа", "Серверт холбогдох боломжгүй байна");
      console.error("Password change error:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleNotificationToggle = async (value: boolean) => {
    setNotifications(value);
    try {
      await AsyncStorage.setItem(
        "@notification_settings",
        JSON.stringify(value)
      );
      // Sync with backend
      await updateNotificationPreferences({ notifications_enabled: value });
      if (value) {
        // Re-register push token when enabling
        await initializePushNotifications();
      } else {
        // Unregister when disabling
        await unregisterPushTokenFromServer();
      }
    } catch (error: any) {
      console.error("Save notification settings error:", error);
    }
  };

  const handleSignalNotificationToggle = async (value: boolean) => {
    setSignalNotifications(value);
    try {
      await updateNotificationPreferences({ signal_notifications: value });
    } catch (error: any) {
      console.error("Save signal notification settings error:", error);
    }
  };

  const handleNewsNotificationToggle = async (value: boolean) => {
    setNewsNotifications(value);
    try {
      await updateNotificationPreferences({ news_notifications: value });
    } catch (error: any) {
      console.error("Save news notification settings error:", error);
    }
  };

  const handleNewsImpactFilterChange = async (filter: NewsImpactFilter) => {
    setNewsImpactFilter(filter);
    setShowImpactFilterModal(false);
    try {
      await updateNotificationPreferences({ news_impact_filter: filter });
    } catch (error: any) {
      console.error("Save news impact filter error:", error);
    }
  };

  const handleSecurityNotificationToggle = async (value: boolean) => {
    setSecurityNotifications(value);
    try {
      await updateNotificationPreferences({ security_notifications: value });
    } catch (error: any) {
      console.error("Save security notification settings error:", error);
    }
  };

  const handleSignalThresholdChange = async (threshold: number) => {
    setSignalThreshold(threshold);
    setShowSignalThresholdModal(false);
    try {
      await AsyncStorage.setItem("@signal_threshold", String(threshold));
      await updateNotificationPreferences({ signal_threshold: threshold });
    } catch (error: any) {
      console.error("Save signal threshold error:", error);
    }
  };

  const handleDarkModeToggle = async (_value: boolean) => {
    // Cycle through 'light' → 'dark' → 'auto'
    const newMode =
      themeMode === "light" ? "dark" : themeMode === "dark" ? "auto" : "light";
    setTheme(newMode);
  };

  const handleLogout = () => {
    showAlert("Гарах", "Та системээс гарахдаа итгэлтэй байна уу?", [
      {
        text: "Үгүй",
        style: "cancel",
      },
      {
        text: "Тийм",
        onPress: async () => {
          await logoutUser();
          navigation.reset({
            index: 0,
            routes: [{ name: "Login" }],
          });
        },
      },
    ]);
  };

  // Open document modal
  const openDocument = (docType: string) => {
    const documents = {
      help: {
        title: "Тусламж",
        content: `Форекс Сигнал Апп

Энэ апп нь хамгийн сүүлд хөгжүүлсэн GBDT ensemble машин сургалтын загварыг ашиглан форекс валютын ханшийн хөдөлгөөнийг таамагладаг.

Үндсэн функцүүд:
• ML сигналын үндсэн хос: EUR/USD
• 3 төрлийн сигнал (BUY, HOLD, SELL)
• Real-time магадлал
• Өдрийн тойм статистик

[!] Анхааруулга:
Энэ апп нь зөвхөн судалгааны зориулалттай бөгөөд санхүүгийн зөвлөгөө биш. Бүх арилжааны шийдвэр таны хувийн хариуцлага юм.

Холбоо барих:
• Email: support@predictrix.com
• GitHub: github.com/Asura-lab/Predictrix`,
      },
      terms: {
        title: "Үйлчилгээний нөхцөл",
        content: `ҮЙЛЧИЛГЭЭНИЙ НӨХЦӨЛ

1. ХҮЛЭЭН ЗӨВШӨӨРӨХ
Апп-г ашиглаж эхлэхдээ та эдгээр үйлчилгээний нөхцөлтэй бүрэн танилцаж, хүлээн зөвшөөрч байна.

2. ҮЙЛЧИЛГЭЭНИЙ ТОДОРХОЙЛОЛТ
• GBDT ensemble ашиглан форекс зах зээлийн чиг хандлагыг таамаглах
• Техникийн шинжилгээний мэдээлэл өгөх
• Валютын хосын мэдээлэл харуулах

[!] БИД САНАЛ, ЗӨВЛӨМЖ ӨГДӨГГҮЙ
Манай апп нь ээллийн зориулалттай бөгөөд санхүүгийн зөвлөгөө биш.

3. ЭРСДЭЛИЙН АНХААРУУЛГА
• Форекс зах зээл маш өндөр эрсдэлтэй
• Таамаглал 100% үнэн зөв байх баталгаагүй
• Өнгөрсөн үр дүн ирээдүйн амжилтыг батлахгүй
• Загвар нь зах зээлийн бүх хөдөлгөөнийг 100% баттай таамаглахгүй

4. ХЭРЭГЛЭГЧИЙН ХАРИУЦЛАГА
• Бүртгэлийн мэдээллээ үнэн зөв өгөх
• Нууц үгээ нууцлах
• Өөрийн хөрөнгө оруулалтын шийдвэр өөрөө гаргах

5. ХОРИОТОЙ ҮЙЛДЛҮҮД
- Системийг хакердах
- Бусад хэрэглэгчийн данс руу нэвтрэх
- Автоматжуулсан систем ашиглах
- Апп-ын код хуулах

6. ХАРИУЦЛАГЫН ХЯЗГААРЛАЛТ
Бид дараах зүйлд хариуцлага хүлээхгүй:
• Таны арилжааны алдагдал
• Апп-ын алдаа, техникийн саатал
• Мэдээллийн алдаа, хоцрогдол

Дэлгэрэнгүй: docs/TERMS_OF_SERVICE.md`,
      },
      privacy: {
        title: "Нууцлалын бодлого",
        content: `НУУЦЛАЛЫН БОДЛОГО

1. ЦУГЛУУЛАХ МЭДЭЭЛЭЛ

+ Бид цуглуулдаг:
• Нэр, имэйл хаяг
• Нууц үг (hash хэлбэрээр)
• Төхөөрөмжийн мэдээлэл
• Апп ашиглалтын статистик

- Бид цуглуулдаггүй:
• Санхүүгийн дансны мэдээлэл
• Кредит карт
• Арилжааны түүх
• Утасны дугаар
• GPS байршил

2. МЭДЭЭЛЭЛ АШИГЛАХ

Зориулалт:
• Үйлчилгээ үзүүлэх (нэвтрэх, профайл)
• Апп сайжруулах
• Хэрэглэгчтэй харилцах
• Аюулгүй байдал

3. ХАДГАЛАХ БАЙРШИЛ

• MongoDB Atlas (AWS, Ази-Номхон далай)
• Утас дээр encrypted (AsyncStorage)
• Backup: 30 хоног

4. ХАМГААЛАЛТ

Техникийн:
• HTTPS/TLS encryption
• bcrypt password hashing
• JWT токен (7 хоног)
• MongoDB Atlas Security
• Firewall protection

5. БИД ХУВААЛЦДАГГҮЙ

+ Бид таны мэдээллийг:
• БОРЛУУЛДАГГҮЙ
• ЗАРДАГГҮЙ
• МАРКЕТИНГ ХИЙДЭГГҮЙ

6. ТАНЫ ЭРХҮҮД

• Үзэх эрх - Апп → Профайл → "Миний мэдээлэл"
• Засах эрх - Апп → Профайл → "Мэдээлэл засах"
• Устгах эрх - Апп → Профайл → "Бүртгэл устгах"

[!] Устгасны дараа сэргээх боломжгүй!

7. ХОЛБОО БАРИХ

Email: privacy@predictrix.com
Email: support@predictrix.com

Дэлгэрэнгүй: docs/PRIVACY_POLICY.md`,
      },
      about: {
        title: "Апп-ын тухай",
        content: `PREDICTRIX
AI-Powered Forex Trading Signals

Хувилбар: 0.4.4
Шинэчилсэн: 2026.04.02
Платформ: Android / iOS (Expo SDK 51)

Зорилго:
Хамгийн сүүлд хөгжүүлсэн олон давхар GBDT ensemble машин сургалтын загварыг үндсэн загвар болгон ашиглаж, форекс валютын ханшийн хөдөлгөөнийг таамаглах, хэрэглэгчдэд бодит цагийн арилжааны дохио, техникийн шинжилгээг өгөх.

ML Загвар:
• Алгоритм: GBDT Ensemble (Gradient Boosted Decision Trees)
• Timeframe: М15, Н1, Н4 олон хэмжигдэхүүний нэгтгэл
• Итгэлцлийн үнэлгээ: Ensemble probability
• Хамрах хүрээ: 3 ангилал (SELL, HOLD, BUY)

Технологийн стек:
• Frontend: React Native + Expo SDK 51
• Backend: Python Flask + MongoDB Atlas
• ML: scikit-learn GBDT + joblib
• Auth: JWT + bcrypt
• Push: Expo Notifications

Дэмждэг валют хосууд:
• ML сигнал: EUR/USD
• Бусад хос: ханш/мэдээ/шинжилгээний мэдээлэл (аппын боломжит хэсгүүдээр)

Судалгааны ажил:
Энэ апп нь дипломын судалгааны ажлын хүрээнд хөгжүүлэгдсэн бөгөөд судалгааны зорилготой.

[!] Эрсдлийн анхааруулга:
Энэ нь санхүүгийн зөвлөгөө биш. Forex арилжаа маш өндөр эрсдэлтэй бөгөөд таны бүх хөрөнгийг алдах магадлалтай. Арилжаа хийхээс өмнө өөрийн санхүүгийн зөвлөхөөс зааж уу.

Хөгжүүлэгч:
GitHub: github.com/Asura-lab/Predictrix

Лиценз:
Судалгааны зориулалтаар үнэгүй ашиглаж болно.

© 2026 Predictrix`,
      },
    };

    setCurrentDocument(documents[docType as keyof typeof documents]);
    setShowDocumentModal(true);
  };

  // Open external link
  const openExternalLink = (url: string) => {
    showAlert(
      "Холбоос нээх",
      "Та вэб хөтөч дээр нээхдээ итгэлтэй байна уу?",
      [
        { text: "Үгүй", style: "cancel" },
        { text: "Тийм", onPress: () => Linking.openURL(url) },
      ]
    );
  };

  return (
    <View style={styles.container}>
      <StatusBar barStyle="light-content" backgroundColor="#0D1421" />
      
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerContent}>
          <View style={styles.avatarContainer}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{(userData?.name || "U").charAt(0).toUpperCase()}</Text>
            </View>
          </View>
          <Text style={styles.userName}>{userData?.name || "User"}</Text>
          <Text style={styles.userEmail}>{userData?.email || ""}</Text>
        </View>
      </View>

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        {/* Statistics Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>STATISTICS</Text>

          <View style={styles.statsContainer}>
            <View style={styles.statCard}>
              <Text style={styles.statValue}>{stats.daysUsed}</Text>
              <Text style={styles.statLabel}>Days Used</Text>
            </View>

            <View style={styles.statCard}>
              <Text style={styles.statValue}>{stats.signalsReceived}</Text>
              <Text style={styles.statLabel}>Signals</Text>
            </View>

            <View style={styles.statCard}>
              <Text style={styles.statValue}>
                {stats.lastActive || "Today"}
              </Text>
              <Text style={styles.statLabel}>Last Active</Text>
            </View>
          </View>
        </View>

        {/* Personal Information Section */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>PERSONAL INFO</Text>
            {!editMode && (
              <TouchableOpacity
                onPress={() => setEditMode(true)}
                style={styles.editButton}
              >
                <Text style={styles.editButtonText}>Edit</Text>
              </TouchableOpacity>
            )}
          </View>

          <View style={styles.infoCard}>
            <View style={styles.infoRow}>
              <View style={styles.infoContent}>
                <Text style={styles.infoLabel}>NAME</Text>
                {editMode ? (
                  <TextInput
                    style={styles.input}
                    value={name}
                    onChangeText={setName}
                    placeholder="Enter name"
                    placeholderTextColor={colors.placeholderText}
                  />
                ) : (
                  <Text style={styles.infoValue}>{name}</Text>
                )}
              </View>
            </View>

            <View style={styles.divider} />

            <View style={styles.infoRow}>
              <View style={styles.infoContent}>
                <Text style={styles.infoLabel}>EMAIL</Text>
                {editMode ? (
                  <TextInput
                    style={styles.input}
                    value={email}
                    onChangeText={setEmail}
                    placeholder="Enter email"
                    placeholderTextColor={colors.placeholderText}
                    keyboardType="email-address"
                  />
                ) : (
                  <Text style={styles.infoValue}>{email}</Text>
                )}
              </View>
            </View>

            {editMode && (
              <>
                <View style={styles.divider} />
                <View style={styles.buttonRow}>
                  <TouchableOpacity
                    style={[styles.actionButton, styles.cancelButton]}
                    onPress={() => {
                      setEditMode(false);
                      setName(userData?.name || "");
                      setEmail(userData?.email || "");
                    }}
                  >
                    <Text style={styles.cancelButtonText}>Cancel</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.actionButton, styles.saveButton]}
                    onPress={handleSave}
                    disabled={loading}
                  >
                    {loading ? (
                      <ActivityIndicator color="#FFFFFF" />
                    ) : (
                      <Text style={styles.saveButtonText}>Save</Text>
                    )}
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        </View>

        {/* Settings Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>SETTINGS</Text>

          <View style={styles.infoCard}>
            <View style={styles.infoRow}>
              <View style={styles.infoContent}>
                <Text style={styles.infoLabel}>NOTIFICATIONS</Text>
                <Text style={styles.infoDescription}>
                  Enable push notifications
                </Text>
              </View>
              <Switch
                value={notifications}
                onValueChange={handleNotificationToggle}
                trackColor={{ false: "#1E293B", true: "#00C853" }}
                thumbColor={notifications ? "#FFFFFF" : "#6B7280"}
              />
            </View>

            {notifications && (
              <>
                <View style={styles.divider} />

                <View style={styles.infoRow}>
                  <View style={styles.infoContent}>
                    <Text style={styles.infoLabel}>SIGNAL ALERTS</Text>
                    <Text style={styles.infoDescription}>
                      High-confidence trading signals
                    </Text>
                  </View>
                  <Switch
                    value={signalNotifications}
                    onValueChange={handleSignalNotificationToggle}
                    trackColor={{ false: "#1E293B", true: "#FFD700" }}
                    thumbColor={signalNotifications ? "#FFFFFF" : "#6B7280"}
                  />
                </View>

                {signalNotifications && (
                  <>
                    <View style={styles.divider} />
                    <TouchableOpacity
                      style={styles.infoRow}
                      onPress={() => setShowSignalThresholdModal(true)}
                    >
                      <View style={styles.infoContent}>
                        <Text style={styles.infoLabel}>SIGNAL THRESHOLD</Text>
                        <Text style={styles.infoDescription}>
                          Min confidence: {(signalThreshold * 100).toFixed(0)}%
                        </Text>
                      </View>
                      <ChevronRight size={16} color={colors.textSecondary} />
                    </TouchableOpacity>
                  </>
                )}

                <View style={styles.divider} />

                <View style={styles.infoRow}>
                  <View style={styles.infoContent}>
                    <Text style={styles.infoLabel}>NEWS ALERTS</Text>
                    <Text style={styles.infoDescription}>
                      Economic news events (10 min before)
                    </Text>
                  </View>
                  <Switch
                    value={newsNotifications}
                    onValueChange={handleNewsNotificationToggle}
                    trackColor={{ false: "#1E293B", true: "#FF5252" }}
                    thumbColor={newsNotifications ? "#FFFFFF" : "#6B7280"}
                  />
                </View>

                {newsNotifications && (
                  <>
                    <View style={styles.divider} />
                    <TouchableOpacity
                      style={styles.infoRow}
                      onPress={() => setShowImpactFilterModal(true)}
                    >
                      <View style={styles.infoContent}>
                        <Text style={styles.infoLabel}>NEWS IMPACT FILTER</Text>
                        <View style={styles.impactRow}>
                          <View style={[styles.impactDot, { backgroundColor: '#F44336' }]} />
                          {newsImpactFilter !== "high" && <View style={[styles.impactDot, { backgroundColor: '#FFC107' }]} />}
                          {newsImpactFilter === "all" && <View style={[styles.impactDot, { backgroundColor: '#4CAF50' }]} />}
                          <Text style={styles.infoDescription}>
                            {newsImpactFilter === "high" ? "High impact only" : newsImpactFilter === "medium" ? "High + Medium" : "All impact levels"}
                          </Text>
                        </View>
                      </View>
                      <ChevronRight size={16} color={colors.textSecondary} />
                    </TouchableOpacity>
                  </>
                )}

                <View style={styles.divider} />

                <View style={styles.infoRow}>
                  <View style={styles.infoContent}>
                    <Text style={styles.infoLabel}>SECURITY ALERTS</Text>
                    <Text style={styles.infoDescription}>
                      Login from new device
                    </Text>
                  </View>
                  <Switch
                    value={securityNotifications}
                    onValueChange={handleSecurityNotificationToggle}
                    trackColor={{ false: "#1E293B", true: "#2196F3" }}
                    thumbColor={securityNotifications ? "#FFFFFF" : "#6B7280"}
                  />
                </View>
              </>
            )}

            <View style={styles.divider} />

            <TouchableOpacity 
              style={styles.infoRow}
              onPress={() => setShowThemeModal(true)}
            >
              <View style={styles.infoContent}>
                <Text style={styles.infoLabel}>THEME</Text>
                <Text style={styles.infoDescription}>
                  {themeMode === "dark"
                    ? "Dark"
                    : themeMode === "light"
                    ? "Light"
                    : "System"}
                </Text>
              </View>
              <ChevronRight size={16} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Security Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>SECURITY</Text>

          <TouchableOpacity
            style={styles.menuItem}
            onPress={handleChangePassword}
          >
            <Text style={styles.menuItemText}>Change Password</Text>
            <ChevronRight size={16} color={colors.textSecondary} />
          </TouchableOpacity>
        </View>

        {/* About Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>ABOUT</Text>

          <TouchableOpacity
            style={styles.menuItem}
            onPress={() => openDocument("help")}
          >
            <Text style={styles.menuItemText}>Help</Text>
            <ChevronRight size={16} color={colors.textSecondary} />
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.menuItem}
            onPress={() => openDocument("terms")}
          >
            <Text style={styles.menuItemText}>Terms of Service</Text>
            <ChevronRight size={16} color={colors.textSecondary} />
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.menuItem}
            onPress={() => openDocument("privacy")}
          >
            <Text style={styles.menuItemText}>Privacy Policy</Text>
            <ChevronRight size={16} color={colors.textSecondary} />
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.menuItem}
            onPress={() => openDocument("about")}
          >
            <Text style={styles.menuItemText}>About App</Text>
            <ChevronRight size={16} color={colors.textSecondary} />
          </TouchableOpacity>

          <View style={styles.menuItem}>
            <Text style={styles.menuItemText}>Version</Text>
            <Text style={styles.versionText}>0.4.3</Text>
          </View>
        </View>

        {/* Logout Button */}
        <View style={styles.section}>
          <TouchableOpacity style={styles.logoutButton} onPress={handleLogout}>
            <Text style={styles.logoutButtonText}>Sign Out</Text>
          </TouchableOpacity>
        </View>

        <View style={{ height: 40 }} />
      </ScrollView>

      {/* Password Change Modal */}
      <Modal
        visible={showPasswordModal}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setShowPasswordModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Change Password</Text>
              <TouchableOpacity
                onPress={() => {
                  setShowPasswordModal(false);
                  setOldPassword("");
                  setNewPassword("");
                  setConfirmPassword("");
                }}
              >
                <Text style={styles.closeButton}>X</Text>
              </TouchableOpacity>
            </View>

            <View style={styles.modalBody}>
              <View style={styles.inputGroup}>
                <Text style={styles.inputLabel}>CURRENT PASSWORD</Text>
                <TextInput
                  style={styles.modalInput}
                  value={oldPassword}
                  onChangeText={setOldPassword}
                  secureTextEntry
                  placeholder="Enter current password"
                  placeholderTextColor={colors.placeholderText}
                  autoCapitalize="none"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.inputLabel}>NEW PASSWORD</Text>
                <TextInput
                  style={styles.modalInput}
                  value={newPassword}
                  onChangeText={setNewPassword}
                  secureTextEntry
                  placeholder="Min 6 characters"
                  placeholderTextColor={colors.placeholderText}
                  autoCapitalize="none"
                />
              </View>

              <View style={styles.inputGroup}>
                <Text style={styles.inputLabel}>CONFIRM PASSWORD</Text>
                <TextInput
                  style={styles.modalInput}
                  value={confirmPassword}
                  onChangeText={setConfirmPassword}
                  secureTextEntry
                  placeholder="Confirm new password"
                  placeholderTextColor={colors.placeholderText}
                  autoCapitalize="none"
                />
              </View>

              <TouchableOpacity
                style={styles.modalButton}
                onPress={handlePasswordChange}
                disabled={loading}
              >
                {loading ? (
                  <ActivityIndicator color="#FFFFFF" />
                ) : (
                  <Text style={styles.modalButtonText}>Update Password</Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Document Modal */}
      <Modal
        animationType="slide"
        transparent={true}
        visible={showDocumentModal}
        onRequestClose={() => setShowDocumentModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.documentModalContainer}>
            <View style={styles.documentModalHeader}>
              <Text style={styles.documentModalTitle}>
                {currentDocument?.title}
              </Text>
              <TouchableOpacity
                onPress={() => setShowDocumentModal(false)}
                style={styles.documentCloseButton}
              >
                <Text style={styles.closeButton}>X</Text>
              </TouchableOpacity>
            </View>

            <ScrollView
              style={styles.documentContent}
              showsVerticalScrollIndicator={false}
            >
              <Text style={styles.documentText}>
                {currentDocument?.content}
              </Text>
            </ScrollView>

            <View style={styles.documentModalFooter}>
              <TouchableOpacity
                style={styles.documentButton}
                onPress={() => setShowDocumentModal(false)}
              >
                <Text style={styles.documentButtonText}>Close</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Theme Selection Modal */}
      <Modal
        animationType="fade"
        transparent={true}
        visible={showThemeModal}
        onRequestClose={() => setShowThemeModal(false)}
      >
        <TouchableOpacity 
          style={styles.themeModalOverlay}
          activeOpacity={1}
          onPress={() => setShowThemeModal(false)}
        >
          <View style={styles.themeModalContent}>
            <Text style={styles.themeModalTitle}>Select Theme</Text>
            
            <TouchableOpacity
              style={[
                styles.themeOption,
                themeMode === 'auto' && styles.themeOptionActive
              ]}
              onPress={() => {
                setTheme('auto');
                setShowThemeModal(false);
              }}
            >
              <Text style={styles.themeOptionText}>System</Text>
              {themeMode === 'auto' && <Check size={16} color={colors.success} />}
            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.themeOption,
                themeMode === 'light' && styles.themeOptionActive
              ]}
              onPress={() => {
                setTheme('light');
                setShowThemeModal(false);
              }}
            >
              <Text style={styles.themeOptionText}>Light</Text>
              {themeMode === 'light' && <Check size={16} color={colors.success} />}
            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.themeOption,
                styles.themeOptionLast,
                themeMode === 'dark' && styles.themeOptionActive
              ]}
              onPress={() => {
                setTheme('dark');
                setShowThemeModal(false);
              }}
            >
              <Text style={styles.themeOptionText}>Dark</Text>
              {themeMode === 'dark' && <Check size={16} color={colors.success} />}
            </TouchableOpacity>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* News Impact Filter Modal */}
      <Modal
        animationType="fade"
        transparent={true}
        visible={showImpactFilterModal}
        onRequestClose={() => setShowImpactFilterModal(false)}
      >
        <TouchableOpacity 
          style={styles.themeModalOverlay}
          activeOpacity={1}
          onPress={() => setShowImpactFilterModal(false)}
        >
          <View style={styles.themeModalContent}>
            <Text style={styles.themeModalTitle}>News Impact Filter</Text>
            
            <TouchableOpacity
              style={[
                styles.themeOption,
                newsImpactFilter === 'high' && styles.themeOptionActive
              ]}
              onPress={() => handleNewsImpactFilterChange('high')}
            >
              <View style={styles.impactRow}>
                <View style={[styles.impactDot, { backgroundColor: '#F44336' }]} />
                <Text style={styles.themeOptionText}>High Impact Only</Text>
              </View>
              {newsImpactFilter === 'high' && <Check size={16} color={colors.success} />}
            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.themeOption,
                newsImpactFilter === 'medium' && styles.themeOptionActive
              ]}
              onPress={() => handleNewsImpactFilterChange('medium')}
            >
              <View style={styles.impactRow}>
                <View style={[styles.impactDot, { backgroundColor: '#F44336' }]} />
                <View style={[styles.impactDot, { backgroundColor: '#FFC107' }]} />
                <Text style={styles.themeOptionText}>High + Medium</Text>
              </View>
              {newsImpactFilter === 'medium' && <Check size={16} color={colors.success} />}
            </TouchableOpacity>

            <TouchableOpacity
              style={[
                styles.themeOption,
                styles.themeOptionLast,
                newsImpactFilter === 'all' && styles.themeOptionActive
              ]}
              onPress={() => handleNewsImpactFilterChange('all')}
            >
              <View style={styles.impactRow}>
                <View style={[styles.impactDot, { backgroundColor: '#F44336' }]} />
                <View style={[styles.impactDot, { backgroundColor: '#FFC107' }]} />
                <View style={[styles.impactDot, { backgroundColor: '#4CAF50' }]} />
                <Text style={styles.themeOptionText}>All Levels</Text>
              </View>
              {newsImpactFilter === 'all' && <Check size={16} color={colors.success} />}
            </TouchableOpacity>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* Signal Threshold Modal */}
      <Modal
        animationType="fade"
        transparent={true}
        visible={showSignalThresholdModal}
        onRequestClose={() => setShowSignalThresholdModal(false)}
      >
        <TouchableOpacity 
          style={styles.themeModalOverlay}
          activeOpacity={1}
          onPress={() => setShowSignalThresholdModal(false)}
        >
          <View style={styles.themeModalContent}>
            <Text style={styles.themeModalTitle}>Signal Confidence Threshold</Text>
            
            {[0.90, 0.92, 0.94, 0.96, 0.98, 1.0].map((val) => (
              <TouchableOpacity
                key={val}
                style={[
                  styles.themeOption,
                  signalThreshold === val && styles.themeOptionActive,
                  val === 1.0 && styles.themeOptionLast,
                ]}
                onPress={() => handleSignalThresholdChange(val)}
              >
                <Text style={styles.themeOptionText}>{(val * 100).toFixed(0)}%{val === 0.90 ? ' (Default)' : val === 1.0 ? ' (Өндөр)' : ''}</Text>
                {signalThreshold === val && <Check size={16} color={colors.success} />}
              </TouchableOpacity>
            ))}
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
};

const createStyles = (colors: any) =>
  StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    header: {
      paddingTop: 60,
      paddingBottom: 30,
      paddingHorizontal: 20,
      alignItems: "center",
      backgroundColor: colors.card,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    headerContent: {
      alignItems: "center",
    },
    avatarContainer: {
      marginBottom: 16,
    },
    avatar: {
      width: 80,
      height: 80,
      borderRadius: 40,
      backgroundColor: colors.success,
      justifyContent: "center",
      alignItems: "center",
    },
    avatarText: {
      fontSize: 32,
      fontWeight: "700",
      color: "#FFFFFF",
    },
    userName: {
      fontSize: 20,
      fontWeight: "700",
      color: colors.textPrimary,
      marginBottom: 4,
    },
    userEmail: {
      fontSize: 13,
      color: colors.textSecondary,
    },
    content: {
      flex: 1,
    },
    section: {
      marginTop: 24,
      paddingHorizontal: 16,
    },
    statsContainer: {
      flexDirection: "row",
      justifyContent: "space-between",
      gap: 12,
    },
    statCard: {
      flex: 1,
      backgroundColor: colors.card,
      borderRadius: 8,
      padding: 16,
      alignItems: "center",
      borderWidth: 1,
      borderColor: colors.border,
    },
    statValue: {
      fontSize: 18,
      fontWeight: "700",
      color: colors.textPrimary,
      marginBottom: 4,
    },
    statLabel: {
      fontSize: 10,
      color: colors.textSecondary,
      textAlign: "center",
      letterSpacing: 1,
    },
    sectionHeader: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      marginBottom: 12,
    },
    sectionTitle: {
      fontSize: 12,
      fontWeight: "600",
      color: colors.textSecondary,
      marginBottom: 12,
      letterSpacing: 1,
    },
    editButton: {
      paddingHorizontal: 12,
      paddingVertical: 6,
    },
    editButtonText: {
      color: colors.success,
      fontSize: 13,
      fontWeight: "600",
    },
    infoCard: {
      backgroundColor: colors.card,
      borderRadius: 8,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    infoRow: {
      flexDirection: "row",
      alignItems: "center",
      paddingVertical: 12,
    },
    infoContent: {
      flex: 1,
    },
    infoLabel: {
      fontSize: 10,
      color: colors.textSecondary,
      marginBottom: 4,
      letterSpacing: 1,
    },
    infoValue: {
      fontSize: 15,
      color: colors.textPrimary,
      fontWeight: "500",
    },
    infoDescription: {
      fontSize: 12,
      color: colors.textSecondary,
      marginTop: 2,
    },
    input: {
      fontSize: 15,
      color: colors.textPrimary,
      fontWeight: "500",
      borderBottomWidth: 1,
      borderBottomColor: colors.success,
      paddingVertical: 4,
    },
    divider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 8,
    },
    buttonRow: {
      flexDirection: "row",
      justifyContent: "space-between",
      marginTop: 16,
      gap: 12,
    },
    actionButton: {
      flex: 1,
      paddingVertical: 12,
      borderRadius: 8,
      alignItems: "center",
    },
    cancelButton: {
      backgroundColor: colors.cardSecondary,
    },
    cancelButtonText: {
      color: colors.textSecondary,
      fontSize: 14,
      fontWeight: "600",
    },
    saveButton: {
      backgroundColor: colors.success,
    },
    saveButtonText: {
      color: "#FFFFFF",
      fontSize: 14,
      fontWeight: "600",
    },
    menuItem: {
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "space-between",
      backgroundColor: colors.card,
      padding: 16,
      borderRadius: 8,
      marginBottom: 8,
      borderWidth: 1,
      borderColor: colors.border,
    },
    menuItemText: {
      fontSize: 14,
      color: colors.textPrimary,
      fontWeight: "500",
    },
    chevron: {
      color: colors.textSecondary,
      fontSize: 16,
    },
    versionText: {
      fontSize: 13,
      color: colors.textSecondary,
    },
    closeButton: {
      fontSize: 20,
      color: colors.textSecondary,
      fontWeight: "600",
    },
    logoutButton: {
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: colors.card,
      padding: 16,
      borderRadius: 8,
      borderWidth: 1,
      borderColor: colors.error,
    },
    logoutButtonText: {
      fontSize: 14,
      color: colors.error,
      fontWeight: "600",
    },
    modalOverlay: {
      flex: 1,
      backgroundColor: colors.overlay,
      justifyContent: "flex-end",
    },
    modalContent: {
      backgroundColor: colors.card,
      borderTopLeftRadius: 16,
      borderTopRightRadius: 16,
      paddingBottom: 40,
    },
    modalHeader: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      padding: 20,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    modalTitle: {
      fontSize: 18,
      fontWeight: "700",
      color: colors.textPrimary,
    },
    modalBody: {
      padding: 20,
    },
    inputGroup: {
      marginBottom: 20,
    },
    inputLabel: {
      fontSize: 10,
      fontWeight: "600",
      color: colors.textSecondary,
      marginBottom: 8,
      letterSpacing: 1,
    },
    modalInput: {
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 8,
      padding: 12,
      fontSize: 15,
      color: colors.textPrimary,
      backgroundColor: colors.background,
    },
    modalButton: {
      backgroundColor: colors.success,
      borderRadius: 8,
      padding: 16,
      alignItems: "center",
      marginTop: 10,
    },
    modalButtonText: {
      color: "#FFFFFF",
      fontSize: 14,
      fontWeight: "600",
    },
    // Document Modal Styles
    documentModalContainer: {
      backgroundColor: colors.card,
      borderTopLeftRadius: 16,
      borderTopRightRadius: 16,
      height: "85%",
      paddingBottom: 0,
    },
    documentModalHeader: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      padding: 20,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    documentModalTitle: {
      fontSize: 18,
      fontWeight: "700",
      color: colors.textPrimary,
      flex: 1,
    },
    documentCloseButton: {
      padding: 4,
    },
    documentContent: {
      flex: 1,
      padding: 20,
    },
    documentText: {
      fontSize: 14,
      lineHeight: 22,
      color: colors.textPrimary,
    },
    documentModalFooter: {
      padding: 20,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    documentButton: {
      backgroundColor: colors.success,
      borderRadius: 8,
      padding: 16,
      alignItems: "center",
    },
    documentButtonText: {
      color: "#FFFFFF",
      fontSize: 14,
      fontWeight: "600",
    },
    themeButton: {
      backgroundColor: colors.success,
      paddingHorizontal: 16,
      paddingVertical: 8,
      borderRadius: 8,
    },
    themeButtonText: {
      color: "#FFFFFF",
      fontSize: 12,
      fontWeight: "600",
    },
    // Theme Modal Styles
    themeModalOverlay: {
      flex: 1,
      backgroundColor: colors.overlay,
      justifyContent: 'center',
      alignItems: 'center',
    },
    themeModalContent: {
      backgroundColor: colors.card,
      borderRadius: 12,
      width: '80%',
      paddingVertical: 8,
      borderWidth: 1,
      borderColor: colors.border,
    },
    themeModalTitle: {
      fontSize: 16,
      fontWeight: '700',
      color: colors.textPrimary,
      paddingHorizontal: 20,
      paddingVertical: 16,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    themeOption: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingHorizontal: 20,
      paddingVertical: 16,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    themeOptionLast: {
      borderBottomWidth: 0,
    },
    themeOptionActive: {
      backgroundColor: colors.success + '20',
    },
    themeOptionText: {
      fontSize: 15,
      color: colors.textPrimary,
    },
    impactRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
    },
    impactDot: {
      width: 10,
      height: 10,
      borderRadius: 5,
    },
  });

export default ProfileScreen;
