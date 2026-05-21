import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Image } from "expo-image";
import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { api } from "../../lib/api";
import { TOUCH_TARGET, cardShadow, radii, theme } from "../../lib/theme";

export default function ItemDetail() {
  const params = useLocalSearchParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();
  const qc = useQueryClient();
  const [showRaw, setShowRaw] = useState(false);

  const item = useQuery({
    queryKey: ["item", id],
    queryFn: () => api.getItem(id!),
    enabled: !!id,
    refetchInterval: (q) =>
      q.state.data && ["pending", "processing"].includes(q.state.data.status)
        ? 2000
        : false,
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

  if (item.isPending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.accent} />
      </View>
    );
  }

  if (item.error || !item.data) {
    return (
      <View style={styles.center}>
        <Text style={styles.errText}>{String((item.error as Error)?.message ?? "Not found")}</Text>
      </View>
    );
  }

  const it = item.data;

  return (
    <ScrollView
      contentContainerStyle={styles.scroll}
      contentInsetAdjustmentBehavior="automatic"
    >
      {it.thumbnail_url ? (
        <Image
          source={{ uri: it.thumbnail_url }}
          style={styles.thumb}
          contentFit="cover"
        />
      ) : null}

      <Text style={styles.title}>{it.title ?? it.url}</Text>
      <Text style={styles.url} onPress={() => Linking.openURL(it.url)}>
        {it.url}
      </Text>

      {it.status !== "ready" && (
        <View style={[styles.banner, statusColor(it.status)]}>
          <Text style={styles.bannerText}>Status: {it.status}</Text>
          {it.error ? <Text style={styles.bannerError}>{it.error}</Text> : null}
          {it.status === "failed" && (
            <Pressable
              onPress={() => retry.mutate()}
              style={({ pressed }) => [
                styles.retryBtn,
                (retry.isPending || pressed) && { opacity: 0.7 },
              ]}
              disabled={retry.isPending}
              accessibilityRole="button"
              accessibilityLabel="Retry ingest"
            >
              <Text style={styles.retryText}>
                {retry.isPending ? "Retrying…" : "Retry"}
              </Text>
            </Pressable>
          )}
        </View>
      )}

      {it.summary ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Summary</Text>
          <Text style={styles.body}>{it.summary}</Text>
        </View>
      ) : null}

      {(it.tags && it.tags.length > 0) ||
      (it.people && it.people.length > 0) ||
      (it.handles && it.handles.length > 0) ||
      (it.brands_products && it.brands_products.length > 0) ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Entities</Text>
          <EntityRow label="tags" items={it.tags} />
          <EntityRow label="people" items={it.people} />
          <EntityRow label="handles" items={it.handles} />
          <EntityRow label="brands" items={it.brands_products} />
          <EntityRow label="topics" items={it.topics} />
        </View>
      ) : null}

      {related.data && related.data.in_wiki.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Appears in wiki</Text>
          {related.data.in_wiki.map((tp) => (
            <Link
              key={tp.id}
              href={{ pathname: "/wiki/[slug]", params: { slug: tp.slug } }}
              asChild
            >
              <Pressable
                style={({ pressed }) => [
                  styles.refRow,
                  pressed && { opacity: 0.6 },
                ]}
              >
                <Text style={styles.refKind}>wiki</Text>
                <Text style={styles.refTitle} numberOfLines={2}>
                  {tp.title}
                </Text>
              </Pressable>
            </Link>
          ))}
        </View>
      ) : null}

      {related.data && related.data.related.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Related items</Text>
          {related.data.related.map((r) => (
            <Link
              key={r.id}
              href={{ pathname: "/item/[id]", params: { id: r.id } }}
              asChild
            >
              <Pressable
                style={({ pressed }) => [
                  styles.refRow,
                  pressed && { opacity: 0.6 },
                ]}
              >
                <Text style={styles.refKind}>{r.source}</Text>
                <Text style={styles.refTitle} numberOfLines={2}>
                  {r.title ?? r.url}
                </Text>
              </Pressable>
            </Link>
          ))}
        </View>
      ) : null}

      {it.raw_text ? (
        <View style={styles.section}>
          <Pressable onPress={() => setShowRaw((v) => !v)}>
            <Text style={styles.sectionLabel}>
              Raw content {showRaw ? "▾" : "▸"}
            </Text>
          </Pressable>
          {showRaw && <Text style={styles.raw}>{it.raw_text}</Text>}
        </View>
      ) : null}

      <View style={styles.actions}>
        <Pressable
          style={({ pressed }) => [
            styles.openBtn,
            pressed && { backgroundColor: theme.accentPressed },
          ]}
          onPress={() => Linking.openURL(it.url)}
          accessibilityRole="link"
          accessibilityLabel="Open original URL"
        >
          <Text style={styles.openBtnText}>Open original</Text>
        </Pressable>
        <Pressable
          style={({ pressed }) => [
            styles.shareBtn,
            pressed && { opacity: 0.6 },
          ]}
          onPress={() =>
            Share.share({
              url: it.url,
              message: it.title ? `${it.title}\n${it.url}` : it.url,
              title: it.title ?? undefined,
            })
          }
          accessibilityRole="button"
          accessibilityLabel="Share item"
        >
          <Text style={styles.shareBtnText}>Share</Text>
        </Pressable>
        <Pressable
          style={({ pressed }) => [
            styles.dangerBtn,
            pressed && { opacity: 0.6 },
          ]}
          onPress={() =>
            Alert.alert("Delete?", "This removes the item permanently.", [
              { text: "Cancel" },
              {
                text: "Delete",
                style: "destructive",
                onPress: () => remove.mutate(),
              },
            ])
          }
          disabled={remove.isPending}
          accessibilityRole="button"
          accessibilityLabel="Delete item"
        >
          <Text style={styles.dangerBtnText}>Delete</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

