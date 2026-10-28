import { useQuery } from "@tanstack/react-query";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, View } from "react-native";

import { EmptyState } from "../../components/EmptyState";
import { useTabBarSpace } from "../../components/FloatingTabBar";
import { WikiSkeletonList } from "../../components/Skeleton";
import { ErrorNotice, LinkRow, ScreenHeader, WikiTile } from "../../components/ui";
import { api } from "../../lib/api";
import { plural, relativeTime, topicTitle } from "../../lib/format";
import { colors, spacing } from "../../lib/theme";

export default function WikiScreen() {
  const router = useRouter();
  const bottomSpace = useTabBarSpace();
  const pages = useQuery({ queryKey: ["wiki"], queryFn: () => api.listWiki() });

  useFocusEffect(
    useCallback(() => {
      void pages.refetch();
    }, [pages]),
  );

  const [pulling, setPulling] = useState(false);
  const onPullRefresh = useCallback(async () => {
    setPulling(true);
    try {
      await pages.refetch();
    } finally {
      setPulling(false);
    }
  }, [pages]);

  const count = pages.data?.length ?? 0;

  let body: React.ReactNode;
  if (pages.isPending) {
    body = <WikiSkeletonList />;
  } else if (pages.error) {
    body = (
      <ErrorNotice
        title="Couldn’t load your wiki"
        detail={(pages.error as Error).message}
        onRetry={() => void pages.refetch()}
        retrying={pages.isRefetching}
        style={styles.notice}
      />
    );
  } else if (count === 0) {
    body = (
      <EmptyState
        icon="book-outline"
        title="Your wiki is empty"
        message="As you save links, topic pages are written and kept up to date here, each linking back to its sources."
      />
    );
  } else {
    body = (
      <FlatList
        data={pages.data ?? []}
        keyExtractor={(p) => p.id}
        contentContainerStyle={[styles.list, { paddingBottom: bottomSpace }]}
        refreshControl={<RefreshControl refreshing={pulling} onRefresh={onPullRefresh} tintColor={colors.ink2} />}
        renderItem={({ item, index }) => (
          <LinkRow
            leading={<WikiTile />}
            title={topicTitle(item.title)}
            subtitle={`${plural(item.source_item_ids?.length ?? 0, "source")} · updated ${relativeTime(item.updated_at)}`}
            onPress={() => router.push({ pathname: "/wiki/[slug]", params: { slug: item.slug } })}
            last={index === count - 1}
          />
        )}
      />
    );
  }

  return (
    <View style={styles.container}>
      <ScreenHeader
        title="Wiki"
        subtitle={count > 0 ? `${plural(count, "topic")}, written from your saves` : null}
      />
      {body}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  list: { paddingHorizontal: spacing.lg, paddingTop: spacing.xs },
  notice: { margin: spacing.lg },
});
