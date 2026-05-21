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
  StyleSheet,
  Text,
  View,
} from "react-native";

import { api } from "../../lib/api";
import { theme } from "../../lib/theme";

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
    <ScrollView contentContainerStyle={styles.scroll}>
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
              style={styles.retryBtn}
              disabled={retry.isPending}
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
              <Pressable style={styles.refRow}>
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
              <Pressable style={styles.refRow}>
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
          style={styles.openBtn}
          onPress={() => Linking.openURL(it.url)}
        >
          <Text style={styles.openBtnText}>Open original</Text>
        </Pressable>
        <Pressable
          style={styles.dangerBtn}
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
  if (status === "failed") return { borderColor: theme.danger };
  if (status === "processing") return { borderColor: theme.accent };
  return { borderColor: theme.textMuted };
}

const styles = StyleSheet.create({
  scroll: { padding: 16 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  errText: { color: theme.danger },
  thumb: {
    width: "100%",
    aspectRatio: 16 / 9,
    borderRadius: 12,
    marginBottom: 12,
    backgroundColor: theme.bgElev,
  },
  title: { color: theme.text, fontSize: 22, fontWeight: "700" },
  url: { color: theme.accent, marginTop: 4, fontSize: 12 },
  banner: {
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    marginTop: 16,
    gap: 8,
    backgroundColor: theme.bgElev,
  },
  bannerText: { color: theme.text, fontWeight: "600" },
  bannerError: { color: theme.danger, fontSize: 12 },
  retryBtn: {
    alignSelf: "flex-start",
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: theme.accent,
    borderRadius: 8,
  },
  retryText: { color: theme.text, fontWeight: "600" },
  section: {
    marginTop: 20,
    padding: 14,
    backgroundColor: theme.bgElev,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  sectionLabel: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  body: { color: theme.text, fontSize: 15, lineHeight: 21 },
  entityRow: { flexDirection: "row", flexWrap: "wrap", marginTop: 4 },
  entityLabel: { color: theme.textMuted, marginRight: 4, fontSize: 13 },
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
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  openBtnText: { color: theme.text, fontWeight: "700" },
  dangerBtn: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: theme.danger,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  dangerBtnText: { color: theme.danger, fontWeight: "700" },
  refRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
  },
  refKind: {
    color: theme.accent,
    fontSize: 11,
    fontWeight: "700",
    width: 48,
    textTransform: "uppercase",
  },
  refTitle: { color: theme.text, fontSize: 14, flex: 1 },
});
