import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Image } from "expo-image";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useMemo, useState } from "react";
import { ActivityIndicator, Linking, Pressable, ScrollView, Share, StyleSheet, Text, View } from "react-native";
import Markdown from "react-native-markdown-display";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ItemConnections, ItemDetails } from "../../components/ItemSections";
import { SourceThumb, showItemActions } from "../../components/ItemRow";
import { NoteEditor } from "../../components/NoteEditor";
import { BottomFade, Button, ErrorNotice, IconButton, StatusBadge, Tag } from "../../components/ui";
import { api } from "../../lib/api";
import {
  domainOf,
  friendlyIngestError,
  humanizeTag,
  isUploadedFile,
  relativeTime,
  sourceMeta,
} from "../../lib/format";
import { parseReaderContent } from "../../lib/readerContent";
import { readingMarkdown } from "../../lib/readingStyles";
import { TOUCH_TARGET, colors, floatingShadow, pressedOpacity, radii, spacing, type } from "../../lib/theme";

const PREVIEW_MAX_CHARS = 1200;
const COLLAPSE_THRESHOLD = 1600;
const MAX_TAGS = 3;

/** Cut long articles at a paragraph or sentence boundary so the preview never ends mid-word. */
function previewOf(body: string): string | null {
  if (body.length <= COLLAPSE_THRESHOLD) return null;
  const slice = body.slice(0, PREVIEW_MAX_CHARS);
  const lastPara = slice.lastIndexOf("\n\n");
  if (lastPara > 400) return slice.slice(0, lastPara).trim();
  const lastSentence = slice.lastIndexOf(". ");
  if (lastSentence > 400) return slice.slice(0, lastSentence + 1).trim();
  return slice.trim() + "…";
}