function EntityRow({ label, items }: { label: string; items: string[] | null }) {
  if (!items || items.length === 0) return null;
  return (
    <View style={styles.entityRow}>
      <Text style={styles.entityLabel}>{label}:</Text>
      <Text style={styles.entityValues}>{items.join(", ")}</Text>
    </View>
  );
}

function statusColor(status: string) {
  if (status === "failed") return { borderColor: theme.danger, backgroundColor: theme.dangerSoft };
  if (status === "processing") return { borderColor: theme.accent, backgroundColor: theme.accentSoft };
  return { borderColor: theme.border, backgroundColor: theme.surface };
}

const styles = StyleSheet.create({
  scroll: { padding: 16, paddingBottom: 48 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: theme.bg },
  errText: { color: theme.danger },
  thumb: {
    width: "100%",
    aspectRatio: 16 / 9,
    borderRadius: radii.lg,
    marginBottom: 14,
    backgroundColor: theme.surfaceSunken,
  },
  title: {
    color: theme.text,
    fontSize: 24,
    fontWeight: "700",
    letterSpacing: -0.5,
  },
  url: { color: theme.accent, marginTop: 4, fontSize: 13, fontWeight: "500" },
  banner: {
    borderWidth: 1,
    borderRadius: radii.md,
    padding: 12,
    marginTop: 16,
    gap: 8,
  },
  bannerText: { color: theme.text, fontWeight: "600" },
  bannerError: { color: theme.danger, fontSize: 12 },
  retryBtn: {
    alignSelf: "flex-start",
    paddingHorizontal: 16,
    minHeight: TOUCH_TARGET,
    justifyContent: "center",
    backgroundColor: theme.accent,
    borderRadius: radii.md,
  },
  retryText: { color: "#FFFFFF", fontWeight: "600", fontSize: 15 },
  section: {
    marginTop: 18,
    padding: 16,
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    ...cardShadow("sm"),
  },
  sectionLabel: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 8,
  },
  body: { color: theme.text, fontSize: 15, lineHeight: 22 },
  entityRow: { flexDirection: "row", flexWrap: "wrap", marginTop: 4 },
  entityLabel: { color: theme.textMuted, marginRight: 4, fontSize: 13, fontWeight: "600" },
  entityValues: { color: theme.text, fontSize: 13, flex: 1 },
  raw: {
    color: theme.textMuted,
    fontSize: 13,
    lineHeight: 19,
    marginTop: 8,
    fontFamily: "Menlo",
  },
  actions: { flexDirection: "row", gap: 8, marginTop: 24, marginBottom: 32 },
  openBtn: {
    flex: 1,
    backgroundColor: theme.accent,
    borderRadius: radii.md,
    paddingVertical: 14,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
    ...cardShadow("sm"),
  },
  openBtnText: { color: "#FFFFFF", fontWeight: "700", fontSize: 16 },
  shareBtn: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.accent,
    borderRadius: radii.md,
    paddingVertical: 14,
    paddingHorizontal: 16,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  shareBtnText: { color: theme.accent, fontWeight: "700", fontSize: 16 },
  dangerBtn: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.danger,
    borderRadius: radii.md,
    paddingVertical: 14,
    paddingHorizontal: 16,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    justifyContent: "center",
  },
  dangerBtnText: { color: theme.danger, fontWeight: "700", fontSize: 16 },
  refRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 12,
    minHeight: TOUCH_TARGET,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
  },
  refKind: {
    color: theme.accent,
    fontSize: 11,
    fontWeight: "700",
    width: 48,
    textTransform: "uppercase",
    letterSpacing: 0.3,
  },
  refTitle: { color: theme.text, fontSize: 14, flex: 1 },
});
