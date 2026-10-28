import { Ionicons } from "@expo/vector-icons";
import { ReactNode, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { domainOf } from "../lib/format";
import { TOUCH_TARGET, colors, fonts, pressedOpacity, radii, spacing, type } from "../lib/theme";
import { AppMark } from "./ui";

/** Brand header + scrollable form + footer link, shared by sign in and sign up. */
export function AuthScaffold({ children, footer }: { children: ReactNode; footer: ReactNode }) {
  const insets = useSafeAreaInsets();
  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView
        contentContainerStyle={[styles.scroll, { paddingTop: insets.top + spacing.xxl * 2, paddingBottom: insets.bottom + spacing.xl }]}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.brand}>
          <AppMark />
          <Text style={styles.wordmark} accessibilityRole="header">
            Memory Dump
          </Text>
          <Text style={styles.tagline}>Save anything. Ask it later.</Text>
        </View>
        <View style={styles.form}>{children}</View>
        <View style={styles.footer}>{footer}</View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

/** Stacked fields in one rounded group, iOS-style. */
export function FieldGroup({ children }: { children: ReactNode }) {
  return <View style={styles.group}>{children}</View>;
}

export function Field({
  label,
  divider = false,
  secure = false,
  ...input
}: { label: string; divider?: boolean; secure?: boolean } & TextInputProps) {
  const [revealed, setRevealed] = useState(false);
  return (
    <>
      {divider ? <View style={styles.divider} /> : null}
      <View style={styles.fieldRow}>
        <View style={styles.field}>
          <Text style={styles.fieldLabel}>{label}</Text>
          <TextInput
            {...input}
            secureTextEntry={secure && !revealed}
            placeholderTextColor={colors.ink3}
            style={styles.input}
            accessibilityLabel={label}
          />
        </View>
        {secure ? (
          <Pressable
            onPress={() => setRevealed((v) => !v)}
            style={styles.reveal}
            accessibilityRole="button"
            accessibilityLabel={revealed ? "Hide password" : "Show password"}
          >
            <Ionicons name={revealed ? "eye-off-outline" : "eye-outline"} size={20} color={colors.ink2} />
          </Pressable>
        ) : null}
      </View>
    </>
  );
}

/** Self-hosted server: a quiet one-liner once set, a field when it isn't. */
export function ServerField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  if (value && !editing) {
    return (
      <Pressable
        onPress={() => setEditing(true)}
        style={({ pressed }) => [styles.serverRow, pressed && { opacity: pressedOpacity }]}
        accessibilityRole="button"
        accessibilityLabel={`Server ${domainOf(value)}. Change`}
      >
        <View style={styles.serverDot} />
        <Text style={styles.serverText} numberOfLines={1}>
          Server: {domainOf(value)}
        </Text>
        <Text style={styles.serverChange}>Change</Text>
      </Pressable>
    );
  }
  return (
    <FieldGroup>
      <Field
        label="Server address"
        value={value}
        onChangeText={onChange}
        placeholder="https://memory.example.com"
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        autoFocus={editing}
      />
    </FieldGroup>
  );
}

export function FooterLink({ prompt, label, onPress }: { prompt: string; label: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={styles.footerLink} accessibilityRole="link" accessibilityLabel={`${prompt} ${label}`}>
      <Text style={styles.footerText}>
        {prompt} <Text style={styles.footerAction}>{label}</Text>
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.bg },
  scroll: { flexGrow: 1, paddingHorizontal: spacing.xl },
  brand: { alignItems: "center", gap: spacing.sm },
  wordmark: { fontFamily: fonts.serifSemibold, fontSize: 34, lineHeight: 40, color: colors.ink, marginTop: spacing.md },
  tagline: { ...type.body, color: colors.ink2 },
  form: { marginTop: spacing.xxl, gap: spacing.md },
  footer: { flex: 1, justifyContent: "flex-end", alignItems: "center", marginTop: spacing.xl },
  group: { borderRadius: radii.lg, backgroundColor: colors.fill, overflow: "hidden" },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.hairlineStrong, marginLeft: spacing.lg - 4 },
  fieldRow: { flexDirection: "row", alignItems: "center", paddingRight: spacing.xs },
  field: { flex: 1, paddingHorizontal: spacing.lg - 4, paddingVertical: 9, gap: 2 },
  fieldLabel: { ...type.caption, color: colors.ink2 },
  input: { ...type.body, color: colors.ink, padding: 0 },
  reveal: { width: TOUCH_TARGET, height: TOUCH_TARGET, alignItems: "center", justifyContent: "center" },
  serverRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, minHeight: TOUCH_TARGET, paddingHorizontal: spacing.xs },
  serverDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.ready },
  serverText: { ...type.subhead, color: colors.ink2, flex: 1 },
  serverChange: { ...type.subhead, fontWeight: "600", color: colors.accent },
  footerLink: { minHeight: TOUCH_TARGET, justifyContent: "center", paddingHorizontal: spacing.sm },
  footerText: { ...type.callout, color: colors.ink2 },
  footerAction: { color: colors.accent, fontWeight: "600" },
});
