import { useQuery } from "@tanstack/react-query";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback } from "react";
import {
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { EmptyState } from "../../components/EmptyState";
import { WikiSkeletonList } from "../../components/Skeleton";
import { api } from "../../lib/api";
import { cardShadow, theme } from "../../lib/theme";

export default function WikiScreen() {
  const router = useRouter();
  const pages = useQuery({
    queryKey: ["wiki"],
    queryFn: () => api.listWiki(),
  });

  useFocusEffect(
    useCallback(() => {
      pages.refetch();
    }, [pages]),
  );

  if (pages.isPending) {
    return <WikiSkeletonList />;
  }

  if (pages.error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errText}>Couldn't load wiki.</Text>
      </View>
    );
  }

  if (pages.data && pages.data.length === 0) {
    return (
      <EmptyState
        emoji="📚"
        title="Wiki is empty"
        message="As you save items, topic pages appear here. Each page is maintained automatically and links to the source items."
      />
    );
  }

  return (
    <FlatList
      data={pages.data ?? []}
      keyExtractor={(p) => p.id}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl
          refreshing={pages.isRefetching}
          onRefresh={() => pages.refetch()}
          tintColor={theme.accent}
        />
      }
      renderItem={({ item }) => (
        <TouchableOpacity
          activeOpacity={0.7}
          style={styles.row}
          onPress={() =>
            router.push({ pathname: "/wiki/[slug]", params: { slug: item.slug } })
          }
          accessibilityRole="button"
          accessibilityLabel={`Open wiki page ${item.title}`}
        >
          <Text style={styles.title}>{item.title}</Text>
          <Text style={styles.slug}>{item.slug}</Text>
          <Text style={styles.meta}>
            {item.source_item_ids?.length ?? 0} item(s) · v{item.version}
          </Text>
        </TouchableOpacity>
      )}
    />
  );
}

const styles = StyleSheet.create({
  list: { padding: 12 },
  row: {
    backgroundColor: theme.bgElev,
    padding: 16,
    borderRadius: 12,
    marginBottom: 10,
    minHeight: 72,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    ...cardShadow("sm"),
  },
  title: { color: theme.text, fontSize: 16, fontWeight: "600" },
  slug: { color: theme.accent, marginTop: 2, fontSize: 12 },
  meta: { color: theme.textMuted, fontSize: 12, marginTop: 4 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  errText: { color: theme.danger },
});
