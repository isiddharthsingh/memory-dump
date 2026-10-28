import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useMemo } from "react";
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import Markdown, { type RenderRules } from "react-native-markdown-display";

import type { Citation } from "../lib/api";
import { domainOf, friendlyAskError, topicTitle } from "../lib/format";
import { answerMarkdown } from "../lib/readingStyles";
import { colors, fonts, pressedOpacity, radii, spacing, type } from "../lib/theme";
import { ErrorNotice } from "./ui";

export type Turn = {
  id: number;
  question: string;
  answer: string;
  citations: Citation[];
  status: "streaming" | "done" | "error";
  error?: string;
};

const MARKER_RE = /\s*\[(item|wiki):([^\]]+)\]/g;

function citationKey(c: Citation): string {
  return c.kind === "item" ? `item:${c.id}` : `wiki:${c.slug ?? c.id}`;
}

/** Number sources in the order the answer first cites them; uncited ones go last. */
function numberSources(answer: string, citations: Citation[]) {
  const order = new Map<string, number>();
  for (const m of answer.matchAll(MARKER_RE)) {
    const key = `${m[1]}:${m[2]}`;
    if (!order.has(key)) order.set(key, order.size + 1);
  }
  for (const c of citations) {
    const key = citationKey(c);
    if (!order.has(key)) order.set(key, order.size + 1);
  }
  const byKey = new Map(citations.map((c) => [citationKey(c), c]));
  const sources = [...order.entries()]
    .map(([key, n]) => ({ n, key, citation: byKey.get(key) }))
    .filter((s): s is { n: number; key: string; citation: Citation } => !!s.citation);
  return { order, sources };
}

const CITE_PREFIX = "memorydump://cite/";

/** Answers are markdown; turn [item:…]/[wiki:…] markers into numbered links. */
function toMarkdown(answer: string, order: Map<string, number>): string {
  return answer.replace(MARKER_RE, (_, kind: string, ref: string) => {
    const n = order.get(`${kind}:${ref}`);
    return n ? ` [${n}](${CITE_PREFIX}${kind}:${ref})` : "";
  });
}

const rules: RenderRules = {
  link: (node, children, _parent, mdStyles, onLinkPress) => {
    const href = String(node.attributes.href ?? "");
    if (href.startsWith(CITE_PREFIX)) {
      // Draw the number ourselves so it doesn't inherit the link underline.
      const n = node.children[0]?.content ?? "";
      return (
        <Text
          key={node.key}
          style={styles.cite}
          onPress={() => onLinkPress?.(href)}
          accessibilityRole="link"
          accessibilityLabel={`Source ${n}`}
        >
          {` ${n} `}
        </Text>
      );
    }
    return (
      <Text key={node.key} style={mdStyles.link} onPress={() => onLinkPress?.(href)} accessibilityRole="link">
        {children}
      </Text>
    );
  },
};

export function AskTurn({ turn, onRetry }: { turn: Turn; onRetry: () => void }) {
  const router = useRouter();
  const { order, sources } = useMemo(() => numberSources(turn.answer, turn.citations), [turn.answer, turn.citations]);
  const markdown = useMemo(() => toMarkdown(turn.answer, order), [turn.answer, order]);

  const open = (key: string) => {
    const [kind, ref] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
    if (kind === "item") router.push({ pathname: "/item/[id]", params: { id: ref } });
    else router.push({ pathname: "/wiki/[slug]", params: { slug: ref } });
  };

  const onLink = (url: string) => {
    if (url.startsWith(CITE_PREFIX)) open(url.slice(CITE_PREFIX.length));
    else Linking.openURL(url).catch(() => undefined);
    return false;
  };

  const error = turn.status === "error" ? friendlyAskError(turn.error ?? "") : null;

  return (
    <View style={styles.turn}>
      <Text style={styles.question} accessibilityRole="header">
        {turn.question}
      </Text>

      {sources.length > 0 ? (
        <View style={styles.sources}>
          <Text style={styles.label}>
            {sources.length} source{sources.length === 1 ? "" : "s"} from your library
          </Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sourceRow}>
            {sources.map(({ n, key, citation }) => {
              const isWiki = citation.kind === "topic_page";
              const where = isWiki ? "Wiki" : citation.url ? domainOf(citation.url) : "Saved item";
              const title = isWiki ? topicTitle(citation.title ?? citation.slug ?? "") : citation.title ?? where;
              return (
                <Pressable
                  key={key}
                  onPress={() => open(key)}
                  style={({ pressed }) => [styles.sourceCard, pressed && { opacity: pressedOpacity }]}
                  accessibilityRole="button"
                  accessibilityLabel={`Source ${n}: ${title}, ${where}`}
                >
                  <View style={styles.sourceTop}>
                    <View style={styles.badge}>
                      <Text style={styles.badgeText}>{n}</Text>
                    </View>
                    <Text style={styles.sourceWhere} numberOfLines={1}>
                      {where}
                    </Text>
                  </View>
                  <Text style={styles.sourceTitle} numberOfLines={2}>
                    {title}
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      ) : null}

      {error ? (
        <ErrorNotice title={error.title} detail={error.detail} onRetry={onRetry} />
      ) : (
        <View style={styles.answer}>
          <View style={styles.answerHeader}>
            <Ionicons name="sparkles" size={15} color={colors.accent} />
            <Text style={[styles.label, { color: colors.accent }]}>Answer</Text>
          </View>
          {!turn.answer.trim() && turn.status === "streaming" ? (
            <View style={styles.thinking}>
              <ActivityIndicator size="small" color={colors.ink3} />
              <Text style={styles.thinkingText}>Searching your library…</Text>
            </View>
          ) : (
            <Markdown style={answerMarkdown} rules={rules} onLinkPress={onLink}>
              {markdown}
            </Markdown>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  turn: { gap: spacing.lg, paddingBottom: spacing.xl },
  question: { fontFamily: fonts.serifSemibold, fontSize: 26, lineHeight: 32, color: colors.ink, letterSpacing: -0.3 },
  label: { ...type.footnote, fontWeight: "600", color: colors.ink2 },
  sources: { gap: spacing.sm },
  sourceRow: { gap: spacing.sm },
  sourceCard: {
    width: 164,
    padding: spacing.md,
    borderRadius: radii.lg,
    backgroundColor: colors.surfaceMuted,
    gap: 6,
  },
  sourceTop: { flexDirection: "row", alignItems: "center", gap: 6 },
  badge: {
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeText: { fontSize: 11, fontWeight: "700", color: colors.accent },
  sourceWhere: { ...type.caption, fontWeight: "400", color: colors.ink2, flexShrink: 1 },
  sourceTitle: { fontSize: 13, lineHeight: 17, fontWeight: "600", color: colors.ink },
  answer: { gap: spacing.md },
  answerHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  cite: { fontSize: 12, fontWeight: "700", color: colors.accent, backgroundColor: colors.accentSoft },
  thinking: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  thinkingText: { ...type.callout, color: colors.ink3 },
});
