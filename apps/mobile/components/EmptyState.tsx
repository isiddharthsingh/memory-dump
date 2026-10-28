import { Ionicons } from "@expo/vector-icons";
import { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";

import { colors, radii, spacing, type } from "../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

export function EmptyState({
  icon,
  title,
  message,
  children,
}: {
  icon: IoniconName;
  title: string;
  message: string;
  children?: ReactNode;
}) {
  return (
    <View style={styles.wrap}>
      <View style={styles.iconWrap} accessibilityElementsHidden importantForAccessibility="no">
        <Ionicons name={icon} size={28} color={colors.ink2} />
      </View>
      <Text style={styles.title} accessibilityRole="header">
        {title}
      </Text>
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
    paddingHorizontal: spacing.xxl,
    paddingVertical: spacing.xxl,
    gap: spacing.sm,
    backgroundColor: colors.bg,
  },
  iconWrap: {
    width: 60,
    height: 60,
    borderRadius: radii.xl,
    backgroundColor: colors.fill,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.sm,
  },
  title: { ...type.section, color: colors.ink, textAlign: "center" },
  message: { ...type.callout, color: colors.ink2, textAlign: "center", maxWidth: 320, marginBottom: spacing.lg },
});
