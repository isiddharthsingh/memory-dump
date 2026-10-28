import { useMutation, useQueryClient } from "@tanstack/react-query";
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

export function NoteEditor({
  visible,
  itemId,
  itemTitle,
  initialText,
  onClose,
}: {
  visible: boolean;
  itemId: string | null;
  itemTitle?: string | null;
  initialText: string;
  onClose: () => void;
}) {
  const [text, setText] = useState(initialText);
  const qc = useQueryClient();

  // Start from the saved note each time the sheet opens (adjusted during
  // render rather than in an effect, so there's no flash of the old draft).
  const openKey = visible ? `${itemId}:${initialText}` : null;
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  if (openKey !== openedFor) {
    setOpenedFor(openKey);
    if (openKey) setText(initialText);
  }

  const save = useMutation({
    mutationFn: () => api.updateItem(itemId!, { share_text: text.trim() || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      if (itemId) qc.invalidateQueries({ queryKey: ["item", itemId] });
      onClose();
    },
  });

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityLabel="Close note">
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.kavWrap}
          pointerEvents="box-none"
        >
          <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
            <View style={styles.grabber} />
            <Text style={styles.title}>Note</Text>
            {itemTitle ? (
              <Text style={styles.itemTitle} numberOfLines={1}>
                {itemTitle}
              </Text>
            ) : null}
            <Text style={styles.hint}>Why you saved it or what to remember. Notes make it easier to find later.</Text>
            <TextInput
              value={text}
              onChangeText={setText}
              placeholder="Key idea, follow-up, who mentioned it…"
              placeholderTextColor={colors.ink3}
              multiline
              style={styles.input}
              autoFocus
              accessibilityLabel="Note"
            />
            {save.isError ? <Text style={styles.err}>Couldn’t save: {(save.error as Error).message}</Text> : null}
            <View style={styles.actions}>
              <Button label="Cancel" variant="secondary" size="md" onPress={onClose} />
              <Button
                label="Save note"
                size="md"
                onPress={() => itemId && save.mutate()}
                disabled={!itemId}
                loading={save.isPending}
              />
            </View>
          </Pressable>
        </KeyboardAvoidingView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: colors.scrim, justifyContent: "flex-end" },
  kavWrap: { justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.bg,
    borderTopLeftRadius: radii.sheet,
    borderTopRightRadius: radii.sheet,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.xxl,
    gap: spacing.sm,
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
  itemTitle: { ...type.subhead, fontWeight: "600", color: colors.ink2 },
  hint: { ...type.footnote, color: colors.ink2 },
  input: {
    ...type.callout,
    backgroundColor: colors.fill,
    color: colors.ink,
    borderRadius: radii.lg,
    padding: 14,
    minHeight: 120,
    textAlignVertical: "top",
    marginTop: spacing.xs,
  },
  err: { ...type.footnote, color: colors.failed },
  actions: { flexDirection: "row", justifyContent: "flex-end", gap: spacing.sm, marginTop: spacing.xs },
});
