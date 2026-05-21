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
import { TOUCH_TARGET, theme } from "../lib/theme";

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
              placeholderTextColor={theme.textMuted}
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
                  <ActivityIndicator color={theme.text} />
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
    backgroundColor: "#000a",
    justifyContent: "flex-end",
  },
  kavWrap: { justifyContent: "flex-end" },
  sheet: {
    backgroundColor: theme.bgElev,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    gap: 10,
    paddingBottom: 32,
  },
  handle: {
    alignSelf: "center",
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.border,
    marginBottom: 6,
  },
  title: { color: theme.text, fontSize: 18, fontWeight: "700" },
  hint: { color: theme.textMuted, fontSize: 12 },
  input: {
    backgroundColor: theme.bg,
    color: theme.text,
    borderRadius: 12,
    padding: 12,
    minHeight: 120,
    textAlignVertical: "top",
    fontSize: 15,
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
    borderRadius: 999,
    paddingHorizontal: 20,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  btnPrimaryText: { color: theme.text, fontWeight: "700", fontSize: 16 },
});
