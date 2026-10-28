import { Ionicons } from "@expo/vector-icons";
import { ReactNode } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleProp,
  StyleSheet,
  Text,
  View,
  ViewStyle,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { TOUCH_TARGET, colors, pressedOpacity, radii, spacing, type } from "../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

/** Large-title header for tab screens (Library, Wiki, Ask). */
export function ScreenHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string | null;
  right?: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.header, { paddingTop: insets.top + spacing.sm }]}>
      <View style={styles.headerText}>
        <Text style={styles.largeTitle} accessibilityRole="header">
          {title}
        </Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>
      {right}
    </View>
  );
}

export function SectionHeader({ title, trailing }: { title: string; trailing?: string }) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle} accessibilityRole="header">
        {title}
      </Text>
      {trailing ? <Text style={styles.sectionTrailing}>{trailing}</Text> : null}
    </View>
  );
}

/** Small grey label above a group of content (Sources, Linked topics…). */
export function Eyebrow({ children, color = colors.ink2 }: { children: ReactNode; color?: string }) {
  return <Text style={[styles.eyebrow, { color }]}>{children}</Text>;
}

/** 34pt pill inside a 44pt hit area. */
export function Chip({
  label,
  count,
  selected = false,
  onPress,
}: {
  label: string;
  count?: number;
  selected?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.chipHit, pressed && { opacity: pressedOpacity }]}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      accessibilityLabel={count != null ? `${label}, ${count}` : label}
    >
      <View style={[styles.chip, selected && styles.chipSelected]}>
        <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
        {count != null ? (
          <Text style={[styles.chipCount, selected && styles.chipTextSelected]}>{count}</Text>
        ) : null}
      </View>
    </Pressable>
  );
}

type ButtonVariant = "primary" | "secondary" | "tint" | "destructive";
type ButtonSize = "lg" | "md" | "sm";

const BUTTON_HEIGHT: Record<ButtonSize, number> = { lg: 54, md: TOUCH_TARGET, sm: 32 };
const BUTTON_FONT: Record<ButtonSize, number> = { lg: 17, md: 16, sm: 13 };

export function Button({
  label,
  onPress,
  variant = "primary",
  size = "lg",
  icon,
  loading = false,
  disabled = false,
  style,
  accessibilityLabel,
}: {
  label: string;
  onPress: () => void;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IoniconName;
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
}) {
  const fg =
    variant === "primary"
      ? colors.onInk
      : variant === "tint"
        ? colors.accent
        : variant === "destructive"
          ? colors.failed
          : colors.ink;
  const bg = variant === "primary" ? colors.ink : variant === "secondary" ? colors.fill : "transparent";
  const inactive = disabled || loading;
  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      style={({ pressed }) => [
        styles.button,
        {
          height: BUTTON_HEIGHT[size],
          minHeight: size === "sm" ? undefined : TOUCH_TARGET,
          backgroundColor: bg,
          paddingHorizontal: variant === "tint" || variant === "destructive" ? spacing.sm : size === "sm" ? spacing.md : spacing.xl,
        },
        disabled && !loading && styles.buttonDisabled,
        pressed && { opacity: pressedOpacity },
        style,
      ]}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: inactive, busy: loading }}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <>
          <Text style={[styles.buttonText, { color: fg, fontSize: BUTTON_FONT[size] }]}>{label}</Text>
          {icon ? <Ionicons name={icon} size={BUTTON_FONT[size]} color={fg} /> : null}
        </>
      )}
    </Pressable>
  );
}

export function IconButton({
  icon,
  onPress,
  accessibilityLabel,
  color = colors.ink,
  size = 22,
  style,
}: {
  icon: IoniconName;
  onPress: () => void;
  accessibilityLabel: string;
  color?: string;
  size?: number;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <Pressable
      onPress={onPress}
      hitSlop={4}
      style={({ pressed }) => [styles.iconButton, pressed && { opacity: pressedOpacity }, style]}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
    >
      <Ionicons name={icon} size={size} color={color} />
    </Pressable>
  );
}

