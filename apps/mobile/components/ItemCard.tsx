import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { api, type ItemSummary } from "../lib/api";
import { TOUCH_TARGET, cardShadow, radii, theme } from "../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const SOURCE_ICON: Record<string, { name: IoniconName; color: string }> = {
  youtube: { name: "logo-youtube", color: theme.text },
  twitter: { name: "logo-twitter", color: theme.text },
  instagram: { name: "logo-instagram", color: theme.text },
  web: { name: "link", color: theme.textMuted },
};

const WEB_ICON: { name: IoniconName; color: string } = {
  name: "link",
  color: theme.textMuted,
};

function SourceIcon({ source, size }: { source: string; size: number }) {
  const def = SOURCE_ICON[source] ?? WEB_ICON;
  return <Ionicons name={def.name} size={size} color={def.color} />;
}

function StatusPill({ status, itemId }: { status: string; itemId: string }) {
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.retryItem(itemId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item", itemId] });
    },
  });

  if (status === "ready") return null;
  const color =
    status === "failed"
      ? theme.danger
      : status === "processing"
        ? theme.accent
        : theme.textMuted;
  const bg =
    status === "failed"
      ? theme.dangerSoft
      : status === "processing"
        ? theme.accentSoft
        : theme.surfaceSunken;

  if (status === "failed") {
    return (
      <Pressable
        onPress={(e) => {
          e.stopPropagation();
          if (!retry.isPending) retry.mutate();
        }}
        style={({ pressed }) => [
          styles.pill,
          styles.pillFailed,
          { borderColor: color, backgroundColor: bg },
          pressed && { opacity: 0.6 },
        ]}
        hitSlop={{ top: 14, bottom: 14, left: 10, right: 10 }}
        accessibilityRole="button"
        accessibilityLabel="Retry failed ingest"
      >
        <Text style={[styles.pillText, { color }]}>
          {retry.isPending ? "retrying" : "failed"}
        </Text>
        {!retry.isPending ? (
          <Ionicons name="refresh" size={12} color={color} />
        ) : null}
      </Pressable>
    );
  }

  return (
    <View style={[styles.pill, { borderColor: color, backgroundColor: bg }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  );
}

export function ItemCard({
  item,
  onLongPress,
}: {
  item: ItemSummary;
  onLongPress?: (item: ItemSummary) => void;
}) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  return (
    <TouchableOpacity
      activeOpacity={0.85}
      style={styles.card}
      onPress={() => {
        void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        router.push({ pathname: "/item/[id]", params: { id: item.id } });
      }}
      onLongPress={
        onLongPress
          ? () => {
              void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
              onLongPress(item);
            }
          : undefined
      }
      delayLongPress={350}
      accessibilityRole="button"
      accessibilityLabel={`Open ${item.title ?? item.url}`}
      accessibilityHint={
        onLongPress ? "Long-press to add a note" : undefined
      }
    >
      {item.thumbnail_url ? (
        <Image
          source={{ uri: item.thumbnail_url }}
          style={styles.thumb}
          contentFit="cover"
          transition={150}
        />
      ) : (
        <View style={[styles.thumb, styles.thumbPlaceholder]}>
          <SourceIcon source={item.source} size={32} />
        </View>
      )}
      <View style={styles.body}>
        <View style={styles.titleRow}>
          <View style={styles.sourceIconWrap}>
            <SourceIcon source={item.source} size={14} />
          </View>
          <Text style={styles.title} numberOfLines={2}>
            {item.title || item.url}
          </Text>
        </View>
        {item.summary ? (
          <Pressable
            onPress={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            hitSlop={4}
          >
            <Text
              style={styles.summary}
              numberOfLines={expanded ? undefined : 2}
            >
              {item.summary}
            </Text>
            {item.summary.length > 120 ? (
              <Text style={styles.expandHint}>
                {expanded ? "Show less" : "Show more"}
              </Text>
            ) : null}
          </Pressable>
        ) : null}
        <View style={styles.tagRow}>
          <StatusPill status={item.status} itemId={item.id} />
          {(item.tags ?? []).slice(0, 3).map((t) => (
            <View key={t} style={styles.tag}>
              <Text style={styles.tagText}>#{t}</Text>
            </View>
          ))}
        </View>
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    marginBottom: 12,
    overflow: "hidden",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    minHeight: TOUCH_TARGET * 2,
    ...cardShadow("sm"),
  },
  thumb: {
    width: 96,
    aspectRatio: 1,
    backgroundColor: theme.surfaceSunken,
  },
  thumbPlaceholder: {
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    flex: 1,
    padding: 12,
    gap: 6,
  },
  titleRow: {
    flexDirection: "row",
    gap: 6,
    alignItems: "flex-start",
  },
  sourceIconWrap: {
    marginTop: 2,
  },
  title: {
    color: theme.text,
    fontSize: 15,
    fontWeight: "600",
    flex: 1,
    letterSpacing: -0.2,
  },
  summary: {
    color: theme.textMuted,
    fontSize: 13,
    lineHeight: 18,
  },
  expandHint: {
    color: theme.accent,
    fontSize: 11,
    fontWeight: "600",
    marginTop: 2,
  },
  tagRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 2,
    alignItems: "center",
  },
  tag: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    backgroundColor: theme.surfaceSunken,
    borderRadius: radii.sm,
  },
  tagText: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "500",
  },
  pill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderWidth: 1,
    borderRadius: radii.sm,
  },
  pillFailed: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  pillText: {
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.3,
  },
});
