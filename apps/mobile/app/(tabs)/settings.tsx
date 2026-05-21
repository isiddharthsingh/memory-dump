import { useState } from "react";
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
import { TOUCH_TARGET, theme } from "../../lib/theme";

export default function SettingsScreen() {
  const { config, save } = useConfig();
  const [serverUrl, setServerUrl] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [checking, setChecking] = useState(false);

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
      <Text style={styles.label}>Server URL</Text>
      <TextInput
        style={styles.input}
        placeholder="https://memory.example.com"
        placeholderTextColor={theme.textMuted}
        value={serverUrl}
        onChangeText={setServerUrl}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />

      <Text style={styles.label}>API Token</Text>
      <TextInput
        style={styles.input}
        placeholder="Bearer token from MEMORY_DUMP_API_TOKEN"
        placeholderTextColor={theme.textMuted}
        value={apiToken}
        onChangeText={setApiToken}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
      />

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
            pressed && { backgroundColor: theme.border },
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
  label: {
    color: theme.textMuted,
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    marginTop: 12,
    marginBottom: 4,
    letterSpacing: 0.5,
  },
  input: {
    backgroundColor: theme.bgElev,
    color: theme.text,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 17,
    minHeight: TOUCH_TARGET,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  btnRow: { flexDirection: "row", gap: 8, marginTop: 20 },
  primary: {
    flex: 1,
    backgroundColor: theme.accent,
    borderRadius: 10,
    paddingVertical: 14,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryText: { color: theme.text, fontWeight: "700", fontSize: 17 },
  secondary: {
    flex: 1,
    backgroundColor: theme.bgElev,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 10,
    paddingVertical: 14,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  secondaryText: { color: theme.text, fontWeight: "600", fontSize: 17 },
  about: {
    marginTop: 32,
    padding: 14,
    backgroundColor: theme.bgElev,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  aboutTitle: { color: theme.textMuted, fontWeight: "700", marginBottom: 4 },
  aboutText: { color: theme.text, fontSize: 13, lineHeight: 18 },
});