type Status = "pending" | "processing" | "failed" | "queued";

const STATUS_STYLE: Record<Status, { fg: string; dot: string; bg: string; label: string }> = {
  pending: { fg: colors.processing, dot: colors.processingDot, bg: colors.processingSoft, label: "Saving…" },
  processing: { fg: colors.processing, dot: colors.processingDot, bg: colors.processingSoft, label: "Analyzing…" },
  failed: { fg: colors.failed, dot: colors.failed, bg: colors.failedSoft, label: "Failed" },
  queued: { fg: colors.ink2, dot: colors.ink3, bg: colors.fill, label: "Queued offline" },
};

/** Ready items show no badge; every other state gets a colored dot + label. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const s = STATUS_STYLE[status as Status];
  if (!s) return null;
  return (
    <View style={[styles.badge, { backgroundColor: s.bg }]}>
      <View style={[styles.badgeDot, { backgroundColor: s.dot }]} />
      <Text style={[styles.badgeText, { color: s.fg }]} numberOfLines={1}>
        {label ?? s.label}
      </Text>
    </View>
  );
}

/** Inline, plain-language error with at most one action. */
export function ErrorNotice({
  title,
  detail,
  onRetry,
  retrying = false,
  style,
}: {
  title: string;
  detail?: string | null;
  onRetry?: () => void;
  retrying?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <View style={[styles.error, style]} accessibilityRole="alert">
      <Ionicons name="alert-circle-outline" size={20} color={colors.failed} style={styles.errorIcon} />
      <View style={styles.errorText}>
        <Text style={styles.errorTitle}>{title}</Text>
        {detail ? <Text style={styles.errorDetail}>{detail}</Text> : null}
      </View>
      {onRetry ? (
        <Button
          label="Retry"
          variant="secondary"
          size="sm"
          onPress={onRetry}
          loading={retrying}
          style={styles.errorRetry}
        />
      ) : null}
    </View>
  );
}

/** Navigational row: leading tile, title, optional subtitle, chevron. */
export function LinkRow({
  leading,
  title,
  subtitle,
  onPress,
  last = false,
}: {
  leading: ReactNode;
  title: string;
  subtitle?: string | null;
  onPress: () => void;
  last?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.linkRow, pressed && { opacity: pressedOpacity }]}
      accessibilityRole="button"
      accessibilityLabel={subtitle ? `${title}, ${subtitle}` : title}
    >
      {leading}
      <View style={[styles.linkRowText, !last && styles.linkRowSeparator]}>
        <View style={styles.linkRowLabels}>
          <Text style={styles.linkRowTitle} numberOfLines={2}>
            {title}
          </Text>
          {subtitle ? <Text style={styles.linkRowSubtitle}>{subtitle}</Text> : null}
        </View>
        <Ionicons name="chevron-forward" size={16} color={colors.hairlineStrong} />
      </View>
    </Pressable>
  );
}

/** Square tile for wiki pages in lists, matching SourceThumb's geometry. */
export function WikiTile({ size = 40 }: { size?: number }) {
  return (
    <View style={[styles.wikiTile, { width: size, height: size }]}>
      <Ionicons name="book-outline" size={size * 0.45} color={colors.accent} />
    </View>
  );
}

const FADE_STEPS = [0, 0.35, 0.65, 0.85, 0.95, 1, 1, 1];

/** Bottom scrim so scrolling content fades out under floating chrome. */
export function BottomFade({ height }: { height: number }) {
  return (
    <View style={[styles.fade, { height }]} pointerEvents="none">
      {FADE_STEPS.map((opacity, i) => (
        <View key={i} style={[styles.fadeBand, { opacity }]} />
      ))}
    </View>
  );
}

/** Non-interactive label chip (tags, people, topics). */
export function Tag({ label }: { label: string }) {
  return (
    <View style={styles.tag}>
      <Text style={styles.tagText}>{label}</Text>
    </View>
  );
}

