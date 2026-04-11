import React, { useState, useCallback } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  ActivityIndicator,
  TouchableOpacity,
  StatusBar,
  Modal,
} from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useFocusEffect } from "@react-navigation/native";
import { useTheme } from "../context/ThemeContext";
import { getColors } from "../config/theme";
import { UI_COPY } from "../config/copy";
import { useQuery } from "@tanstack/react-query";
import { getLatestSignal, getRecentSignals } from "../services/api";
import { updateNotificationPreferences } from "../services/notificationService";
import { RefreshCw, SlidersHorizontal, Check } from "lucide-react-native";

const THRESHOLD_OPTIONS = [0.90, 0.92, 0.94, 0.96, 0.98, 1.0];
const THRESHOLD_KEY = "@signal_threshold";

const PAIR = "EUR/USD";
const DIGITS = 5;

const PredictionScreen = () => {
  const { isDark } = useTheme();
  const colors = getColors(isDark);
  const styles = createStyles(colors);

  const [signalThreshold, setSignalThreshold] = useState<number>(0.9);
  const [showThresholdModal, setShowThresholdModal] = useState(false);

  useFocusEffect(
    useCallback(() => {
      AsyncStorage.getItem(THRESHOLD_KEY).then((val) => {
        if (val !== null) setSignalThreshold(parseFloat(val));
      });
    }, [])
  );

  const handleThresholdChange = async (val: number) => {
    setSignalThreshold(val);
    setShowThresholdModal(false);
    await AsyncStorage.setItem(THRESHOLD_KEY, String(val));
    try {
      await updateNotificationPreferences({ signal_threshold: val });
    } catch {}
  };

  const {
    data: live,
    isLoading: loadingLive,
    refetch: refetchLive,
    isRefetching: isRefetchingLive,
  } = useQuery({
    queryKey: ["livePrediction", PAIR, signalThreshold],
    queryFn: async () => {
      const r = await getLatestSignal(PAIR, signalThreshold * 100);
      if (!r.success) throw new Error(r.error);
      return r.data?.signal ?? null;
    },
    staleTime: 60000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 60000,
    retry: 2,
  });

  const {
    data: recentData,
    isLoading: loadingRecent,
    refetch: refetchRecent,
  } = useQuery({
    queryKey: ["recentSignals", PAIR, signalThreshold],
    queryFn: async () => {
      const r = await getRecentSignals(PAIR, 5, signalThreshold * 100);
      if (r.success && r.data?.signals) return r.data.signals as any[];
      return [];
    },
    staleTime: 60000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: 60000,
  });

  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = useCallback(() => {
    setRefreshing(true);
    Promise.all([refetchLive(), refetchRecent()]).finally(() => setRefreshing(false));
  }, [refetchLive, refetchRecent]);

  const getSignalColor = (type: string) => {
    if (type === "BUY") return colors.success;
    if (type === "SELL") return colors.error;
    return colors.warning;
  };

  const formatTime = (iso: string) => {
    try {
      return new Date(iso).toLocaleString("mn-MN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  const lastUpdateTime = live
    ? new Date().toLocaleTimeString("en-US", { hour12: false })
    : "";

  const provenance = live?.model_provenance || {};
  const provenanceModelVersion = provenance.model_version || live?.model_version || "—";
  const provenanceRunId = provenance.run_id || "—";
  const provenanceTrainedAt = provenance.trained_at_utc || "";
  const signalMetaLabel = live?.human_oversight_required
    ? UI_COPY.signal.humanOversightRequired
    : UI_COPY.signal.humanOversightOptional;

  return (
    <View style={styles.container}>
      <StatusBar
        barStyle={isDark ? "light-content" : "dark-content"}
        backgroundColor={colors.background}
      />

      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>ТААМАГЛАЛ</Text>
          <Text style={styles.headerSub}>EUR/USD · 4 цагийн таамаглал</Text>
        </View>
        <TouchableOpacity
          style={styles.thresholdBtn}
          onPress={() => setShowThresholdModal(true)}
        >
          <SlidersHorizontal size={15} color={colors.primary} />
          <Text style={styles.thresholdBtnText}>
            {(signalThreshold * 100).toFixed(0)}%
          </Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.primary}
          />
        }
      >
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionLabel}>ОДООГИЙН ТААМАГЛАЛ</Text>
          <View style={styles.updateRow}>
            {isRefetchingLive && (
              <ActivityIndicator size="small" color={colors.primary} style={{ marginRight: 6 }} />
            )}
            {lastUpdateTime ? <Text style={styles.updateTime}>{lastUpdateTime}</Text> : null}
          </View>
        </View>

        {loadingLive ? (
          <View style={styles.loadingBox}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.loadingText}>AI загвар шинжилж байна...</Text>
          </View>
        ) : live ? (
          <>
            <View style={styles.provenanceCard}>
              <Text style={styles.provenanceTitle}>MODEL PROVENANCE</Text>
              <View style={styles.provenanceRow}>
                <Text style={styles.provenanceLabel}>Model</Text>
                <Text style={styles.provenanceValue}>{provenanceModelVersion}</Text>
              </View>
              <View style={styles.provenanceRow}>
                <Text style={styles.provenanceLabel}>Run ID</Text>
                <Text style={styles.provenanceValue} numberOfLines={1}>{provenanceRunId}</Text>
              </View>
              {provenanceTrainedAt ? (
                <View style={styles.provenanceRow}>
                  <Text style={styles.provenanceLabel}>Trained</Text>
                  <Text style={styles.provenanceValue}>{formatTime(provenanceTrainedAt)}</Text>
                </View>
              ) : null}
            </View>

          {live.signal === "HOLD" ? (
            // HOLD — show directional lean if available
            <View style={styles.card}>
              <View style={styles.holdIconRow}>
                <View style={[styles.holdDot, { backgroundColor: colors.warning }]} />
                <Text style={[styles.holdTitle, { color: colors.warning }]}>Зах зээл одоо хөдөлгөөн бага байна</Text>
              </View>
              {live.directional_signal && live.confidence > 0 ? (
                <View style={styles.trendHintRow}>
                  <View style={[styles.trendHintBadge, {
                    backgroundColor: live.directional_signal === "BUY" ? colors.success + "20" : colors.error + "20",
                    borderColor: live.directional_signal === "BUY" ? colors.success + "50" : colors.error + "50",
                  }]}>
                    <Text style={[styles.trendHintDir, {
                      color: live.directional_signal === "BUY" ? colors.success : colors.error,
                    }]}>
                      {live.directional_signal === "BUY" ? "↑ Өсөх" : "↓ Буурах"} хандлага
                    </Text>
                    <Text style={[styles.trendHintConf, {
                      color: live.directional_signal === "BUY" ? colors.success : colors.error,
                    }]}>
                      {live.confidence?.toFixed(1)}%
                    </Text>
                  </View>
                  <Text style={styles.holdSubText}>
                    Хангалттай өндөр итгэлтэй арилжааны сигнал биш.
                  </Text>
                </View>
              ) : (
                <Text style={styles.holdSubText}>
                  AI загварын шинжилгээгээр тодорхой арилжааны дохио илрэхгүй байна. Хүлээж байгаарай.
                </Text>
              )}
            </View>
          ) : (live.confidence ?? 0) >= signalThreshold * 100 ? (
            // High confidence BUY/SELL — full card
            <View style={styles.card}>
              <View style={styles.signalTop}>
                <View style={[styles.signalBadge, { backgroundColor: getSignalColor(live.signal) }]}>
                  <Text style={styles.signalBadgeText}>
                    {live.signal === "BUY" ? "BUY ↑" : "SELL ↓"}
                  </Text>
                </View>
                <Text style={[styles.confBig, { color: getSignalColor(live.signal) }]}>
                  {live.confidence?.toFixed(1)}%
                </Text>
              </View>
              <View style={styles.progressBg}>
                <View style={[styles.progressFill, {
                  width: `${Math.min(live.confidence ?? 0, 100)}%`,
                  backgroundColor: getSignalColor(live.signal),
                }]} />
              </View>
              <Text style={styles.confLabel}>Итгэлцүүр</Text>
              <View style={styles.grid}>
                <View style={styles.gridItem}>
                  <Text style={styles.gridLabel}>ОРОЛТ</Text>
                  <Text style={styles.gridValue}>{live.entry_price?.toFixed(DIGITS)}</Text>
                </View>
                <View style={[styles.gridItem, styles.slItem]}>
                  <Text style={[styles.gridLabel, { color: colors.error }]}>STOP LOSS</Text>
                  <Text style={[styles.gridValue, { color: colors.error }]}>{live.stop_loss?.toFixed(DIGITS)}</Text>
                  <Text style={styles.pipsText}>-{live.sl_pips} pips</Text>
                </View>
                <View style={[styles.gridItem, styles.tpItem]}>
                  <Text style={[styles.gridLabel, { color: colors.success }]}>TAKE PROFIT</Text>
                  <Text style={[styles.gridValue, { color: colors.success }]}>{live.take_profit?.toFixed(DIGITS)}</Text>
                  <Text style={styles.pipsText}>+{live.tp_pips} pips</Text>
                </View>
                <View style={styles.gridItem}>
                  <Text style={styles.gridLabel}>ЭРСДЭЛ/ӨГӨӨЖ</Text>
                  <Text style={[styles.gridValue, { color: colors.success }]}>{live.risk_reward}</Text>
                </View>
              </View>
            </View>
          ) : (
            // Low confidence BUY/SELL — trend hint only
            <View style={styles.card}>
              <View style={styles.signalTop}>
                <View style={[styles.signalBadge, { backgroundColor: getSignalColor(live.signal) + "99" }]}>
                  <Text style={styles.signalBadgeText}>
                    {live.signal === "BUY" ? "BUY ↑" : "SELL ↓"}
                  </Text>
                </View>
                <Text style={[styles.confBig, { color: getSignalColor(live.signal), fontSize: 26 }]}>
                  {live.confidence?.toFixed(1)}%
                </Text>
              </View>
              <View style={styles.progressBg}>
                <View style={[styles.progressFill, {
                  width: `${Math.min(live.confidence ?? 0, 100)}%`,
                  backgroundColor: getSignalColor(live.signal),
                }]} />
              </View>
              <Text style={styles.confLabel}>Итгэлцүүр</Text>
              <View style={styles.trendBox}>
                <Text style={styles.trendText}>
                  {live.signal === "BUY"
                    ? "Зах зээл өсөх хандлагатай байна."
                    : "Зах зээл буурах хандлагатай байна."}{" "}
                  Гэхдээ хангалттай өндөр итгэлтэй арилжааны сигнал биш.
                </Text>
              </View>
            </View>
          )}
            
            <View style={styles.raiCard}>
              <Text style={styles.raiHeader}>RESPONSIBLE AI</Text>
              <View style={styles.raiRow}>
                <Text style={styles.raiLabel}>{UI_COPY.signal.uncertaintyLabel}</Text>
                <Text style={styles.raiValue}>{String(live?.uncertainty_level || "UNKNOWN").toUpperCase()}</Text>
              </View>
              <View style={styles.raiDivider} />
              <View style={styles.raiRow}>
                <Text style={styles.raiLabel}>{UI_COPY.signal.actionabilityLabel}</Text>
                <Text style={styles.raiValue}>{String(live?.actionability || "review_then_execute").replace(/_/g, " ")}</Text>
              </View>
              <View style={styles.raiDivider} />
              <View style={styles.raiRow}>
                <Text style={styles.raiLabel}>{UI_COPY.signal.humanOversightLabel}</Text>
                <Text style={styles.raiValue}>{signalMetaLabel}</Text>
              </View>
              {!!live?.oversight_note && <Text style={styles.raiNote}>{live.oversight_note}</Text>}
            </View>
          </>
        ) : (
          <View style={styles.emptyBox}>
            <Text style={styles.emptyText}>Таамаглал авах боломжгүй байна</Text>
            <TouchableOpacity style={styles.retryBtn} onPress={() => refetchLive()}>
              <RefreshCw size={14} color="#fff" style={{ marginRight: 6 }} />
              <Text style={styles.retryText}>Дахин оролдох</Text>
            </TouchableOpacity>
          </View>
        )}

        <View style={[styles.sectionHeader, { marginTop: 8 }]}>
          <Text style={styles.sectionLabel}>ӨНДӨР ИТГЭЛТЭЙ ДОХИОНУУД</Text>
        </View>

        {loadingRecent ? (
          <View style={styles.loadingBox}>
            <ActivityIndicator color={colors.primary} />
          </View>
        ) : recentData && recentData.length > 0 ? (
          recentData.map((sig: any, idx: number) => (
            <View key={sig._id || idx} style={[styles.card, { marginBottom: 10 }]}>
              <View style={styles.signalTop}>
                <View style={[styles.signalBadge, { backgroundColor: getSignalColor(sig.signal) }]}>
                  <Text style={styles.signalBadgeText}>
                    {sig.signal === "BUY" ? "BUY ↑" : "SELL ↓"}
                  </Text>
                </View>
                <View style={{ alignItems: "flex-end" }}>
                  <Text style={[styles.confBig, { color: getSignalColor(sig.signal), fontSize: 22 }]}>
                    {sig.confidence?.toFixed(1)}%
                  </Text>
                  {sig.created_at && (
                    <Text style={styles.savedAt}>{formatTime(sig.created_at)}</Text>
                  )}
                </View>
              </View>
              <View style={styles.grid}>
                <View style={styles.gridItem}>
                  <Text style={styles.gridLabel}>ОРОЛТ</Text>
                  <Text style={styles.gridValue}>{sig.entry_price?.toFixed(DIGITS) ?? "—"}</Text>
                </View>
                <View style={[styles.gridItem, styles.slItem]}>
                  <Text style={[styles.gridLabel, { color: colors.error }]}>STOP LOSS</Text>
                  <Text style={[styles.gridValue, { color: colors.error }]}>{sig.stop_loss?.toFixed(DIGITS) ?? "—"}</Text>
                  {sig.sl_pips != null && <Text style={styles.pipsText}>-{sig.sl_pips} pips</Text>}
                </View>
                <View style={[styles.gridItem, styles.tpItem]}>
                  <Text style={[styles.gridLabel, { color: colors.success }]}>TAKE PROFIT</Text>
                  <Text style={[styles.gridValue, { color: colors.success }]}>{sig.take_profit?.toFixed(DIGITS) ?? "—"}</Text>
                  {sig.tp_pips != null && <Text style={styles.pipsText}>+{sig.tp_pips} pips</Text>}
                </View>
                <View style={styles.gridItem}>
                  <Text style={styles.gridLabel}>ЭРСДЭЛ/ӨГӨӨЖ</Text>
                  <Text style={[styles.gridValue, { color: colors.success }]}>{sig.risk_reward ?? "—"}</Text>
                </View>
              </View>
            </View>
          ))
        ) : (
          <View style={styles.emptyBox}>
            <Text style={styles.emptyText}>
              {(signalThreshold * 100).toFixed(0)}%-аас дээш итгэлтэй дохио одоогоор байхгүй байна
            </Text>
          </View>
        )}

        <View style={styles.disclaimerBox}>
          <Text style={styles.disclaimerBadge}>АНХААРУУЛГА</Text>
          <Text style={styles.disclaimer}>
            Зөвхөн судалгааны зорилготой. Санхүүгийн зөвлөгөө биш!
          </Text>
        </View>
      </ScrollView>

      {/* Threshold Modal */}
      <Modal
        animationType="fade"
        transparent={true}
        visible={showThresholdModal}
        onRequestClose={() => setShowThresholdModal(false)}
      >
        <TouchableOpacity
          style={styles.modalOverlay}
          activeOpacity={1}
          onPress={() => setShowThresholdModal(false)}
        >
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Хамгийн доод итгэлцүүр</Text>
            {THRESHOLD_OPTIONS.map((val, idx) => (
              <TouchableOpacity
                key={val}
                style={[
                  styles.modalOption,
                  signalThreshold === val && styles.modalOptionActive,
                  idx === THRESHOLD_OPTIONS.length - 1 && styles.modalOptionLast,
                ]}
                onPress={() => handleThresholdChange(val)}
              >
                <Text style={[
                  styles.modalOptionText,
                  signalThreshold === val && { color: colors.primary, fontWeight: "700" },
                ]}>
                  {(val * 100).toFixed(0)}%
                  {val === 0.90 ? "  (Default)" : val === 1.0 ? "  (Өндөр)" : ""}
                </Text>
                {signalThreshold === val && <Check size={16} color={colors.primary} />}
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
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      paddingTop: 52,
      paddingBottom: 16,
      paddingHorizontal: 20,
      backgroundColor: colors.card,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    headerTitle: { fontSize: 20, fontWeight: "800", color: colors.textPrimary, letterSpacing: 1 },
    headerSub: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
    thresholdBtn: {
      flexDirection: "row",
      alignItems: "center",
      gap: 6,
      backgroundColor: colors.primary + "18",
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderRadius: 20,
      borderWidth: 1,
      borderColor: colors.primary + "40",
    },
    thresholdBtnText: { fontSize: 13, fontWeight: "700", color: colors.primary },
    modalOverlay: {
      flex: 1,
      backgroundColor: "rgba(0,0,0,0.5)",
      justifyContent: "center",
      alignItems: "center",
    },
    modalContent: {
      backgroundColor: colors.card,
      borderRadius: 20,
      paddingVertical: 8,
      paddingHorizontal: 4,
      width: "82%",
      shadowColor: "#000",
      shadowOpacity: 0.3,
      shadowRadius: 20,
      elevation: 10,
    },
    modalTitle: {
      fontSize: 15,
      fontWeight: "700",
      color: colors.textPrimary,
      textAlign: "center",
      paddingVertical: 16,
      paddingHorizontal: 16,
    },
    modalSubtitle: {
      fontSize: 11,
      color: colors.textSecondary,
      textAlign: "center",
      marginTop: -10,
      marginBottom: 8,
      paddingHorizontal: 16,
      lineHeight: 16,
    },
    modalOption: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      paddingVertical: 14,
      paddingHorizontal: 20,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    modalOptionActive: { backgroundColor: colors.primary + "10" },
    modalOptionLast: { borderBottomWidth: 0 },
    modalOptionText: { fontSize: 14, color: colors.textPrimary },
    scrollContent: { padding: 16, paddingBottom: 40 },
    sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 10 },
    sectionLabel: { fontSize: 11, fontWeight: "700", color: colors.textSecondary, letterSpacing: 1 },
    updateRow: { flexDirection: "row", alignItems: "center" },
    updateTime: { fontSize: 11, color: colors.textSecondary },
    loadingBox: { alignItems: "center", paddingVertical: 40 },
    loadingText: { color: colors.textSecondary, fontSize: 13, marginTop: 12 },
    card: { backgroundColor: colors.card, borderRadius: 16, padding: 20, marginBottom: 16 },
    provenanceCard: { backgroundColor: colors.card, borderRadius: 16, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: colors.border },
    provenanceTitle: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.textSecondary, marginBottom: 10 },
    provenanceRow: { flexDirection: "row", justifyContent: "space-between", gap: 12, marginBottom: 8 },
    provenanceLabel: { fontSize: 12, color: colors.textSecondary, fontWeight: "600" },
    provenanceValue: { fontSize: 12, color: colors.textPrimary, fontWeight: "700", flexShrink: 1, textAlign: "right" },
    raiCard: { backgroundColor: colors.warning + "14", borderRadius: 16, padding: 16, marginBottom: 16, borderWidth: 1, borderColor: colors.warning + "40" },
    raiHeader: { fontSize: 11, fontWeight: "800", letterSpacing: 1, color: colors.warning, marginBottom: 10 },
    raiRow: { flexDirection: "row", justifyContent: "space-between", gap: 12, marginBottom: 8 },
    raiLabel: { fontSize: 12, color: colors.textSecondary, fontWeight: "600" },
    raiValue: { fontSize: 12, color: colors.textPrimary, fontWeight: "700", flexShrink: 1, textAlign: "right" },
    raiDivider: { height: 1, backgroundColor: colors.warning + "30", marginBottom: 8 },
    raiNote: { fontSize: 12, color: colors.textPrimary, lineHeight: 18, textAlign: "justify", fontWeight: "600" },
    signalTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 16 },
    signalBadge: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 25 },
    signalBadgeText: { color: "#fff", fontWeight: "800", fontSize: 16 },
    confBig: { fontSize: 32, fontWeight: "800" },
    progressBg: { height: 8, backgroundColor: colors.border, borderRadius: 4, overflow: "hidden", marginBottom: 4 },
    progressFill: { height: "100%", borderRadius: 4 },
    confLabel: { fontSize: 11, color: colors.textSecondary, marginBottom: 16 },
    grid: { gap: 10, marginTop: 8 },
    gridItem: { backgroundColor: colors.background, borderRadius: 12, padding: 14, alignItems: "center" },
    slItem: { borderWidth: 1, borderColor: colors.error + "40" },
    tpItem: { borderWidth: 1, borderColor: colors.success + "40" },
    gridLabel: { fontSize: 10, color: colors.textSecondary, letterSpacing: 0.8, marginBottom: 6 },
    gridValue: { fontSize: 18, fontWeight: "700", color: colors.textPrimary },
    pipsText: { fontSize: 11, color: colors.textSecondary, marginTop: 3 },
    holdBox: { backgroundColor: colors.background, borderRadius: 12, padding: 14, borderLeftWidth: 3, borderLeftColor: colors.warning, marginTop: 8 },
    holdText: { fontSize: 13, color: colors.textSecondary, lineHeight: 20 },
    holdIconRow: { flexDirection: "row", alignItems: "center", marginBottom: 12 },
    holdDot: { width: 10, height: 10, borderRadius: 5, marginRight: 10 },
    holdTitle: { fontSize: 15, fontWeight: "700" },
    holdSubText: { fontSize: 13, color: colors.textSecondary, lineHeight: 20, marginTop: 8, textAlign: "justify" },
    trendHintRow: { marginTop: 4 },
    trendHintBadge: {
      flexDirection: "row", alignItems: "center", justifyContent: "space-between",
      borderWidth: 1, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 10, marginBottom: 2,
    },
    trendHintDir: { fontSize: 14, fontWeight: "700" },
    trendHintConf: { fontSize: 22, fontWeight: "800" },
    trendBox: { backgroundColor: colors.background, borderRadius: 12, padding: 14, borderLeftWidth: 3, borderLeftColor: colors.primary, marginTop: 4 },
    trendText: { fontSize: 13, color: colors.textSecondary, lineHeight: 20, textAlign: "justify" },
    savedAt: { fontSize: 11, color: colors.textSecondary, marginTop: 2 },
    emptyBox: { alignItems: "center", paddingVertical: 24, backgroundColor: colors.card, borderRadius: 16, marginBottom: 16, paddingHorizontal: 16 },
    emptyText: { color: colors.textSecondary, fontSize: 13 },
    emptyThresholdTitle: { color: colors.textPrimary, fontSize: 13, fontWeight: "700", textAlign: "center", marginBottom: 10 },
    emptyThresholdSub: { color: colors.textSecondary, fontSize: 11, marginBottom: 8 },
    emptyBestRow: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 10 },
    signalBadgeSmall: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20 },
    signalBadgeSmallText: { color: "#fff", fontWeight: "700", fontSize: 12 },
    emptyBestConf: { fontSize: 18, fontWeight: "800" },
    emptyBestTime: { fontSize: 11, color: colors.textSecondary },
    emptyThresholdHint: { fontSize: 11, color: colors.textSecondary, textAlign: "justify" },
    retryBtn: { flexDirection: "row", alignItems: "center", backgroundColor: colors.primary, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 20, marginTop: 12 },
    retryText: { color: "#fff", fontWeight: "600", fontSize: 13 },
    disclaimerBox: {
      marginTop: 10,
      backgroundColor: colors.warning + "14",
      borderWidth: 1,
      borderColor: colors.warning + "45",
      borderRadius: 12,
      paddingVertical: 10,
      paddingHorizontal: 12,
    },
    disclaimerBadge: {
      alignSelf: "flex-start",
      fontSize: 10,
      fontWeight: "800",
      color: colors.warning,
      letterSpacing: 1,
      marginBottom: 6,
    },
    disclaimer: {
      fontSize: 12,
      color: colors.textPrimary,
      textAlign: "justify",
      lineHeight: 18,
      fontWeight: "600",
    },
  });

export default PredictionScreen;
