import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useMemo } from "react";
import {
  ActivityIndicator,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import Markdown from "react-native-markdown-display";

import { api } from "../../lib/api";
import { radii, theme } from "../../lib/theme";

/**
 * The body_md content has two custom token types:
 *   [[slug]]     → tappable wikilink to /wiki/[slug]
 *   [item:UUID]  → tappable item card link to /item/[id]
 *
 * We pre-process the markdown to convert them into standard markdown links
 * with custom URL schemes, then intercept onLinkPress to navigate.
 */
function preprocessMarkdown(md: string): string {
  return md
    .replace(/\[\[([a-z0-9][a-z0-9\-]*)\]\]/g, (_, slug) => `[${slug}](memorydump://wiki/${slug})`)
    .replace(/\[item:([A-Za-z0-9\-]{8,})\]/g, (_, id) => ` [→item](memorydump://item/${id})`);
}

export default function WikiDetail() {
  const params = useLocalSearchParams<{ slug: string }>();
  const slug = params.slug;
  const router = useRouter();

  const page = useQuery({
    queryKey: ["wiki", slug],
    queryFn: () => api.getWikiPage(slug!),
    enabled: !!slug,
  });

  const processed = useMemo(
    () => (page.data ? preprocessMarkdown(page.data.body_md) : ""),
    [page.data],
  );

  if (page.isPending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.accent} />
      </View>
    );
  }

  if (page.error || !page.data) {
    return (
      <View style={styles.center}>
        <Text style={styles.errText}>{String((page.error as Error)?.message ?? "Not found")}</Text>
      </View>
    );
  }

  const onLink = (url: string) => {
    if (url.startsWith("memorydump://wiki/")) {
      const targetSlug = url.replace("memorydump://wiki/", "");
      router.push({ pathname: "/wiki/[slug]", params: { slug: targetSlug } });
      return false;
    }
    if (url.startsWith("memorydump://item/")) {
      const id = url.replace("memorydump://item/", "");
      router.push({ pathname: "/item/[id]", params: { id } });
      return false;
    }
    Linking.openURL(url).catch(() => undefined);
    return false;
  };

  return (
    <ScrollView
      contentContainerStyle={styles.scroll}
      contentInsetAdjustmentBehavior="automatic"
    >
      <Text style={styles.title}>{page.data.title}</Text>
      <Text style={styles.meta}>
        {page.data.slug} · v{page.data.version} ·{" "}
        {page.data.source_item_ids?.length ?? 0} sources
      </Text>
      <View style={styles.divider} />
      <Markdown style={markdownStyles} onLinkPress={onLink} rules={rules}>
        {processed}
      </Markdown>
      {page.data.backlinks && page.data.backlinks.length > 0 ? (
        <View style={styles.backlinks}>
          <Text style={styles.sectionLabel}>Linked topics</Text>
          <View style={styles.chipRow}>
            {page.data.backlinks.map((bl) => (
              <Text
                key={bl}
                style={styles.chip}
                onPress={() =>
                  router.push({ pathname: "/wiki/[slug]", params: { slug: bl } })
                }
              >
                {bl}
              </Text>
            ))}
          </View>
        </View>
      ) : null}
    </ScrollView>
  );
}

const rules = {};

const markdownStyles = {
  body: { color: theme.text, fontSize: 16, lineHeight: 24 },
  heading1: { color: theme.text, fontSize: 24, fontWeight: "700", marginTop: 16, letterSpacing: -0.4 },
  heading2: { color: theme.text, fontSize: 20, fontWeight: "700", marginTop: 14, letterSpacing: -0.3 },
  heading3: { color: theme.text, fontSize: 17, fontWeight: "600", marginTop: 12 },
  link: { color: theme.accent, fontWeight: "500" },
  code_inline: {
    backgroundColor: theme.surfaceSunken,
    color: theme.accent,
    paddingHorizontal: 4,
    paddingVertical: 2,
    borderRadius: 4,
    fontFamily: "Menlo",
    fontSize: 14,
  },
  code_block: {
    backgroundColor: theme.surfaceSunken,
    color: theme.text,
    padding: 12,
    borderRadius: radii.md,
    fontFamily: "Menlo",
    fontSize: 13,
  },
  bullet_list: { marginVertical: 4 },
  hr: { backgroundColor: theme.border, height: 1, marginVertical: 12 },
} as const;

const styles = StyleSheet.create({
  scroll: { padding: 16, paddingBottom: 48 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: theme.bg },
  title: { color: theme.text, fontSize: 28, fontWeight: "700", letterSpacing: -0.5 },
  meta: { color: theme.textMuted, fontSize: 12, marginTop: 6 },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: theme.border, marginVertical: 16 },
  errText: { color: theme.danger },
  backlinks: { marginTop: 24 },
  sectionLabel: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: theme.accentSoft,
    color: theme.accent,
    borderRadius: radii.sm,
    fontSize: 13,
    fontWeight: "500",
    overflow: "hidden",
  },
});
