import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import {
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { api } from "../../lib/api";
import { useConfig } from "../../lib/settings";
import { shareQueue } from "../../lib/shareQueue";
import { TOUCH_TARGET, cardShadow, radii, theme } from "../../lib/theme";

export default function SettingsScreen() {
  const { config, save } = useConfig();
  const [serverUrl, setServerUrl] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [checking, setChecking] = useState(false);
  const [pendingShares, setPendingShares] = useState(0);

  const refreshPending = useCallback(async () => {
    setPendingShares(await shareQueue.count());
  }, []);

  useFocusEffect(
    useCallback(() => {
      refreshPending();
    }, [refreshPending]),
  );

  async function onRetryQueue() {
    const r = await shareQueue.flush();
    await refreshPending();
    Alert.alert(
      "Queue",
      `Sent ${r.sent} · Dropped ${r.failed} · Remaining ${r.remaining}`,
    );
  }

  async function onClearQueue() {
    Alert.alert("Clear queued shares?", "This discards any pending shares.", [
      { text: "Cancel" },
      {
        text: "Clear",
        style: "destructive",
        onPress: async () => {
          await shareQueue.clear();
          await refreshPending();
        },
      },
    ]);
  }

  // Keep local form in sync once config loads.
  if (config && serverUrl === "" && apiToken === "") {
    if (config.serverUrl) setServerUrl(config.serverUrl);
    if (config.apiToken) setApiToken(config.apiToken);
  }

  async function onSave() {
    const cleanedUrl = serverUrl.trim().replace(/\/$/, "");
    await save({ serverUrl: cleanedUrl || null, apiToken: apiToken.trim() || null });
    Alert.alert("Saved", "Settings updated.");
  }

  async function onTest() {
    setChecking(true);
    try {
      await api.health();
      Alert.alert("OK", "Server reachable.");
    } catch (e) {
      Alert.alert("Failed", String((e as Error).message));
    } finally {
      setChecking(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.card}>
        <Text style={styles.label}>Server URL</Text>
        <TextInput
          style={styles.input}
          placeholder="https://memory.example.com"
          placeholderTextColor={theme.textFaint}
          value={serverUrl}
          onChangeText={setServerUrl}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
        />

        <Text style={[styles.label, { marginTop: 16 }]}>API Token</Text>
        <TextInput
          style={styles.input}
          placeholder="Bearer token from MEMORY_DUMP_API_TOKEN"
          placeholderTextColor={theme.textFaint}
          value={apiToken}
          onChangeText={setApiToken}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
        />
      </View>

      <View style={styles.btnRow}>
        <Pressable
          style={({ pressed }) => [
            styles.primary,
            pressed && { backgroundColor: theme.accentPressed },
          ]}
          onPress={onSave}
          accessibilityRole="button"
          accessibilityLabel="Save settings"
        >
          <Text style={styles.primaryText}>Save</Text>
        </Pressable>
        <Pressable
          style={({ pressed }) => [
            styles.secondary,
            checking && { opacity: 0.6 },
            pressed && { backgroundColor: theme.surfaceSunken },
          ]}
          onPress={onTest}
          disabled={checking}
          accessibilityRole="button"
          accessibilityLabel="Test server connection"
          accessibilityState={{ disabled: checking }}
        >
          <Text style={styles.secondaryText}>
            {checking ? "Testing…" : "Test connection"}
          </Text>
        </Pressable>
      </View>

      {pendingShares > 0 ? (
        <View style={styles.queueCard}>
          <Text style={styles.queueTitle}>
            {pendingShares} pending share{pendingShares === 1 ? "" : "s"}
          </Text>
          <Text style={styles.queueText}>
            Saved locally while offline. They'll send automatically when the
            app comes back online.
          </Text>
          <View style={styles.queueBtnRow}>
            <Pressable
              style={({ pressed }) => [
                styles.queueBtn,
                pressed && { backgroundColor: theme.surfaceSunken },
              ]}
              onPress={onRetryQueue}
              accessibilityRole="button"
              accessibilityLabel="Retry pending shares now"
            >
              <Text style={styles.queueBtnText}>Retry now</Text>
            </Pressable>
            <Pressable
              style={({ pressed }) => [
                styles.queueBtnDanger,
                pressed && { opacity: 0.6 },
              ]}
              onPress={onClearQueue}
              accessibilityRole="button"
              accessibilityLabel="Discard pending shares"
            >
              <Text style={styles.queueBtnDangerText}>Discard</Text>
            </Pressable>
          </View>
        </View>
      ) : null}

      <View style={styles.about}>
        <Text style={styles.aboutTitle}>About</Text>
        <Text style={styles.aboutText}>
          memory-dump v0.1.0 · Tap share on any link to save it. Ask questions
          in natural language to find anything.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 8 },
  card: {
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    padding: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    ...cardShadow("sm"),
  },
  label: {
    color: theme.textMuted,
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    marginBottom: 6,
    letterSpacing: 0.5,
  },
  input: {
    backgroundColor: theme.surfaceSunken,
    color: theme.text,
    borderRadius: radii.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    minHeight: TOUCH_TARGET,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  btnRow: { flexDirection: "row", gap: 10, marginTop: 16 },
  primary: {
    flex: 1,
    backgroundColor: theme.accent,
    borderRadius: radii.md,
    paddingVertical: 14,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
    ...cardShadow("sm"),
  },
  primaryText: { color: "#FFFFFF", fontWeight: "700", fontSize: 17 },
  secondary: {
    flex: 1,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    borderRadius: radii.md,
    paddingVertical: 14,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  secondaryText: { color: theme.text, fontWeight: "600", fontSize: 17 },
  about: {
    marginTop: 24,
    padding: 16,
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    ...cardShadow("sm"),
  },
  aboutTitle: {
    color: theme.textMuted,
    fontWeight: "700",
    marginBottom: 6,
    fontSize: 12,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  aboutText: { color: theme.text, fontSize: 14, lineHeight: 20 },
  queueCard: {
    marginTop: 16,
    padding: 16,
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    ...cardShadow("sm"),
  },
  queueTitle: {
    color: theme.text,
    fontWeight: "700",
    fontSize: 15,
    marginBottom: 4,
  },
  queueText: { color: theme.textMuted, fontSize: 13, lineHeight: 18 },
  queueBtnRow: { flexDirection: "row", gap: 10, marginTop: 12 },
  queueBtn: {
    flex: 1,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.borderStrong,
    borderRadius: radii.md,
    paddingVertical: 10,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  queueBtnText: { color: theme.text, fontWeight: "600", fontSize: 15 },
  queueBtnDanger: {
    flex: 1,
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: theme.danger,
    borderRadius: radii.md,
    paddingVertical: 10,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  queueBtnDangerText: { color: theme.danger, fontWeight: "600", fontSize: 15 },
});