export default function ItemReader() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const insets = useSafeAreaInsets();
  const [noteOpen, setNoteOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const item = useQuery({
    queryKey: ["item", id],
    queryFn: () => api.getItem(id!),
    enabled: !!id,
    refetchInterval: (q) =>
      q.state.data && ["pending", "processing"].includes(q.state.data.status) ? 2000 : false,
  });
  const related = useQuery({
    queryKey: ["item", id, "related"],
    queryFn: () => api.related(id!),
    enabled: !!id && item.data?.status === "ready",
  });
  const retry = useMutation({
    mutationFn: () => api.retryItem(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["item", id] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteItem(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      router.back();
    },
  });

  const reader = useMemo(() => parseReaderContent(item.data?.raw_text), [item.data?.raw_text]);
  // Extracted bodies often open with the page title, which is already the heading.
  const body = useMemo(() => {
    const t = item.data?.title?.trim();
    if (!reader.body || !t || !reader.body.startsWith(t)) return reader.body;
    return reader.body.slice(t.length).trimStart();
  }, [reader.body, item.data?.title]);
  const preview = useMemo(() => (body ? previewOf(body) : null), [body]);

  if (item.isPending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.ink2} />
      </View>
    );
  }
  if (item.error || !item.data) {
    return (
      <View style={[styles.center, { padding: spacing.lg }]}>
        <ErrorNotice
          title="Couldn’t open this item"
          detail={(item.error as Error | null)?.message ?? "It may have been deleted."}
          onRetry={() => void item.refetch()}
        />
      </View>
    );
  }

  const it = item.data;
  const domain = domainOf(it.url);
  const title = it.title || domain;
  const note = it.share_text || reader.note;
  const tags = it.tags ?? [];
  const author = it.people?.[0];
  const openOriginal = () => void Linking.openURL(it.url);
  const share = () => void Share.share({ url: it.url, message: it.title ? `${it.title}\n${it.url}` : it.url });

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          headerRight: () => (
            <IconButton
              icon="ellipsis-horizontal"
              accessibilityLabel="More actions"
              onPress={() =>
                showItemActions(it, { onNote: () => setNoteOpen(true), onDelete: () => remove.mutate() })
              }
            />
          ),
        }}
      />
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        contentContainerStyle={[styles.scroll, { paddingBottom: insets.bottom + 54 + spacing.xxl * 2 }]}
      >
        {it.thumbnail_url ? (
          <Image source={{ uri: it.thumbnail_url }} style={styles.hero} contentFit="cover" transition={150} />
        ) : null}

        <View style={styles.sourceLine}>
          <SourceThumb source={it.source} url={it.url} size={20} />
          <Text style={styles.sourceText} numberOfLines={1}>
            {sourceMeta(it.url, it.source)}
          </Text>
        </View>
        <Text style={styles.title} accessibilityRole="header">
          {title}
        </Text>
        <Text style={styles.byline}>
          {author ? (
            <>
              By <Text style={styles.bylineName}>{author}</Text> ·{" "}
            </>
          ) : null}
          Saved {relativeTime(it.created_at)}
        </Text>
        {tags.length > 0 ? (
          <View style={styles.tags}>
            {tags.slice(0, MAX_TAGS).map((t) => (
              <Tag key={t} label={humanizeTag(t)} />
            ))}
            {tags.length > MAX_TAGS ? <Tag label={`+${tags.length - MAX_TAGS}`} /> : null}
          </View>
        ) : null}

        {it.status === "failed" ? (
          isUploadedFile(it.url) ? (
            <ErrorNotice
              title={friendlyIngestError(it.error, it.url)}
              detail="The file isn’t kept after a failed save. Share it to Memory Dump again to try once more."
              style={styles.block}
            />
          ) : (
            <ErrorNotice
              title={friendlyIngestError(it.error, it.url)}
              detail={`We couldn’t fetch ${domain}. Check the link, or try again.`}
              onRetry={() => retry.mutate()}
              retrying={retry.isPending}
              style={styles.block}
            />
          )
        ) : it.status !== "ready" ? (
          <View style={[styles.block, styles.pending]}>
            <StatusBadge status={it.status} />
            <Text style={styles.pendingText}>Summarizing and filing this link. It’ll be ready in a moment.</Text>
          </View>
        ) : null}

        {note ? (
          <Pressable
            onPress={() => setNoteOpen(true)}
            style={({ pressed }) => [styles.block, styles.note, pressed && { opacity: pressedOpacity }]}
            accessibilityRole="button"
            accessibilityHint="Edit your note"
          >
            <Text style={styles.calloutLabel}>Your note</Text>
            <Text style={styles.noteText}>{note}</Text>
          </Pressable>
        ) : null}

        {it.summary ? (
          <View style={[styles.block, styles.summary]}>
            <View style={styles.calloutHeader}>
              <Ionicons name="sparkles-outline" size={15} color={colors.accent} />
              <Text style={[styles.calloutLabel, { color: colors.accent }]}>Summary</Text>
            </View>
            <Text style={styles.summaryText}>{it.summary}</Text>
          </View>
        ) : null}

        {it.status === "ready" ? (
          body && !reader.isStub ? (
            <View style={styles.article}>
              <Markdown style={readingMarkdown} onLinkPress={(url) => (Linking.openURL(url).catch(() => undefined), false)}>
                {preview && !expanded ? preview : body}
              </Markdown>
              {preview ? (
                <Button
                  label={expanded ? "Show less" : "Continue reading"}
                  variant="secondary"
                  size="md"
                  onPress={() => setExpanded((v) => !v)}
                  style={styles.continue}
                />
              ) : null}
            </View>
          ) : (
            <Text style={[styles.block, styles.stub]}>
              There’s no readable version of this page. It may be paywalled or behind a bot wall. Open the
              original to read it.
            </Text>
          )
        ) : null}

        {related.data ? <ItemConnections inWiki={related.data.in_wiki} related={related.data.related} /> : null}
        <ItemDetails item={it} />
      </ScrollView>

      <BottomFade height={Math.max(insets.bottom, spacing.lg) + 54 + 28} />
      <View style={[styles.toolbar, { bottom: Math.max(insets.bottom, spacing.lg) }]}>
        <Button label="Open original" icon="open-outline" onPress={openOriginal} style={styles.openButton} />
        <View style={styles.toolCapsule}>
          <IconButton icon="create-outline" accessibilityLabel="Add a note" onPress={() => setNoteOpen(true)} />
          <IconButton
            icon="sparkles-outline"
            color={colors.accent}
            accessibilityLabel="Ask about this item"
            onPress={() => router.push({ pathname: "/ask", params: { q: `What are the key ideas in “${title}”?` } })}
          />
          <IconButton icon="share-outline" accessibilityLabel="Share" onPress={share} />
        </View>
      </View>

      <NoteEditor
        visible={noteOpen}
        itemId={it.id}
        itemTitle={title}
        initialText={it.share_text ?? ""}
        onClose={() => setNoteOpen(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.bg },
  scroll: { paddingHorizontal: spacing.xl, paddingTop: spacing.sm },
  hero: { width: "100%", aspectRatio: 16 / 9, borderRadius: radii.xl, marginBottom: spacing.lg, backgroundColor: colors.fill },
  sourceLine: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  sourceText: { ...type.footnote, color: colors.ink2, flexShrink: 1 },
  title: { ...type.readingTitle, color: colors.ink, marginTop: spacing.md },
  byline: { ...type.callout, color: colors.ink2, marginTop: spacing.sm },
  bylineName: { color: colors.ink, fontWeight: "500" },
  tags: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: spacing.md },
  block: { marginTop: spacing.lg },
  pending: { gap: spacing.sm },
  pendingText: { ...type.callout, color: colors.ink2 },
  note: { padding: spacing.lg - 4, borderRadius: radii.xl, backgroundColor: colors.processingSoft, gap: spacing.xs },
  noteText: { ...type.callout, color: colors.ink },
  summary: { padding: spacing.lg - 4, borderRadius: radii.xl, backgroundColor: colors.surfaceMuted, gap: spacing.sm },
  calloutHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  calloutLabel: { ...type.footnote, fontWeight: "600", color: colors.ink2 },
  summaryText: { ...type.callout, color: colors.inkReading },
  article: { marginTop: spacing.xl },
  continue: { alignSelf: "flex-start" },
  stub: { ...type.callout, color: colors.ink2 },
  toolbar: { position: "absolute", left: spacing.lg, right: spacing.lg, flexDirection: "row", alignItems: "center", gap: 10 },
  openButton: { flex: 1, ...floatingShadow },
  toolCapsule: {
    flexDirection: "row",
    alignItems: "center",
    height: 54,
    paddingHorizontal: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.bg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairlineStrong,
    minWidth: TOUCH_TARGET * 3,
    ...floatingShadow,
  },
});
