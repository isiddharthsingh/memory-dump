import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
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
import { TOUCH_TARGET, cardShadow, radii, theme } from "../lib/theme";

export function NoteEditor({
  visible,
  itemId,
  initialText,
  onClose,
}: {
  visible: boolean;
  itemId: string | null;
  initialText: string;
  onClose: () => void;
}) {
  const [text, setText] = useState(initialText);
  const qc = useQueryClient();

  useEffect(() => {
    if (visible) setText(initialText);
  }, [visible, initialText]);

  const save = useMutation({
    mutationFn: () => api.updateItem(itemId!, { share_text: text.trim() || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      if (itemId) qc.invalidateQueries({ queryKey: ["item", itemId] });
      onClose();
    },
  });

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <Pressable style={styles.backdrop} onPress={onClose}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.kavWrap}
          pointerEvents="box-none"
        >
          <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
            <View style={styles.handle} />
            <Text style={styles.title}>Note</Text>
            <Text style={styles.hint}>
              Folded into embeddings — retrieval gets better when you add context.
            </Text>
            <TextInput
              value={text}
              onChangeText={setText}
              placeholder="Why you saved this, key idea, follow-up…"
              placeholderTextColor={theme.textFaint}
              multiline
              style={styles.input}
              autoFocus
            />
            {save.isError ? (
              <Text style={styles.err}>{(save.error as Error).message}</Text>
            ) : null}
            <View style={styles.actions}>
              <Pressable
                onPress={onClose}
                style={({ pressed }) => [
                  styles.btnGhost,
                  pressed && { opacity: 0.6 },
                ]}
                accessibilityRole="button"
                accessibilityLabel="Cancel"
              >
                <Text style={styles.btnGhostText}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={() => itemId && save.mutate()}
                disabled={save.isPending || !itemId}
                style={({ pressed }) => [
                  styles.btnPrimary,
                  save.isPending && { opacity: 0.6 },
                  pressed && { backgroundColor: theme.accentPressed },
                ]}
                accessibilityRole="button"
                accessibilityLabel="Save note and re-embed"
                accessibilityState={{ disabled: save.isPending || !itemId }}
              >
                {save.isPending ? (
                  <ActivityIndicator color="#FFFFFF" />
                ) : (
                  <Text style={styles.btnPrimaryText}>Save & re-embed</Text>
                )}
              </Pressable>
            </View>
          </Pressable>
        </KeyboardAvoidingView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: theme.scrim,
    justifyContent: "flex-end",
  },
  kavWrap: { justifyContent: "flex-end" },
  sheet: {
    backgroundColor: theme.surface,
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
    padding: 20,
    gap: 10,
    paddingBottom: 32,
    ...cardShadow("lg"),
  },
  handle: {
    alignSelf: "center",
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.borderStrong,
    marginBottom: 6,
  },
  title: { color: theme.text, fontSize: 20, fontWeight: "700", letterSpacing: -0.3 },
  hint: { color: theme.textMuted, fontSize: 13, lineHeight: 18 },
  input: {
    backgroundColor: theme.surfaceSunken,
    color: theme.text,
    borderRadius: radii.md,
    padding: 14,
    minHeight: 120,
    textAlignVertical: "top",
    fontSize: 15,
    lineHeight: 21,
  },
  err: { color: theme.danger, fontSize: 12 },
  actions: { flexDirection: "row", justifyContent: "flex-end", gap: 10, marginTop: 6 },
  btnGhost: {
    paddingHorizontal: 16,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  btnGhostText: { color: theme.textMuted, fontWeight: "600", fontSize: 16 },
  btnPrimary: {
    backgroundColor: theme.accent,
    borderRadius: radii.pill,
    paddingHorizontal: 20,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
    ...cardShadow("sm"),
  },
  btnPrimaryText: { color: "#FFFFFF", fontWeight: "700", fontSize: 16 },
});
