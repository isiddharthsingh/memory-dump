import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import { useMemo } from "react";
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import type { SearchHit } from "../lib/api";
import { domainOf, topicTitle } from "../lib/format";
import { colors, radii, spacing, type } from "../lib/theme";
import { SourceThumb } from "./ItemRow";
import { ErrorNotice } from "./ui";

function sourceFromUrl(url: string | null | undefined): string {
  const host = url ? domainOf(url) : "";
  if (/(^|\.)youtube\.com$|^youtu\.be$/.test(host)) return "youtube";
  if (/(^|\.)(x|twitter)\.com$/.test(host)) return "twitter";
  return "web";
}

/** One row per item or wiki page; the retriever can return several chunks of each. */
function dedupe(hits: SearchHit[]): SearchHit[] {
  const seen = new Set<string>();
  return hits.filter((h) => (seen.has(h.id) ? false : (seen.add(h.id), true)));
}

export function SearchResults({
  query,
  hits,
  isPending,
  error,
  bottomSpace,
}: {
  query: string;
  hits: SearchHit[];
  isPending: boolean;
  error: unknown;
  bottomSpace: number;
}) {
  const router = useRouter();
  const rows = useMemo(() => dedupe(hits), [hits]);

  if (isPending && rows.length === 0) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.ink2} />
      </View>
    );
  }
  if (error) {
    return (
      <ErrorNotice
        title="Search isn’t working right now"
        detail={(error as Error).message}
        style={styles.notice}
      />
    );
  }
  if (rows.length === 0) {
    return (
      <View style={styles.center}>
        <Text style={styles.empty}>Nothing saved matches “{query}”.</Text>
      </View>
    );
  }

  return (
    <FlatList
      data={rows}
      keyExtractor={(h) => `${h.kind}:${h.id}`}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="on-drag"
      contentContainerStyle={{ paddingBottom: bottomSpace }}
      renderItem={({ item: hit, index }) => {
        const isWiki = hit.kind === "topic_page";
        const title = isWiki ? topicTitle(hit.title ?? hit.slug ?? "") : hit.title || (hit.url ? domainOf(hit.url) : "Untitled");
        const meta = isWiki ? "Wiki" : hit.url ? domainOf(hit.url) : "";
        return (
          <Pressable
            onPress={() => {
              void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
              if (isWiki && hit.slug) router.push({ pathname: "/wiki/[slug]", params: { slug: hit.slug } });
              else router.push({ pathname: "/item/[id]", params: { id: hit.id } });
            }}
            style={({ pressed }) => [styles.row, pressed && { backgroundColor: colors.surfaceMuted }]}
            accessibilityRole="button"
            accessibilityLabel={`${title}, ${meta}`}
          >
            {isWiki ? (
              <View style={styles.wikiThumb}>
                <Ionicons name="book-outline" size={20} color={colors.accent} />
              </View>
            ) : (
              <SourceThumb source={sourceFromUrl(hit.url)} url={hit.url} size={44} />
            )}
            <View style={[styles.text, index < rows.length - 1 && styles.separator]}>
              <Text style={styles.title} numberOfLines={2}>
                {title}
              </Text>
              {hit.snippet ? (
                <Text style={styles.snippet} numberOfLines={2}>
                  {hit.snippet}
                </Text>
              ) : null}
              <Text style={styles.meta}>{meta}</Text>
            </View>
          </Pressable>
        );
      }}
    />
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xxl },
  empty: { ...type.callout, color: colors.ink2, textAlign: "center" },
  notice: { margin: spacing.lg },
  row: { flexDirection: "row", gap: 14, paddingLeft: spacing.lg, paddingTop: spacing.md },
  wikiThumb: {
    width: 44,
    height: 44,
    borderRadius: radii.sm,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  text: { flex: 1, gap: 3, paddingRight: spacing.lg, paddingBottom: spacing.md },
  separator: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.hairlineStrong },
  title: { ...type.headline, color: colors.ink },
  snippet: { ...type.subhead, color: colors.ink2 },
  meta: { ...type.footnote, color: colors.ink3 },
});
