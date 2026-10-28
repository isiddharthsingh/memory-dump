import { Ionicons } from "@expo/vector-icons";
import { Children, Fragment, ReactNode, isValidElement } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { colors, radii, spacing, type } from "../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

/** iOS inset-grouped section: optional header, rounded card of rows, optional footer. */
export function Group({
  header,
  footer,
  plain = false,
  children,
}: {
  header?: string;
  footer?: string;
  /** Rows without icons: separators run from the text edge, not past an icon. */
  plain?: boolean;
  children: ReactNode;
}) {
  const rows = Children.toArray(children).filter(isValidElement);
  return (
    <View style={styles.group}>
      {header ? <Text style={styles.header}>{header}</Text> : null}
      <View style={styles.card}>
        {rows.map((row, i) => (
          <Fragment key={row.key ?? i}>
            {i > 0 ? <View style={[styles.separator, plain && { marginLeft: spacing.lg - 4 }]} /> : null}
            {row}
          </Fragment>
        ))}
      </View>
      {footer ? <Text style={styles.footer}>{footer}</Text> : null}
    </View>
  );
}

export function Row({
  icon,
  iconBg = colors.ink2,
  label,
  value,
  valueColor = colors.ink2,
  accessory,
  onPress,
  tone = "default",
  chevron = false,
  busy = false,
}: {
  icon?: IoniconName;
  iconBg?: string;
  label: string;
  value?: string | null;
  valueColor?: string;
  accessory?: ReactNode;
  onPress?: () => void;
  tone?: "default" | "tint" | "destructive";
  chevron?: boolean;
  busy?: boolean;
}) {
  const labelColor = tone === "tint" ? colors.accent : tone === "destructive" ? colors.failed : colors.ink;
  const content = (
    <>
      {icon ? (
        <View style={[styles.icon, { backgroundColor: iconBg }]}>
          <Ionicons name={icon} size={17} color={colors.onInk} />
        </View>
      ) : null}
      <Text style={[styles.label, { color: labelColor }, !value && !accessory && { flex: 1 }]} numberOfLines={1}>
        {label}
      </Text>
      {busy ? <ActivityIndicator size="small" color={colors.ink3} /> : null}
      {accessory}
      {value ? (
        <Text style={[styles.value, { color: valueColor }]} numberOfLines={1}>
          {value}
        </Text>
      ) : null}
      {chevron ? <Ionicons name="chevron-forward" size={16} color={colors.hairlineStrong} /> : null}
    </>
  );
  if (!onPress) return <View style={styles.row}>{content}</View>;
  return (
    <Pressable
      onPress={onPress}
      disabled={busy}
      style={({ pressed }) => [styles.row, pressed && { backgroundColor: colors.fill }]}
      accessibilityRole="button"
      accessibilityLabel={value ? `${label}, ${value}` : label}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  group: { marginTop: spacing.xl },
  header: { ...type.footnote, color: colors.ink2, marginLeft: spacing.lg, marginBottom: 7 },
  footer: { ...type.footnote, color: colors.ink2, marginHorizontal: spacing.lg, marginTop: spacing.sm },
  card: { backgroundColor: colors.bg, borderRadius: radii.lg, overflow: "hidden" },
  separator: { height: StyleSheet.hairlineWidth, backgroundColor: colors.hairlineStrong, marginLeft: 58 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    minHeight: 50,
    paddingHorizontal: spacing.lg - 4,
  },
  icon: { width: 30, height: 30, borderRadius: radii.sm, alignItems: "center", justifyContent: "center" },
  label: { ...type.body, flexShrink: 1 },
  value: { ...type.body, flex: 1, textAlign: "right" },
});
