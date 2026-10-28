import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { ActionSheetIOS, Alert, Platform, Pressable, Share, StyleSheet, Text, View } from "react-native";

import { api, type ItemSummary } from "../lib/api";
import { domainOf, friendlyIngestError, isUploadedFile, pathOf, sourceKind, sourceMeta } from "../lib/format";
import { colors, fonts, radii, spacing, type } from "../lib/theme";
import { Button, StatusBadge } from "./ui";

/** Thumbnail when we have one, otherwise a quiet tile that names the source. */
export function SourceThumb({
  source,
  url,
  thumbnailUrl,
  failed = false,
  size = 56,
}: {
  source: string;
  url: string | null | undefined;
  thumbnailUrl?: string | null;
  failed?: boolean;
  size?: number;
}) {
  const box = { width: size, height: size, borderRadius: size >= 48 ? radii.md : radii.sm };
  if (thumbnailUrl && !failed) {
    return <Image source={{ uri: thumbnailUrl }} style={[styles.thumb, box]} contentFit="cover" transition={150} />;
  }
  if (failed) {
    return (
      <View style={[styles.thumb, box, { backgroundColor: colors.failedSoft }]}>
        <Ionicons name="link-outline" size={size * 0.4} color={colors.failed} />
      </View>
    );
  }
  const kind = sourceKind(source);
  if (source === "youtube" || source === "twitter") {
    return (
      <View style={[styles.thumb, box, { backgroundColor: colors.ink }]}>
        <Ionicons name={kind.icon} size={size * 0.36} color={colors.onInk} />
      </View>
    );
  }
  const letter = url ? domainOf(url).charAt(0).toUpperCase() : "";
  return (
    <View style={[styles.thumb, box, { backgroundColor: colors.fill }]}>
      {letter ? (
        <Text style={[styles.thumbLetter, { fontSize: size * 0.44 }]}>{letter}</Text>
      ) : (
        <Ionicons name={kind.icon} size={size * 0.4} color={colors.ink2} />
      )}
    </View>
  );
}

export function showItemActions(
  item: Pick<ItemSummary, "id" | "url" | "title">,
  handlers: { onNote: () => void; onDelete: () => void },
) {
  const share = () =>
    Share.share({ url: item.url, message: item.title ? `${item.title}\n${item.url}` : item.url });
  if (Platform.OS === "ios") {
    ActionSheetIOS.showActionSheetWithOptions(
      {
        title: item.title ?? domainOf(item.url),
        options: ["Add note", "Share", "Delete", "Cancel"],
        destructiveButtonIndex: 2,
        cancelButtonIndex: 3,
      },
      (i) => {
        if (i === 0) handlers.onNote();
        else if (i === 1) void share();
        else if (i === 2) handlers.onDelete();
      },
    );
    return;
  }
  Alert.alert(item.title ?? domainOf(item.url), undefined, [
    { text: "Add note", onPress: handlers.onNote },
    { text: "Share", onPress: () => void share() },
    { text: "Delete", style: "destructive", onPress: handlers.onDelete },
    { text: "Cancel", style: "cancel" },
  ]);
}

export function ItemRow({
  item,
  onAddNote,
  last = false,
}: {
  item: ItemSummary;
  onAddNote?: (item: ItemSummary) => void;
  last?: boolean;
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const failed = item.status === "failed";
  const busy = item.status === "pending" || item.status === "processing";
  const domain = domainOf(item.url);

  const retry = useMutation({
    mutationFn: () => api.retryItem(item.id),
    onSettled: () => qc.invalidateQueries({ queryKey: ["items"] }),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteItem(item.id),
    onSuccess: () => {
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });

  const title = item.title || domain;
  const meta = item.title
    ? sourceMeta(item.url, item.source)
    : [pathOf(item.url), "Link"].filter(Boolean).join(" · ");

  return (
    <Pressable
      onPress={() => {
        void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        router.push({ pathname: "/item/[id]", params: { id: item.id } });
      }}
      onLongPress={() => {
        void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        showItemActions(item, {
          onNote: () => onAddNote?.(item),
          onDelete: () => remove.mutate(),
        });
      }}
      delayLongPress={350}
      style={({ pressed }) => [styles.row, pressed && { backgroundColor: colors.surfaceMuted }]}
      accessibilityRole="button"
      accessibilityLabel={`${title}, ${meta}${failed ? ", failed" : ""}`}
      accessibilityHint="Opens the item. Long-press for more actions."
    >
      <SourceThumb source={item.source} url={item.url} thumbnailUrl={item.thumbnail_url} failed={failed} />
      <View style={[styles.text, !last && styles.separator]}>
        <Text style={styles.title} numberOfLines={2}>
          {title}
        </Text>
        {failed ? (
          <View style={styles.failedLine}>
            <View style={styles.failedDot} />
            <Text style={styles.failedText} numberOfLines={1}>
              {friendlyIngestError(item.error, item.url)}
            </Text>
          </View>
        ) : busy ? (
          <StatusBadge status={item.status} />
        ) : item.summary ? (
          <Text style={styles.summary} numberOfLines={1}>
            {item.summary}
          </Text>
        ) : null}
        <View style={styles.metaRow}>
          <Text style={styles.meta} numberOfLines={1}>
            {failed && isUploadedFile(item.url) ? `${meta} · Share it again to retry` : meta}
          </Text>
          {failed && !isUploadedFile(item.url) ? (
            <Button
              label="Retry"
              variant="secondary"
              size="sm"
              onPress={() => retry.mutate()}
              loading={retry.isPending}
              accessibilityLabel={`Retry saving ${title}`}
            />
          ) : null}
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 14,
    paddingLeft: spacing.lg,
    paddingTop: spacing.md,
  },
  thumb: { alignItems: "center", justifyContent: "center", overflow: "hidden" },
  thumbLetter: { fontFamily: fonts.serifMedium, color: colors.ink2 },
  text: { flex: 1, minWidth: 0, gap: 3, paddingRight: spacing.lg, paddingBottom: spacing.md },
  separator: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.hairlineStrong },
  title: { ...type.headline, color: colors.ink },
  summary: { ...type.subhead, color: colors.ink2 },
  failedLine: { flexDirection: "row", alignItems: "center", gap: 6 },
  failedDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.failed },
  failedText: { ...type.subhead, fontWeight: "500", color: colors.failed, flexShrink: 1 },
  metaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm },
  meta: { ...type.footnote, color: colors.ink3, flexShrink: 1 },
});