/** The app mark: a card dropping into a slot. Drawn, so it scales crisply. */
export function AppMark({ size = 76 }: { size?: number }) {
  const u = size / 76;
  return (
    <View
      style={[styles.mark, { width: size, height: size, borderRadius: 19 * u }]}
      accessible
      accessibilityRole="image"
      accessibilityLabel="Memory Dump"
    >
      <View
        style={[
          styles.markCard,
          { left: 22 * u, top: 13 * u, width: 32 * u, height: 22 * u, borderRadius: 5 * u, opacity: 0.38, transform: [{ rotate: "-9deg" }] },
        ]}
      />
      <View style={[styles.markCard, { left: 22 * u, top: 22 * u, width: 32 * u, height: 22 * u, borderRadius: 5 * u }]} />
      <View style={[styles.markCard, { left: 16 * u, top: 52 * u, width: 44 * u, height: 5 * u, borderRadius: 3 * u }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.md,
    gap: spacing.md,
  },
  headerText: { flex: 1, gap: spacing.xxs },
  largeTitle: { ...type.largeTitle, color: colors.ink },
  subtitle: { ...type.callout, color: colors.ink2 },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xl,
    paddingBottom: spacing.xs,
    backgroundColor: colors.bg,
  },
  sectionTitle: { ...type.section, color: colors.ink },
  sectionTrailing: { ...type.footnote, color: colors.ink3 },
  eyebrow: { ...type.footnote, fontWeight: "600" },
  chipHit: { minHeight: TOUCH_TARGET, justifyContent: "center" },
  // minHeight, not height: long labels (Ask suggestions) wrap and must grow.
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 34,
    paddingVertical: 7,
    paddingHorizontal: 14,
    borderRadius: radii.pill,
    backgroundColor: colors.fill,
  },
  chipSelected: { backgroundColor: colors.ink },
  chipText: { fontSize: 14, fontWeight: "500", color: colors.ink },
  chipCount: { fontSize: 14, color: colors.ink3 },
  chipTextSelected: { color: colors.onInk, fontWeight: "600" },
  button: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    borderRadius: radii.pill,
  },
  buttonDisabled: { opacity: 0.35 },
  buttonText: { fontWeight: "600", letterSpacing: -0.2 },
  iconButton: {
    width: TOUCH_TARGET,
    height: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    height: 24,
    paddingHorizontal: 10,
    borderRadius: radii.pill,
  },
  badgeDot: { width: 7, height: 7, borderRadius: 4 },
  badgeText: { fontSize: 13, fontWeight: "600" },
  error: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.md,
    padding: spacing.lg - 4,
    borderRadius: radii.lg,
    backgroundColor: colors.failedSoft,
  },
  errorIcon: { marginTop: 1 },
  errorText: { flex: 1, gap: spacing.xxs },
  errorTitle: { ...type.callout, fontWeight: "600", color: colors.failedStrong },
  errorDetail: { ...type.subhead, color: colors.ink2 },
  errorRetry: { backgroundColor: colors.bg },
  linkRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, minHeight: 56 },
  linkRowText: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: spacing.md,
    alignSelf: "stretch",
  },
  linkRowSeparator: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.hairlineStrong },
  linkRowLabels: { flex: 1, gap: 1 },
  linkRowTitle: { ...type.callout, fontWeight: "600", color: colors.ink },
  linkRowSubtitle: { ...type.footnote, color: colors.ink3 },
  wikiTile: { borderRadius: radii.sm, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" },
  tag: { height: 28, paddingHorizontal: 10, borderRadius: radii.pill, backgroundColor: colors.fill, justifyContent: "center" },
  tagText: { fontSize: 13, fontWeight: "500", color: colors.ink2 },
  fade: { position: "absolute", left: 0, right: 0, bottom: 0 },
  fadeBand: { flex: 1, backgroundColor: colors.bg },
  mark: { backgroundColor: colors.ink, overflow: "hidden" },
  markCard: { position: "absolute", backgroundColor: colors.onInk },
});
