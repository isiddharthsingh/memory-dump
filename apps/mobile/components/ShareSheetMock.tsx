import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";

import { colors, floatingShadow, radii, spacing, type } from "../lib/theme";
import { AppMark } from "./ui";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const OTHER_APPS: { name: string; icon: IoniconName }[] = [
  { name: "Messages", icon: "chatbubble-outline" },
  { name: "Mail", icon: "mail-outline" },
  { name: "Notes", icon: "document-text-outline" },
];

/** Illustration of the iOS share sheet for the empty Library. */
export function ShareSheetMock() {
  return (
    <View style={styles.sheet} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <View style={styles.grabber} />
      <View style={styles.linkBar}>
        <Ionicons name="link-outline" size={14} color={colors.ink2} />
        <Text style={styles.linkText} numberOfLines={1}>
          youtube.com/watch?v=…
        </Text>
      </View>
      <View style={styles.row}>
        {OTHER_APPS.map((a) => (
          <View key={a.name} style={styles.appCell}>
            <View style={styles.appIcon}>
              <Ionicons name={a.icon} size={22} color={colors.ink2} />
            </View>
            <Text style={styles.appLabel} numberOfLines={1}>
              {a.name}
            </Text>
          </View>
        ))}
        <View style={styles.appCell}>
          <View style={styles.markRing}>
            <AppMark size={44} />
          </View>
          <Text style={[styles.appLabel, styles.appLabelHighlight]} numberOfLines={1}>
            Memory Dump
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: {
    width: 300,
    backgroundColor: colors.bg,
    borderRadius: radii.sheet,
    padding: spacing.lg - 4,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairlineStrong,
    ...floatingShadow,
  },
  grabber: {
    alignSelf: "center",
    width: 36,
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.hairlineStrong,
    marginBottom: spacing.md,
  },
  linkBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.fill,
    borderRadius: radii.sm,
    paddingHorizontal: 10,
    paddingVertical: spacing.sm,
    marginBottom: spacing.lg - 4,
  },
  linkText: { ...type.caption, fontWeight: "400", color: colors.ink2, flex: 1 },
  row: { flexDirection: "row", justifyContent: "space-between" },
  appCell: { alignItems: "center", width: 62, gap: 6 },
  appIcon: {
    width: 48,
    height: 48,
    borderRadius: radii.md,
    backgroundColor: colors.fill,
    alignItems: "center",
    justifyContent: "center",
  },
  markRing: { padding: 2, borderRadius: radii.md + 2, borderWidth: 2, borderColor: colors.accent },
  appLabel: { fontSize: 10, color: colors.ink2, textAlign: "center" },
  appLabelHighlight: { color: colors.accent, fontWeight: "700" },
});
