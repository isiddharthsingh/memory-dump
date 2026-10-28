import { Ionicons } from "@expo/vector-icons";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useMemo } from "react";
import { ActivityIndicator, Linking, ScrollView, StyleSheet, Text, View } from "react-native";
import Markdown, { type RenderRules } from "react-native-markdown-display";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { SourceThumb } from "../../components/ItemRow";
import { Chip, ErrorNotice, Eyebrow, IconButton, LinkRow } from "../../components/ui";
import { api } from "../../lib/api";
import { domainOf, plural, relativeTime, sourceMeta, topicTitle } from "../../lib/format";
import { readingMarkdown } from "../../lib/readingStyles";
import { colors, fonts, spacing, type } from "../../lib/theme";

const ITEM_MARKER = /\[item:([A-Za-z0-9-]{8,})\]/g;
const CITE_PREFIX = "memorydump://cite/";
const WIKI_PREFIX = "memorydump://wiki/";

/** Item ids in the order the page first cites them, then any uncited sources. */
function citationOrder(md: string, sourceIds: string[]): string[] {
  const order: string[] = [];
  for (const m of md.matchAll(ITEM_MARKER)) if (!order.includes(m[1]!)) order.push(m[1]!);
  for (const id of sourceIds) if (!order.includes(id)) order.push(id);
  return order;
}

/**
 * body_md has two custom tokens: [[slug]] wikilinks and [item:UUID]
 * citations. Turn them into markdown links with app URLs: wikilinks show the
 * topic's name, citations a number that matches the Sources list.
 */
function preprocess(md: string, title: string, order: string[], titleBySlug: Map<string, string>): string {
  const lines = md.trimStart().split("\n");
  // Pages usually open with "# <Title>", which the screen already shows.
  if (lines[0]?.startsWith("# ") && topicTitle(lines[0].slice(2).trim()).toLowerCase() === title.toLowerCase()) {
    lines.shift();
  }
  return lines
    .join("\n")
    .replace(/\[\[([a-z0-9][a-z0-9-]*)\]\]/g, (_, slug: string) => {
      const label = topicTitle(titleBySlug.get(slug) ?? slug).replace(/[[\]]/g, "");
      return `[${label}](${WIKI_PREFIX}${slug})`;
    })
    .replace(/\s*\[item:([A-Za-z0-9-]{8,})\]/g, (_, id: string) => `[${order.indexOf(id) + 1}](${CITE_PREFIX}${id})`);
}

const rules: RenderRules = {
  link: (node, children, _parent, styles, onLinkPress) => {
    const href = String(node.attributes.href ?? "");
    if (href.startsWith(CITE_PREFIX)) {
      // Draw the number ourselves: rendered children would inherit link underline.
      const n = node.children[0]?.content ?? "";
      return (
        <Text
          key={node.key}
          style={markdownExtra.cite}
          onPress={() => onLinkPress?.(href)}
          accessibilityRole="link"
          accessibilityLabel={`Source ${n}`}
        >
          {` ${n}`}
        </Text>
      );
    }
    return (
      <Text key={node.key} style={styles.link} onPress={() => onLinkPress?.(href)} accessibilityRole="link">
        {children}
      </Text>
    );
  },
};

