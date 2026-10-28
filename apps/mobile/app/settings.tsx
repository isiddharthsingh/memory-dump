import { useQuery } from "@tanstack/react-query";
import Constants from "expo-constants";
import * as Haptics from "expo-haptics";
import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, Linking, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { Group, Row } from "../components/Grouped";
import { useAuth } from "../lib/AuthContext";
import { api } from "../lib/api";
import { initials, plural } from "../lib/format";
import { useConfig } from "../lib/settings";
import { shareQueue } from "../lib/shareQueue";
import { colors, radii, spacing, type } from "../lib/theme";

export default function SettingsScreen() {
  const { session, logout, deleteAccount } = useAuth();
  const { config, save } = useConfig();
  // The saved server shows until the user edits it.
  const [serverDraft, setServerUrl] = useState<string | null>(null);
  const serverUrl = serverDraft ?? config?.serverUrl ?? "";
  const [queued, setQueued] = useState(0);
  const [flushing, setFlushing] = useState(false);

  const health = useQuery({
    queryKey: ["health", config?.serverUrl],
    queryFn: () => api.health(),
    enabled: !!config?.serverUrl,
    retry: false,
  });

  const refreshQueued = useCallback(async () => setQueued(await shareQueue.count()), []);
  useFocusEffect(
    useCallback(() => {
      void refreshQueued();
    }, [refreshQueued]),
  );

  async function onServerSubmit() {
    const cleaned = serverUrl.trim().replace(/\/$/, "");
    if (cleaned === (config?.serverUrl ?? "")) return;
    await save({ serverUrl: cleaned || null });
  }

  async function onTest() {
    const r = await health.refetch();
    void Haptics.notificationAsync(
      r.isSuccess ? Haptics.NotificationFeedbackType.Success : Haptics.NotificationFeedbackType.Error,
    );
  }

  async function onSendQueued() {
    setFlushing(true);
    try {
      const r = await shareQueue.flush();
      if (r.failed > 0) Alert.alert("Some shares couldn’t be sent", `${plural(r.failed, "share")} failed and were removed.`);
    } finally {
      setFlushing(false);
      await refreshQueued();
    }
  }

  function onDiscardQueued() {
    Alert.alert(`Discard ${plural(queued, "queued share")}?`, "They haven’t reached your server yet.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Discard",
        style: "destructive",
        onPress: async () => {
          await shareQueue.clear();
          await refreshQueued();
        },
      },
    ]);
  }

  function onSignOut() {
    Alert.alert("Sign out?", "You’ll need to sign in again on this device.", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: () => void logout() },
    ]);
  }

  function onDeleteAccount() {
    Alert.alert(
      "Delete account?",
      "This permanently removes your account and every saved item, wiki page and device. It can’t be undone.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete account",
          style: "destructive",
          onPress: async () => {
            try {
              await deleteAccount();
            } catch (e) {
              Alert.alert("Couldn’t delete your account", (e as Error).message);
            }
          },
        },
      ],
    );
  }

  const status = !config?.serverUrl
    ? { label: "Not set", color: colors.ink2 }
    : health.isFetching
      ? { label: "Checking…", color: colors.ink2 }
      : health.isSuccess
        ? { label: "Connected", color: colors.ready }
        : { label: "Unreachable", color: colors.failed };

  const name = session?.user.display_name || session?.user.email || "";
  const version = Constants.expoConfig?.version ?? "";

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      {session ? (
        <View style={styles.profile}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{initials(name)}</Text>
          </View>
          <View style={styles.profileText}>
            <Text style={styles.profileName} numberOfLines={1}>
              {session.user.display_name || session.user.email}
            </Text>
            {session.user.display_name ? (
              <Text style={styles.profileEmail} numberOfLines={1}>
                {session.user.email}
              </Text>
            ) : null}
          </View>
        </View>
      ) : null}

      <Group header="Server" footer="Memory Dump is self-hosted. Point the app at your own server.">
        <Row
          icon="server-outline"
          iconBg={colors.accent}
          label="Address"
          accessory={
            <TextInput
              value={serverUrl}
              onChangeText={setServerUrl}
              onEndEditing={() => void onServerSubmit()}
              onSubmitEditing={() => void onServerSubmit()}
              placeholder="https://memory.example.com"
              placeholderTextColor={colors.ink3}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              returnKeyType="done"
              style={styles.addressInput}
              accessibilityLabel="Server address"
            />
          }
        />
        <Row
          icon={health.isSuccess ? "checkmark" : "pulse-outline"}
          iconBg={health.isSuccess ? colors.ready : colors.ink2}
          label="Status"
          value={status.label}
          valueColor={status.color}
        />
        <Row label="Test connection" tone="tint" onPress={() => void onTest()} busy={health.isFetching} icon="refresh" iconBg={colors.ink2} />
      </Group>

      <Group
        header="Saving"
        footer={queued > 0 ? "Shares made while offline wait here and send automatically when you’re back online." : undefined}
      >
        <Row icon="file-tray-outline" iconBg={colors.ink2} label="Offline queue" value={queued > 0 ? `${queued} waiting` : "Empty"} />
        {queued > 0 ? <Row label="Send now" tone="tint" icon="paper-plane-outline" iconBg={colors.accent} onPress={() => void onSendQueued()} busy={flushing} /> : null}
        {queued > 0 ? <Row label="Discard queued shares" tone="destructive" icon="trash-outline" iconBg={colors.failed} onPress={onDiscardQueued} /> : null}
        <Row icon="notifications-outline" iconBg={colors.failed} label="Notifications" chevron onPress={() => void Linking.openSettings()} />
      </Group>

      {session ? (
        <Group plain footer="Deleting removes your account, saved items, wiki pages and devices. It can’t be undone.">
          <Row label="Sign out" onPress={onSignOut} />
          <Row label="Delete account…" tone="destructive" onPress={onDeleteAccount} />
        </Group>
      ) : null}

      <Text style={styles.version}>Memory Dump {version}</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.fill },
  scroll: { paddingHorizontal: spacing.lg - 4, paddingBottom: spacing.xxl * 2 },
  profile: {
    marginTop: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    padding: spacing.lg - 4,
    borderRadius: radii.lg,
    backgroundColor: colors.bg,
  },
  avatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.ink,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: { fontSize: 18, fontWeight: "600", color: colors.onInk },
  profileText: { flex: 1, gap: 2 },
  profileName: { ...type.body, fontWeight: "600", color: colors.ink },
  profileEmail: { ...type.subhead, color: colors.ink2 },
  addressInput: { ...type.body, flex: 1, textAlign: "right", color: colors.ink2, paddingVertical: 0 },
  version: { ...type.footnote, color: colors.ink3, textAlign: "center", marginTop: spacing.xl },
});
