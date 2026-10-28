import { Ionicons } from "@expo/vector-icons";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  SectionList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { EmptyState } from "../../components/EmptyState";
import { useTabBarSpace } from "../../components/FloatingTabBar";
import { ItemRow } from "../../components/ItemRow";
import { NoteEditor } from "../../components/NoteEditor";
import { Rediscover } from "../../components/Rediscover";
import { SearchResults } from "../../components/SearchResults";
import { ShareSheetMock } from "../../components/ShareSheetMock";
import { LibrarySkeletonList } from "../../components/Skeleton";
import { Chip, ErrorNotice, ScreenHeader, SectionHeader } from "../../components/ui";
import { useAuth } from "../../lib/AuthContext";
import { api, type ItemSummary } from "../../lib/api";
import { humanizeTag, initials, plural, sourceKind } from "../../lib/format";
import { TOUCH_TARGET, colors, pressedOpacity, radii, spacing, type } from "../../lib/theme";

const PAGE_SIZE = 25;
const POOL_SIZE = 200;
const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_MIN_CHARS = 2;
const MAX_TAG_CHIPS = 12;
const SOURCE_ORDER = ["web", "youtube", "twitter", "instagram", "reddit", "pdf", "image"];

type Filter = { kind: "all" } | { kind: "source"; value: string } | { kind: "tag"; value: string };

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function sectionLabel(created: Date, now: Date): string {
  const dayDiff = Math.floor((startOfDay(now) - startOfDay(created)) / 86_400_000);
  if (dayDiff <= 0) return "Today";
  if (dayDiff === 1) return "Yesterday";
  if (dayDiff < 7) return "This week";
  if (created.getFullYear() === now.getFullYear() && created.getMonth() === now.getMonth()) {
    return "This month";
  }
  return `${MONTHS[created.getMonth()]} ${created.getFullYear()}`;
}

