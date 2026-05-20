import { Image } from "expo-image";
import { Link } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { ItemSummary } from "../lib/api";
import { theme } from "../lib/theme";

const SOURCE_ICON: Record<string, string> = {
  youtube: "▶️",
  twitter: "🐦",
  instagram: "📸",
  web: "🔗",
};

function StatusPill({ status }: { status: string }) {
  if (status === "ready") return null;
  const color =
    status === "failed"
      ? theme.danger
      : status === "processing"
        ? theme.accent
        : theme.textMuted;
  return (
    <View style={[styles.pill, { borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  );
}

export function ItemCard({ item }: { item: ItemSummary }) {
  return (
    <Link href={{ pathname: "/item/[id]", params: { id: item.id } }} asChild>
      <Pressable style={styles.card}>
        {item.thumbnail_url ? (
          <Image
            source={{ uri: item.thumbnail_url }}
            style={styles.thumb}
            contentFit="cover"
            transition={150}
          />
        ) : (
          <View style={[styles.thumb, styles.thumbPlaceholder]}>
            <Text style={styles.sourceIcon}>{SOURCE_ICON[item.source] ?? "🔗"}</Text>
          </View>
        )}
        <View style={styles.body}>
          <View style={styles.titleRow}>
            <Text style={styles.sourceIcon}>{SOURCE_ICON[item.source] ?? "🔗"}</Text>
            <Text style={styles.title} numberOfLines={2}>
              {item.title || item.url}
            </Text>
          </View>
          {item.summary ? (
            <Text style={styles.summary} numberOfLines={2}>
              {item.summary}
            </Text>
          ) : null}
          <View style={styles.tagRow}>
            <StatusPill status={item.status} />
            {(item.tags ?? []).slice(0, 3).map((t) => (
              <View key={t} style={styles.tag}>
                <Text style={styles.tagText}>#{t}</Text>
              </View>
            ))}
          </View>
        </View>
      </Pressable>
    </Link>
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
  sourceIcon: {
    fontSize: 16,
    marginTop: 1,
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
  pillText: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
  },
});