export default function WikiTopic() {
  const { slug } = useLocalSearchParams<{ slug: string }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const page = useQuery({ queryKey: ["wiki", slug], queryFn: () => api.getWikiPage(slug!), enabled: !!slug });
  // Shares the list query so wikilinks and linked topics can show real titles.
  const pages = useQuery({ queryKey: ["wiki"], queryFn: () => api.listWiki() });
  const titleBySlug = useMemo(() => new Map((pages.data ?? []).map((p) => [p.slug, p.title])), [pages.data]);

  const order = useMemo(
    () => (page.data ? citationOrder(page.data.body_md, page.data.source_item_ids ?? []) : []),
    [page.data],
  );
  // Cached on the same key as the reader, so opening a source is instant.
  const sources = useQueries({
    queries: order.map((id) => ({
      queryKey: ["item", id] as const,
      queryFn: () => api.getItem(id),
      staleTime: 5 * 60 * 1000,
    })),
  });

  const title = page.data ? topicTitle(page.data.title) : "";
  const processed = useMemo(
    () => (page.data ? preprocess(page.data.body_md, title, order, titleBySlug) : ""),
    [page.data, title, order, titleBySlug],
  );

  if (page.isPending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.ink2} />
      </View>
    );
  }
  if (page.error || !page.data) {
    return (
      <View style={[styles.center, { padding: spacing.lg }]}>
        <ErrorNotice
          title="Couldn’t open this topic"
          detail={(page.error as Error | null)?.message ?? "It may have been merged into another page."}
          onRetry={() => void page.refetch()}
        />
      </View>
    );
  }

  const onLink = (url: string) => {
    if (url.startsWith(WIKI_PREFIX)) {
      router.push({ pathname: "/wiki/[slug]", params: { slug: url.slice(WIKI_PREFIX.length) } });
    } else if (url.startsWith(CITE_PREFIX)) {
      router.push({ pathname: "/item/[id]", params: { id: url.slice(CITE_PREFIX.length) } });
    } else {
      Linking.openURL(url).catch(() => undefined);
    }
    return false;
  };

  const backlinks = page.data.backlinks ?? [];
  const meta = `Written from ${plural(page.data.source_item_ids?.length ?? 0, "source")} · updated ${relativeTime(page.data.updated_at)}`;

  return (
    <>
      <Stack.Screen
        options={{
          headerRight: () => (
            <IconButton
              icon="sparkles-outline"
              color={colors.accent}
              accessibilityLabel="Ask about this topic"
              onPress={() => router.push({ pathname: "/ask", params: { q: `What do I know about ${title}?` } })}
            />
          ),
        }}
      />
      <ScrollView
        style={styles.container}
        contentInsetAdjustmentBehavior="automatic"
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + spacing.xxl }]}
      >
        <View style={styles.eyebrow}>
          <Ionicons name="book-outline" size={15} color={colors.ink2} />
          <Text style={styles.eyebrowText}>Wiki</Text>
        </View>
        <Text style={styles.title} accessibilityRole="header">
          {title}
        </Text>
        <Text style={styles.meta}>{meta}</Text>
        <View style={styles.rule} />

        <Markdown style={readingMarkdown} rules={rules} onLinkPress={onLink}>
          {processed}
        </Markdown>

        {order.length > 0 ? (
          <View style={styles.group}>
            <Eyebrow>Sources</Eyebrow>
            {order.map((id, i) => {
              const it = sources[i]?.data;
              return (
                <LinkRow
                  key={id}
                  leading={
                    <View style={styles.sourceLead}>
                      <Text style={styles.sourceNumber}>{i + 1}</Text>
                      <SourceThumb source={it?.source ?? "web"} url={it?.url} thumbnailUrl={it?.thumbnail_url} size={40} />
                    </View>
                  }
                  title={it?.title ?? (it ? domainOf(it.url) : "Loading…")}
                  subtitle={it ? sourceMeta(it.url, it.source) : null}
                  onPress={() => router.push({ pathname: "/item/[id]", params: { id } })}
                  last={i === order.length - 1}
                />
              );
            })}
          </View>
        ) : null}

        {backlinks.length > 0 ? (
          <View style={styles.group}>
            <Eyebrow>Linked topics</Eyebrow>
            <View style={styles.chips}>
              {backlinks.map((bl) => (
                <Chip
                  key={bl}
                  label={topicTitle(titleBySlug.get(bl) ?? bl)}
                  onPress={() => router.push({ pathname: "/wiki/[slug]", params: { slug: bl } })}
                />
              ))}
            </View>
          </View>
        ) : null}
      </ScrollView>
    </>
  );
}

const markdownExtra = StyleSheet.create({
  cite: { fontFamily: fonts.serifSemibold, fontSize: 14, color: colors.accent, textDecorationLine: "none" },
});

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.bg },
  scroll: { paddingHorizontal: spacing.xl, paddingTop: spacing.sm },
  eyebrow: { flexDirection: "row", alignItems: "center", gap: 6 },
  eyebrowText: { ...type.footnote, fontWeight: "600", color: colors.ink2 },
  title: { ...type.readingTitle, fontSize: 34, lineHeight: 40, color: colors.ink, marginTop: spacing.sm },
  meta: { ...type.subhead, color: colors.ink2, marginTop: spacing.sm },
  rule: { height: StyleSheet.hairlineWidth, backgroundColor: colors.hairlineStrong, marginVertical: spacing.lg },
  group: {
    marginTop: spacing.xl,
    paddingTop: spacing.lg,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairlineStrong,
    gap: spacing.xs,
  },
  sourceLead: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  sourceNumber: { width: 16, fontSize: 13, fontWeight: "700", color: colors.accent, textAlign: "center" },
  chips: { flexDirection: "row", flexWrap: "wrap", columnGap: spacing.sm },
});
