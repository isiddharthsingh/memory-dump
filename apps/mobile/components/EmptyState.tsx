import { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";

import { theme } from "../lib/theme";

export function EmptyState({
  emoji,
  title,
  message,
  children,
}: {
  emoji: string;
  title: string;
  message: string;
  children?: ReactNode;
}) {
  return (
    <View style={styles.wrap}>
      <Text style={styles.emoji}>{emoji}</Text>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.message}>{message}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 32,
    gap: 8,
  },
  emoji: { fontSize: 56, marginBottom: 4 },
  title: { color: theme.text, fontSize: 18, fontWeight: "600" },
  message: { color: theme.textMuted, textAlign: "center", marginBottom: 16 },
});
