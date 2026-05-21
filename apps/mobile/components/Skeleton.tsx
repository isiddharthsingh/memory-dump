import { useEffect, useRef } from "react";
import { Animated, StyleProp, StyleSheet, View, ViewStyle } from "react-native";

import { radii, theme } from "../lib/theme";

export function SkeletonBox({ style }: { style?: StyleProp<ViewStyle> }) {
  const opacity = useRef(new Animated.Value(0.5)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 1,
          duration: 750,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 0.5,
          duration: 750,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);

  return <Animated.View style={[styles.box, style, { opacity }]} />;
}

export function ItemCardSkeleton() {
  return (
    <View style={styles.card}>
      <SkeletonBox style={styles.thumb} />
      <View style={styles.body}>
        <SkeletonBox style={styles.titleLine} />
        <SkeletonBox style={styles.titleLineShort} />
        <SkeletonBox style={styles.summaryLine} />
        <View style={styles.tagRow}>
          <SkeletonBox style={styles.tag} />
          <SkeletonBox style={styles.tag} />
        </View>
      </View>
    </View>
  );
}

export function WikiRowSkeleton() {
  return (
    <View style={styles.row}>
      <SkeletonBox style={styles.titleLine} />
      <SkeletonBox style={styles.slugLine} />
      <SkeletonBox style={styles.metaLine} />
    </View>
  );
}

export function InboxSkeletonList({ count = 5 }: { count?: number }) {
  return (
    <View style={styles.list}>
      {Array.from({ length: count }).map((_, i) => (
        <ItemCardSkeleton key={i} />
      ))}
    </View>
  );
}

export function WikiSkeletonList({ count = 6 }: { count?: number }) {
  return (
    <View style={styles.list}>
      {Array.from({ length: count }).map((_, i) => (
        <WikiRowSkeleton key={i} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    backgroundColor: theme.surfaceSunken,
    borderRadius: 6,
  },
  list: { padding: 12, paddingTop: 14, backgroundColor: theme.bg, flex: 1 },
  card: {
    flexDirection: "row",
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    marginBottom: 12,
    overflow: "hidden",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  thumb: {
    width: 96,
    aspectRatio: 1,
    borderRadius: 0,
  },
  body: {
    flex: 1,
    padding: 12,
    gap: 8,
  },
  titleLine: { height: 14, width: "85%" },
  titleLineShort: { height: 14, width: "60%" },
  summaryLine: { height: 11, width: "95%", marginTop: 2 },
  slugLine: { height: 11, width: "40%", marginTop: 6 },
  metaLine: { height: 11, width: "30%", marginTop: 6 },
  tagRow: { flexDirection: "row", gap: 6, marginTop: 4 },
  tag: { height: 16, width: 52, borderRadius: 6 },
  row: {
    backgroundColor: theme.surface,
    padding: 16,
    borderRadius: radii.lg,
    marginBottom: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
});
