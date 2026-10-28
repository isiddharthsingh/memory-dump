import { useEffect, useState } from "react";
import { Animated, StyleProp, StyleSheet, View, ViewStyle } from "react-native";

import { colors, radii, spacing } from "../lib/theme";

export function SkeletonBox({ style }: { style?: StyleProp<ViewStyle> }) {
  const [opacity] = useState(() => new Animated.Value(0.5));

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 750, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.5, duration: 750, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);

  return <Animated.View style={[styles.box, style, { opacity }]} />;
}

/** Same geometry as ItemRow so nothing jumps when the real rows arrive. */
function ItemRowSkeleton() {
  return (
    <View style={styles.row}>
      <SkeletonBox style={styles.thumb} />
      <View style={styles.text}>
        <SkeletonBox style={styles.titleLine} />
        <SkeletonBox style={styles.summaryLine} />
        <SkeletonBox style={styles.metaLine} />
      </View>
    </View>
  );
}

function WikiRowSkeleton() {
  return (
    <View style={styles.wikiRow}>
      <SkeletonBox style={styles.titleLine} />
      <SkeletonBox style={styles.metaLine} />
    </View>
  );
}

export function LibrarySkeletonList({ count = 6 }: { count?: number }) {
  return (
    <View style={styles.list}>
      {Array.from({ length: count }).map((_, i) => (
        <ItemRowSkeleton key={i} />
      ))}
    </View>
  );
}

export function WikiSkeletonList({ count = 7 }: { count?: number }) {
  return (
    <View style={styles.list}>
      {Array.from({ length: count }).map((_, i) => (
        <WikiRowSkeleton key={i} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { backgroundColor: colors.fill, borderRadius: radii.sm },
  list: { paddingTop: spacing.sm, backgroundColor: colors.bg, flex: 1 },
  row: { flexDirection: "row", gap: 14, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  thumb: { width: 56, height: 56, borderRadius: radii.md },
  text: { flex: 1, gap: spacing.sm, paddingTop: 2 },
  titleLine: { height: 16, width: "80%" },
  summaryLine: { height: 13, width: "95%" },
  metaLine: { height: 12, width: "40%" },
  wikiRow: { gap: spacing.sm, paddingHorizontal: spacing.lg, paddingVertical: spacing.lg - 4 },
});
