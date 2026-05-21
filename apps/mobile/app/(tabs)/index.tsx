import { useQuery } from "@tanstack/react-query";
import { useFocusEffect } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { EmptyState } from "../../components/EmptyState";
import { ItemCard } from "../../components/ItemCard";
import { NoteEditor } from "../../components/NoteEditor";
import { ShareSheetMock } from "../../components/ShareSheetMock";
import { InboxSkeletonList } from "../../components/Skeleton";
import { api, type ItemSummary } from "../../lib/api";
import { TOUCH_TARGET, theme } from "../../lib/theme";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function sectionLabel(created: Date, now: Date): string {
  const today = startOfDay(now);
  const dayDiff = Math.floor(
    (today.getTime() - startOfDay(created).getTime()) / 86_400_000,
  );
  if (dayDiff <= 0) return "Today";
  if (dayDiff === 1) return "Yesterday";
  if (dayDiff < 7) return "This week";
  if (
    created.getFullYear() === now.getFullYear() &&
    created.getMonth() === now.getMonth()
  ) {
    return "This month";
  }
  return `${MONTHS[created.getMonth()]} ${created.getFullYear()}`;
}

function groupByDate(
  items: ItemSummary[],
): { title: string; data: ItemSummary[] }[] {
  const now = new Date();
  const order: string[] = [];
  const buckets: Record<string, ItemSummary[]> = {};
  for (const item of items) {
    const label = sectionLabel(new Date(item.created_at), now);
    if (!buckets[label]) {
      buckets[label] = [];
      order.push(label);
    }
    buckets[label].push(item);
  }
  return order.map((title) => ({ title, data: buckets[title]! }));
}

export default function InboxScreen() {
  const [selectedTag, setSelectedTag] = useState<string | null>(null);

  const items = useQuery({
    queryKey: ["items", { tag: selectedTag }],
    queryFn: () => api.listItems({ limit: 50, tag: selectedTag ?? undefined }),
  });

  const tags = useQuery({
    queryKey: ["tags"],
    queryFn: () => api.listTags(),
  });

  useFocusEffect(
    useCallback(() => {
      items.refetch();
    }, [items]),
  );

  const sections = useMemo(() => groupByDate(items.data ?? []), [items.data]);

  const [editing, setEditing] = useState<{ id: string; initial: string } | null>(
    null,
  );
  const openNoteEditor = useCallback(async (it: ItemSummary) => {
    try {
      const detail = await api.getItem(it.id);
      setEditing({ id: it.id, initial: detail.share_text ?? "" });
    } catch {
      setEditing({ id: it.id, initial: "" });
    }
  }, []);

  // Manual pull-to-refresh state. Kept separate from `items.isRefetching`
  // because focus-triggered background refetches would otherwise flip the
  // spinner whenever the user merely tapped the list.
  const [pulling, setPulling] = useState(false);
  const onPullRefresh = useCallback(async () => {
    setPulling(true);
    try {
      await Promise.all([items.refetch(), tags.refetch()]);
    } finally {
      setPulling(false);
    }
  }, [items, tags]);

  return (
    <View style={styles.container}>
      {tags.data && tags.data.length > 0 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipRow}
          style={styles.chipScroll}
        >
          <Chip
            label="All"
            active={selectedTag === null}
            onPress={() => setSelectedTag(null)}
          />
          {tags.data.slice(0, 20).map((t) => (
            <Chip
              key={t.tag}
              label={`#${t.tag}`}
              count={t.count}
              active={selectedTag === t.tag}
              onPress={() =>
                setSelectedTag(selectedTag === t.tag ? null : t.tag)
              }
            />
          ))}
        </ScrollView>
      )}

      {items.isPending ? (
        <InboxSkeletonList />
      ) : items.error ? (
        <View style={styles.center}>
          <Text style={styles.errText}>
            Couldn't load items. Check Settings →
          </Text>
          <Text style={styles.errDetail}>{String((items.error as Error).message)}</Text>
        </View>
      ) : items.data && items.data.length === 0 ? (
        <EmptyState
          emoji="📥"
          title="Inbox is empty"
          message="Hit Share on any link, then tap memory-dump."
        >
          <ShareSheetMock />
        </EmptyState>
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(it) => it.id}
          renderItem={({ item }) => (
            <ItemCard item={item} onLongPress={openNoteEditor} />
          )}
          renderSectionHeader={({ section }) => (
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionHeaderText}>{section.title}</Text>
            </View>
          )}
          stickySectionHeadersEnabled={false}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={pulling}
              onRefresh={onPullRefresh}
              tintColor={theme.accent}
            />
          }
        />
      )}
      <NoteEditor
        visible={editing !== null}
        itemId={editing?.id ?? null}
        initialText={editing?.initial ?? ""}
        onClose={() => setEditing(null)}
      />
    </View>
  );
}

function Chip({
  label,
  count,
  active,
  onPress,
}: {
  label: string;
  count?: number;
  active?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.chip,
        {
          borderColor: active ? theme.accent : theme.border,
          backgroundColor: active ? theme.accent + "22" : theme.bgElev,
        },
        pressed && { opacity: 0.7 },
      ]}
      accessibilityRole="button"
      accessibilityLabel={`Filter by ${label}`}
      accessibilityState={{ selected: !!active }}
    >
      <Text style={[styles.chipText, { color: active ? theme.accent : theme.text }]}>
        {label}
        {count != null ? ` · ${count}` : ""}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  chipScroll: { maxHeight: 60, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: theme.border },
  chipRow: { paddingHorizontal: 12, paddingVertical: 8, gap: 8, alignItems: "center" },
  chip: {
    paddingHorizontal: 14,
    minHeight: 36,
    justifyContent: "center",
    borderRadius: 999,
    borderWidth: 1,
  },
  chipText: { fontSize: 14, fontWeight: "500" },
  list: { padding: 12 },
  sectionHeader: {
    paddingTop: 4,
    paddingBottom: 6,
  },
  sectionHeaderText: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  errText: { color: theme.danger, fontWeight: "600" },
  errDetail: { color: theme.textMuted, marginTop: 8, textAlign: "center" },
});
