import { useQuery } from "@tanstack/react-query";
import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import {
  FlatList,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { EmptyState } from "../../components/EmptyState";
import { ItemCard } from "../../components/ItemCard";
import { InboxSkeletonList } from "../../components/Skeleton";
import { api } from "../../lib/api";
import { theme } from "../../lib/theme";

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
          message="Share a link from any app via the iOS or Android share sheet to save it here."
        />
      ) : (
        <FlatList
          data={items.data ?? []}
          keyExtractor={(it) => it.id}
          renderItem={({ item }) => <ItemCard item={item} />}
          contentContainerStyle={styles.list}
        />
      )}
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
      style={[
        styles.chip,
        { borderColor: active ? theme.accent : theme.border, backgroundColor: active ? theme.accent + "22" : theme.bgElev },
      ]}
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
  chipScroll: { maxHeight: 52, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: theme.border },
  chipRow: { paddingHorizontal: 12, paddingVertical: 8, gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
  },
  chipText: { fontSize: 13, fontWeight: "500" },
  list: { padding: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  errText: { color: theme.danger, fontWeight: "600" },
  errDetail: { color: theme.textMuted, marginTop: 8, textAlign: "center" },
});
