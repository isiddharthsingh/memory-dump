import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { api, type ItemSummary } from "../lib/api";
import { theme } from "../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const SOURCE_ICON: Record<string, { name: IoniconName; color: string }> = {
  youtube: { name: "logo-youtube", color: "#FF0033" },
  twitter: { name: "logo-twitter", color: "#1DA1F2" },
  instagram: { name: "logo-instagram", color: "#E4405F" },
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

  if (status === "failed") {
    return (
      <Pressable
        onPress={(e) => {
          e.stopPropagation();
          if (!retry.isPending) retry.mutate();
        }}
        style={[styles.pill, styles.pillFailed, { borderColor: color }]}
        hitSlop={6}
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
    <View style={[styles.pill, { borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  );
}

export function ItemCard({ item }: { item: ItemSummary }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  return (
    <TouchableOpacity
      activeOpacity={0.7}
      style={styles.card}
      onPress={() => router.push({ pathname: "/item/[id]", params: { id: item.id } })}
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
    backgroundColor: theme.bgElev,
    borderRadius: 12,
    marginBottom: 12,
    overflow: "hidden",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  thumb: {
    width: 96,
    aspectRatio: 1,
    backgroundColor: theme.bg,
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
  },
  summary: {
    color: theme.textMuted,
    fontSize: 13,
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
  },
  tag: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    backgroundColor: theme.bg,
    borderRadius: 6,
  },
  tagText: {
    color: theme.textMuted,
    fontSize: 11,
  },
  pill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderWidth: 1,
    borderRadius: 6,
  },
  pillFailed: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  pillText: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
  },
});
