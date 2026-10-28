import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { ItemSummary } from "../lib/api";
import { daysAgo, domainOf, plural, sourceMeta } from "../lib/format";
import { TOUCH_TARGET, colors, pressedOpacity, radii, spacing, type } from "../lib/theme";

/** Deterministic 0..1 value from a string (FNV-1a), so the pick is pure. */
function seeded(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 0x1_0000_0000;
}

/**
 * Pick one item with weight proportional to its age in days (plus 1 so
 * brand-new items still have a chance). This mostly surfaces older saves
 * you've forgotten about instead of what's already at the top of the list.
 */
function pickAgeWeighted(items: ItemSummary[], r01: number, excludeId?: string): ItemSummary | null {
  const pool = items.length > 1 && excludeId ? items.filter((i) => i.id !== excludeId) : items;
  if (pool.length === 0) return null;
  const weights = pool.map((it) => 1 + daysAgo(it.created_at));
  let r = r01 * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < pool.length; i++) {
    r -= weights[i]!;
    if (r <= 0) return pool[i] ?? null;
  }
  return pool[pool.length - 1] ?? null;
}

function savedLabel(iso: string): string {
  const d = daysAgo(iso);
  if (d === 0) return "Saved today";
  if (d === 1) return "Saved yesterday";
  return `Saved ${plural(d, "day")} ago`;
}

export function Rediscover({ pool }: { pool: ItemSummary[] }) {
  const router = useRouter();
  // Each shuffle bumps `round` and remembers what was showing so it isn't
  // picked twice in a row; the pick itself is derived, not stored.
  const [shuffles, setShuffles] = useState<{ round: number; exclude?: string }>({ round: 0 });
  const poolKey = pool.map((i) => i.id).join(",");
  const pick = useMemo(
    () => pickAgeWeighted(pool, seeded(`${poolKey}#${shuffles.round}`), shuffles.exclude),
    [pool, poolKey, shuffles],
  );

  const shuffle = useCallback(() => {
    void Haptics.selectionAsync();
    setShuffles((s) => ({ round: s.round + 1, exclude: pick?.id }));
  }, [pick]);

  if (!pick || pool.length < 2) return null;

  return (
    <Pressable
      onPress={() => router.push({ pathname: "/item/[id]", params: { id: pick.id } })}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}
      accessibilityRole="button"
      accessibilityLabel={`Rediscover: ${pick.title ?? domainOf(pick.url)}`}
    >
      <View style={styles.text}>
        <Text style={styles.eyebrow}>Rediscover · {savedLabel(pick.created_at).toLowerCase()}</Text>
        <Text style={styles.title} numberOfLines={2}>
          {pick.title ?? domainOf(pick.url)}
        </Text>
        <Text style={styles.meta}>
          {sourceMeta(pick.url, pick.source)}
        </Text>
      </View>
      <Pressable
        onPress={shuffle}
        style={({ pressed }) => [styles.shuffle, pressed && { opacity: pressedOpacity }]}
        accessibilityRole="button"
        accessibilityLabel="Show another saved item"
      >
        <Ionicons name="shuffle" size={20} color={colors.accent} />
      </Pressable>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.sm,
    paddingVertical: 14,
    paddingLeft: spacing.lg - 4,
    paddingRight: spacing.sm,
    borderRadius: radii.xl,
    backgroundColor: colors.accentSoft,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  text: { flex: 1, gap: spacing.xs },
  eyebrow: { ...type.caption, fontWeight: "600", letterSpacing: 0.4, textTransform: "uppercase", color: colors.accent },
  title: { ...type.cardTitle, color: colors.ink },
  meta: { ...type.footnote, color: colors.ink2 },
  shuffle: {
    width: TOUCH_TARGET,
    height: TOUCH_TARGET,
    borderRadius: TOUCH_TARGET / 2,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
});