function groupByDate(items: ItemSummary[]): { title: string; data: ItemSummary[] }[] {
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

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function sameFilter(a: Filter, b: Filter): boolean {
  if (a.kind === "all" || b.kind === "all") return a.kind === b.kind;
  return a.kind === b.kind && a.value === b.value;
}

export default function LibraryScreen() {
  const router = useRouter();
  const { session } = useAuth();
  const bottomSpace = useTabBarSpace();
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query.trim(), SEARCH_DEBOUNCE_MS);
  const searchActive = debouncedQuery.length >= SEARCH_MIN_CHARS;

  const items = useInfiniteQuery({
    queryKey: ["items", filter],
    queryFn: ({ pageParam }) =>
      api.listItems({
        limit: PAGE_SIZE,
        offset: pageParam as number,
        tag: filter.kind === "tag" ? filter.value : undefined,
        source: filter.kind === "source" ? filter.value : undefined,
      }),
    initialPageParam: 0,
    // A short page means there's nothing left to fetch.
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length < PAGE_SIZE ? undefined : allPages.length * PAGE_SIZE,
    // Keep polling while something is still being saved or analyzed, so rows
    // flip from "Analyzing…" to ready without a pull-to-refresh.
    refetchInterval: (q) =>
      (q.state.data?.pages ?? []).flat().some((i) => i.status === "pending" || i.status === "processing")
        ? 3000
        : false,
  });
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api.listTags() });
  // Ready items for Rediscover and the source chips, independent of paging.
  const pool = useQuery({
    queryKey: ["items-pool"],
    queryFn: () => api.listItems({ limit: POOL_SIZE, status: "ready" }),
    staleTime: 5 * 60 * 1000,
  });
  const search = useQuery({
    queryKey: ["search", debouncedQuery],
    queryFn: () => api.search(debouncedQuery, 25),
    enabled: searchActive,
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });

  useFocusEffect(
    useCallback(() => {
      void items.refetch();
    }, [items]),
  );

  const flatItems = useMemo(() => {
    const seen = new Set<string>();
    return (items.data?.pages ?? []).flat().filter((it) => (seen.has(it.id) ? false : (seen.add(it.id), true)));
  }, [items.data]);
  const sections = useMemo(() => groupByDate(flatItems), [flatItems]);

  const sources = useMemo(() => {
    const present = new Set((pool.data ?? []).map((i) => i.source));
    return SOURCE_ORDER.filter((s) => present.has(s));
  }, [pool.data]);

  const [editing, setEditing] = useState<{ id: string; title: string | null; initial: string } | null>(null);
  const openNoteEditor = useCallback(async (it: ItemSummary) => {
    try {
      const detail = await api.getItem(it.id);
      setEditing({ id: it.id, title: it.title, initial: detail.share_text ?? "" });
    } catch {
      setEditing({ id: it.id, title: it.title, initial: "" });
    }
  }, []);

  const [pulling, setPulling] = useState(false);
  const onPullRefresh = useCallback(async () => {
    setPulling(true);
    try {
      await Promise.all([items.refetch(), tags.refetch(), pool.refetch()]);
    } finally {
      setPulling(false);
    }
  }, [items, tags, pool]);

  const failedCount = flatItems.filter((i) => i.status === "failed").length;
  const subtitle =
    filter.kind === "all" && flatItems.length > 0
      ? `${items.hasNextPage ? `${flatItems.length}+` : flatItems.length} saved` +
        (failedCount > 0 ? ` · ${failedCount} need${failedCount === 1 ? "s" : ""} attention` : "")
      : null;

  const name = session?.user.display_name || session?.user.email || "";

  const chips = (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.chipRow}
      keyboardShouldPersistTaps="handled"
    >
      <Chip label="All" selected={filter.kind === "all"} onPress={() => setFilter({ kind: "all" })} />
      {sources.length > 1
        ? sources.map((s) => {
            const f: Filter = { kind: "source", value: s };
            const selected = sameFilter(filter, f);
            return (
              <Chip
                key={s}
                label={`${sourceKind(s).label}s`}
                selected={selected}
                onPress={() => setFilter(selected ? { kind: "all" } : f)}
              />
            );
          })
        : null}
      {(tags.data ?? []).slice(0, MAX_TAG_CHIPS).map((t) => {
        const f: Filter = { kind: "tag", value: t.tag };
        const selected = sameFilter(filter, f);
        return (
          <Chip
            key={t.tag}
            label={humanizeTag(t.tag)}
            count={t.count}
            selected={selected}
            onPress={() => setFilter(selected ? { kind: "all" } : f)}
          />
        );
      })}
    </ScrollView>
  );

  let body: React.ReactNode;
  if (searchActive) {
    body = (
      <SearchResults
        query={debouncedQuery}
        hits={search.data?.hits ?? []}
        isPending={search.isPending}
        error={search.error}
        bottomSpace={bottomSpace}
      />
    );
  } else if (items.isPending) {
    body = <LibrarySkeletonList />;
  } else if (items.error) {
    body = (
      <ErrorNotice
        title="Couldn’t load your library"
        detail={(items.error as Error).message}
        onRetry={() => void items.refetch()}
        retrying={items.isRefetching}
        style={styles.notice}
      />
    );
  } else if (flatItems.length === 0 && filter.kind === "all") {
    body = (
      <EmptyState
        icon="library-outline"
        title="Your library is empty"
        message="Share any link to Memory Dump from another app, or tap + to paste one."
      >
        <ShareSheetMock />
      </EmptyState>
    );
  } else {
    body = (
      <SectionList
        sections={sections}
        keyExtractor={(it) => it.id}
        renderItem={({ item, index, section }) => (
          <ItemRow item={item} onAddNote={openNoteEditor} last={index === section.data.length - 1} />
        )}
        renderSectionHeader={({ section }) => (
          <SectionHeader title={section.title} trailing={plural(section.data.length, "item")} />
        )}
        ListHeaderComponent={
          <>
            {chips}
            {filter.kind === "all" ? <Rediscover pool={pool.data ?? []} /> : null}
          </>
        }
        ListEmptyComponent={<Text style={styles.filterEmpty}>Nothing saved matches this filter.</Text>}
        stickySectionHeadersEnabled={false}
        keyboardDismissMode="on-drag"
        contentContainerStyle={{ paddingBottom: bottomSpace }}
        refreshControl={<RefreshControl refreshing={pulling} onRefresh={onPullRefresh} tintColor={colors.ink2} />}
        onEndReached={() => {
          if (items.hasNextPage && !items.isFetchingNextPage) void items.fetchNextPage();
        }}
        onEndReachedThreshold={0.4}
        ListFooterComponent={
          items.isFetchingNextPage ? (
            <ActivityIndicator style={styles.footer} color={colors.ink2} />
          ) : undefined
        }
      />
    );
  }

  return (
    <View style={styles.container}>
      <ScreenHeader
        title="Library"
        subtitle={subtitle}
        right={
          <Pressable
            onPress={() => router.push("/settings")}
            style={({ pressed }) => [styles.avatar, pressed && { opacity: pressedOpacity }]}
            accessibilityRole="button"
            accessibilityLabel="Account and settings"
          >
            <Text style={styles.avatarText}>{initials(name) || "?"}</Text>
          </Pressable>
        }
      />
      <View style={styles.searchBar}>
        <Ionicons name="search" size={18} color={colors.ink3} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search your library"
          placeholderTextColor={colors.ink3}
          value={query}
          onChangeText={setQuery}
          returnKeyType="search"
          autoCorrect={false}
          autoCapitalize="none"
          clearButtonMode="never"
          accessibilityLabel="Search your library"
        />
        {searchActive && search.isFetching ? <ActivityIndicator size="small" color={colors.ink3} /> : null}
        {query.length > 0 ? (
          <Pressable onPress={() => setQuery("")} hitSlop={12} accessibilityRole="button" accessibilityLabel="Clear search">
            <Ionicons name="close-circle" size={18} color={colors.ink3} />
          </Pressable>
        ) : null}
      </View>
      {body}
      <NoteEditor
        visible={editing !== null}
        itemId={editing?.id ?? null}
        itemTitle={editing?.title ?? null}
        initialText={editing?.initial ?? ""}
        onClose={() => setEditing(null)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  avatar: {
    width: TOUCH_TARGET,
    height: TOUCH_TARGET,
    borderRadius: TOUCH_TARGET / 2,
    backgroundColor: colors.ink,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: { ...type.callout, fontWeight: "600", color: colors.onInk },
  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    height: TOUCH_TARGET,
    marginHorizontal: spacing.lg,
    marginBottom: spacing.xs,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.fill,
  },
  searchInput: { ...type.body, flex: 1, color: colors.ink, paddingVertical: 0 },
  chipRow: { paddingHorizontal: spacing.lg, gap: spacing.sm, alignItems: "center" },
  notice: { margin: spacing.lg },
  filterEmpty: { ...type.callout, color: colors.ink2, textAlign: "center", padding: spacing.xxl },
  footer: { paddingVertical: spacing.lg },
});
