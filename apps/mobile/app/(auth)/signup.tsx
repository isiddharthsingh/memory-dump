import { Link, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useAuth } from "../../lib/AuthContext";
import { loadConfig, saveConfig, useConfig } from "../../lib/settings";
import { TOUCH_TARGET, cardShadow, radii, theme } from "../../lib/theme";

const MIN_PASSWORD_LEN = 10;

export default function SignupScreen() {
  const router = useRouter();
  const { status, signup } = useAuth();
  const { config } = useConfig();
  const [serverUrl, setServerUrl] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "signedIn") router.replace("/");
  }, [status, router]);

  useEffect(() => {
    if (config?.serverUrl && serverUrl === "") setServerUrl(config.serverUrl);
  }, [config, serverUrl]);

  async function onSubmit() {
    setError(null);
    if (password.length < MIN_PASSWORD_LEN) {
      setError(`Password must be at least ${MIN_PASSWORD_LEN} characters.`);
      return;
    }
    setBusy(true);
    try {
      const cleanedUrl = serverUrl.trim().replace(/\/$/, "");
      if (cleanedUrl !== (config?.serverUrl ?? "")) {
        await saveConfig({ serverUrl: cleanedUrl || null, apiToken: null });
        await loadConfig();
      }
      await signup(
        email.trim().toLowerCase(),
        password,
        displayName.trim() || undefined,
      );
    } catch (e) {
      setError((e as Error).message || "Sign up failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.flex}
    >
      <View style={styles.container}>
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

          <Text style={[styles.label, { marginTop: 16 }]}>Email</Text>
          <TextInput
            style={styles.input}
            placeholder="you@example.com"
            placeholderTextColor={theme.textFaint}
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            textContentType="emailAddress"
          />

          <Text style={[styles.label, { marginTop: 16 }]}>Display name (optional)</Text>
          <TextInput
            style={styles.input}
            placeholder="Jane Doe"
            placeholderTextColor={theme.textFaint}
            value={displayName}
            onChangeText={setDisplayName}
            autoCapitalize="words"
          />

          <Text style={[styles.label, { marginTop: 16 }]}>Password</Text>
          <TextInput
            style={styles.input}
            placeholder={`At least ${MIN_PASSWORD_LEN} characters`}
            placeholderTextColor={theme.textFaint}
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            textContentType="newPassword"
          />

          {error ? <Text style={styles.error}>{error}</Text> : null}
        </View>

        <Pressable
          style={({ pressed }) => [
            styles.primary,
            busy && { opacity: 0.6 },
            pressed && { backgroundColor: theme.accentPressed },
          ]}
          onPress={onSubmit}
          disabled={busy || !email || !password || !serverUrl}
          accessibilityRole="button"
          accessibilityLabel="Create account"
        >
          {busy ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.primaryText}>Create account</Text>
          )}
        </Pressable>

        <View style={styles.footer}>
          <Text style={styles.footerText}>Already have an account? </Text>
          <Link href="/(auth)/login" replace asChild>
            <Pressable hitSlop={8}>
              <Text style={styles.link}>Sign in</Text>
            </Pressable>
          </Link>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: theme.bg },
  container: { padding: 16, gap: 12 },
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
  primary: {
    backgroundColor: theme.accent,
    borderRadius: radii.md,
    paddingVertical: 14,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
    ...cardShadow("sm"),
  },
  primaryText: { color: "#FFFFFF", fontWeight: "700", fontSize: 17 },
  error: {
    color: theme.danger,
    marginTop: 12,
    fontSize: 14,
  },
  footer: {
    flexDirection: "row",
    justifyContent: "center",
    marginTop: 16,
  },
  footerText: { color: theme.textMuted, fontSize: 14 },
  link: { color: theme.accent, fontWeight: "600", fontSize: 14 },
});
