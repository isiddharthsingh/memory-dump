import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";

import { theme } from "../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const APPS: { name: string; icon: IoniconName; color: string; highlight?: boolean }[] = [
  { name: "Messages", icon: "chatbubble", color: "#34C759" },
  { name: "Mail", icon: "mail", color: "#0A84FF" },
  { name: "Notes", icon: "document-text", color: "#FFCC00" },
  { name: "memory-dump", icon: "sparkles", color: theme.accent, highlight: true },
];

export function ShareSheetMock() {
  return (
    <View style={styles.sheet}>
      <View style={styles.handle} />
      <View style={styles.linkBar}>
        <Ionicons name="link" size={14} color={theme.textMuted} />
        <Text style={styles.linkText} numberOfLines={1}>
          youtube.com/watch?v=…
        </Text>
      </View>
      <View style={styles.row}>
        {APPS.map((a) => (
          <View key={a.name} style={styles.appCell}>
            <View
              style={[
                styles.appIcon,
                { backgroundColor: a.color + "22" },
                a.highlight && styles.appIconHighlight,
              ]}
            >
              <Ionicons name={a.icon} size={22} color={a.color} />
            </View>
            <Text
              style={[styles.appLabel, a.highlight && styles.appLabelHighlight]}
              numberOfLines={1}
            >
              {a.name}
            </Text>
          </View>
        ))}
      </View>
      <View style={styles.arrowWrap}>
        <Ionicons name="arrow-up" size={18} color={theme.accent} />
        <Text style={styles.arrowText}>Tap memory-dump to save</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: {
    width: 280,
    backgroundColor: theme.bgElev,
    borderRadius: 18,
    padding: 14,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    shadowColor: "#000",
    shadowOpacity: 0.25,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.border,
    marginBottom: 12,
  },
  linkBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: theme.bg,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginBottom: 14,
  },
  linkText: { color: theme.textMuted, fontSize: 12, flex: 1 },
  row: { flexDirection: "row", justifyContent: "space-between" },
  appCell: { alignItems: "center", width: 56 },
  appIcon: {
    width: 48,
    height: 48,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  appIconHighlight: {
    borderWidth: 2,
    borderColor: theme.accent,
  },
  appLabel: {
    color: theme.textMuted,
    fontSize: 10,
    marginTop: 6,
    textAlign: "center",
  },
  appLabelHighlight: { color: theme.accent, fontWeight: "700" },
  arrowWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 14,
    justifyContent: "center",
  },
  arrowText: { color: theme.accent, fontSize: 12, fontWeight: "600" },
});
