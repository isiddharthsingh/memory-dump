import { Ionicons } from "@expo/vector-icons";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
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
import { TOUCH_TARGET, radii, theme } from "../../lib/theme";

const PAGE_SIZE = 25;
const SURPRISE_POOL_SIZE = 200;

/**
 * Pick one item with weight proportional to its age in days (plus 1 so
 * brand-new items still have a chance). This makes "Surprise me" mostly
 * surface older saves you've forgotten about, instead of regurgitating
 * what's already at the top of the inbox.
 */
function pickAgeWeighted(items: ItemSummary[]): ItemSummary | null {
  if (items.length === 0) return null;
  if (items.length === 1) return items[0] ?? null;
  const now = Date.now();
  const weights = items.map((it) => {
    const ageMs = now - new Date(it.created_at).getTime();
    return 1 + Math.max(0, Math.floor(ageMs / 86_400_000));
  });
  const total = weights.reduce((a, b) => a + b, 0);
  let r = Math.random() * total;
  for (let i = 0; i < items.length; i++) {
    r -= weights[i]!;
    if (r <= 0) return items[i] ?? null;
  }
  return items[items.length - 1] ?? null;
}

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
  const router = useRouter();
  const [selectedTag, setSelectedTag] = useState<string | null>(null);

  // Separate "pool" query for the Surprise button so it picks from up to
  // 200 ready items, not just whatever's been paged into the list yet.
  const surprisePool = useQuery({
    queryKey: ["items-pool"],
    queryFn: () => api.listItems({ limit: SURPRISE_POOL_SIZE, status: "ready" }),
    staleTime: 5 * 60 * 1000,
  });

  const onSurprise = useCallback(() => {
    const pool = surprisePool.data ?? [];
    if (pool.length === 0) return;
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const pick = pickAgeWeighted(pool);
    if (pick) {
      router.push({ pathname: "/item/[id]", params: { id: pick.id } });
    }
  }, [router, surprisePool.data]);

  const items = useInfiniteQuery({
    queryKey: ["items", { tag: selectedTag }],
    queryFn: ({ pageParam }) =>
      api.listItems({
        limit: PAGE_SIZE,
        offset: pageParam as number,
        tag: selectedTag ?? undefined,
      }),
    initialPageParam: 0,
    // Backend lists items in page-size chunks. We stop paging when we get a
    // short page (fewer rows than asked) — there's nothing left.
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length < PAGE_SIZE ? undefined : allPages.length * PAGE_SIZE,
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

  const flatItems = useMemo(
    () => (items.data?.pages ?? []).flat(),
    [items.data],
  );
  const sections = useMemo(() => groupByDate(flatItems), [flatItems]);

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

  const onEndReached = useCallback(() => {
    if (items.hasNextPage && !items.isFetchingNextPage) {
      items.fetchNextPage();
    }
  }, [items]);

  const poolReady = (surprisePool.data?.length ?? 0) > 0;

  return (
    <View style={styles.container}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.chipRow}
        style={styles.chipScroll}
      >
        <Pressable
          onPress={onSurprise}
          disabled={!poolReady}
          style={({ pressed }) => [
            styles.surpriseBtn,
            !poolReady && { opacity: 0.4 },
            pressed && { opacity: 0.7 },
          ]}
          accessibilityRole="button"
          accessibilityLabel="Surprise me with a random saved item"
          accessibilityState={{ disabled: !poolReady }}
        >
          <Ionicons name="sparkles" size={14} color={theme.accent} />
          <Text style={styles.surpriseText}>Surprise me</Text>
        </Pressable>
        {tags.data && tags.data.length > 0 ? (
          <>
            <View style={styles.chipDivider} />
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
          </>
        ) : null}
      </ScrollView>

      {items.isPending ? (
        <InboxSkeletonList />
      ) : items.error ? (
        <View style={styles.center}>
          <Text style={styles.errText}>
            Couldn't load items. Check Settings →
          </Text>
          <Text style={styles.errDetail}>{String((items.error as Error).message)}</Text>
        </View>
      ) : flatItems.length === 0 ? (
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
          onEndReached={onEndReached}
          onEndReachedThreshold={0.4}
          ListFooterComponent={
            items.isFetchingNextPage ? (
              <View style={styles.footer}>
                <ActivityIndicator color={theme.accent} />
              </View>
            ) : !items.hasNextPage && flatItems.length > PAGE_SIZE ? (
              <Text style={styles.footerEnd}>End of inbox</Text>
            ) : null
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
          backgroundColor: active ? theme.accentSoft : theme.surface,
        },
        pressed && { opacity: 0.7 },
      ]}
      accessibilityRole="button"
      accessibilityLabel={`Filter by ${label}`}
      accessibilityState={{ selected: !!active }}
    >
      <Text
        style={[
          styles.chipText,
          { color: active ? theme.accent : theme.text },
          active && { fontWeight: "700" },
        ]}
      >
        {label}
        {count != null ? ` · ${count}` : ""}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  chipScroll: {
    maxHeight: 60,
    backgroundColor: theme.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.border,
  },
  chipRow: { paddingHorizontal: 12, paddingVertical: 10, gap: 8, alignItems: "center" },
  surpriseBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 14,
    minHeight: 34,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: theme.accent,
    backgroundColor: theme.surface,
  },
  surpriseText: {
    color: theme.accent,
    fontSize: 13,
    fontWeight: "700",
    letterSpacing: -0.1,
  },
  chipDivider: {
    width: StyleSheet.hairlineWidth,
    height: 22,
    backgroundColor: theme.borderStrong,
    marginHorizontal: 4,
  },
  chip: {
    paddingHorizontal: 14,
    minHeight: 34,
    justifyContent: "center",
    borderRadius: radii.pill,
    borderWidth: 1,
  },
  chipText: { fontSize: 14, fontWeight: "500" },
  list: { padding: 12 },
  sectionHeader: {
    paddingTop: 8,
    paddingBottom: 6,
    paddingHorizontal: 4,
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
  footer: { paddingVertical: 16, alignItems: "center" },
  footerEnd: {
    color: theme.textFaint,
    fontSize: 12,
    textAlign: "center",
    paddingVertical: 16,
  },
});
