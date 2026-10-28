import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { useState } from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { api } from "../lib/api";
import { colors, radii, spacing, type } from "../lib/theme";
import { Button } from "./ui";

function normalizeUrl(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  const withScheme = /^[a-z]+:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  return /^https?:\/\/[^\s/]+\.[^\s]+/i.test(withScheme) ? withScheme : null;
}

/** Manual capture for links that didn't come through the share sheet. */
export function SaveLinkSheet({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const [text, setText] = useState("");
  const qc = useQueryClient();
  const url = normalizeUrl(text);

  // Clear the field each time the sheet opens.
  const [wasVisible, setWasVisible] = useState(visible);
  if (visible !== wasVisible) {
    setWasVisible(visible);
    if (visible) setText("");
  }

  const save = useMutation({
    mutationFn: (u: string) => api.ingest(u),
    onSuccess: () => {
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      qc.invalidateQueries({ queryKey: ["items"] });
      onClose();
    },
  });

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityLabel="Close">
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.kav}
          pointerEvents="box-none"
        >
          <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
            <View style={styles.grabber} />
            <Text style={styles.title}>Save a link</Text>
            <Text style={styles.hint}>Paste any article, video or post. It’s summarized and filed for you.</Text>
            <TextInput
              value={text}
              onChangeText={setText}
              placeholder="https://"
              placeholderTextColor={colors.ink3}
              style={styles.input}
              autoFocus
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              returnKeyType="done"
              onSubmitEditing={() => url && save.mutate(url)}
              accessibilityLabel="Link to save"
            />
            {save.isError ? (
              <Text style={styles.err}>Couldn’t save: {(save.error as Error).message}</Text>
            ) : null}
            <Button
              label="Save to Library"
              onPress={() => url && save.mutate(url)}
              disabled={!url}
              loading={save.isPending}
            />
          </Pressable>
        </KeyboardAvoidingView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: colors.scrim, justifyContent: "flex-end" },
  kav: { width: "100%" },
  sheet: {
    backgroundColor: colors.bg,
    borderTopLeftRadius: radii.sheet,
    borderTopRightRadius: radii.sheet,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.xxl + spacing.sm,
    gap: spacing.md,
  },
  grabber: {
    alignSelf: "center",
    width: 36,
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.hairlineStrong,
    marginBottom: spacing.sm,
  },
  title: { ...type.section, color: colors.ink },
  hint: { ...type.callout, color: colors.ink2 },
  input: {
    ...type.body,
    minHeight: 52,
    paddingHorizontal: spacing.lg - 4,
    borderRadius: radii.lg,
    backgroundColor: colors.fill,
    color: colors.ink,
  },
  err: { ...type.subhead, color: colors.failed },
});
