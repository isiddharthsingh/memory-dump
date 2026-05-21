import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useRef, useState } from "react";
import {
  Alert,
  Dimensions,
  Pressable,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import ReanimatedSwipeable, {
  type SwipeableMethods,
} from "react-native-gesture-handler/ReanimatedSwipeable";
import Animated, {
  runOnJS,
  type SharedValue,
  useAnimatedReaction,
  useAnimatedStyle,
  useSharedValue,
} from "react-native-reanimated";

import { api, type ItemSummary } from "../lib/api";
import { TOUCH_TARGET, cardShadow, theme } from "../lib/theme";

const SCREEN_W = Dimensions.get("window").width;
const FULL_SWIPE_THRESHOLD = SCREEN_W * 0.5;

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const SOURCE_ICON: Record<string, { name: IoniconName; color: string }> = {
  youtube: { name: "logo-youtube", color: "#FF0033" },
  twitter: { name: "logo-twitter", color: "#1DA1F2" },
  instagram: { name: "logo-instagram", color: "#E4405F" },
  web: { name: "link", color: theme.textMuted },
};

const WEB_ICON: { name: IoniconName; color: string } = {
  name: "link",
  color: theme.textMuted,
};

function SourceIcon({ source, size }: { source: string; size: number }) {
  const def = SOURCE_ICON[source] ?? WEB_ICON;
  return <Ionicons name={def.name} size={size} color={def.color} />;
}

function StatusPill({ status, itemId }: { status: string; itemId: string }) {
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.retryItem(itemId),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item", itemId] });
    },
  });

  if (status === "ready") return null;
  const color =
    status === "failed"
      ? theme.danger
      : status === "processing"
        ? theme.accent
        : theme.textMuted;

  if (status === "failed") {
    return (
      <Pressable
        onPress={(e) => {
          e.stopPropagation();
          if (!retry.isPending) retry.mutate();
        }}
        style={({ pressed }) => [
          styles.pill,
          styles.pillFailed,
          { borderColor: color },
          pressed && { opacity: 0.6 },
        ]}
        hitSlop={{ top: 14, bottom: 14, left: 10, right: 10 }}
        accessibilityRole="button"
        accessibilityLabel="Retry failed ingest"
      >
        <Text style={[styles.pillText, { color }]}>
          {retry.isPending ? "retrying" : "failed"}
        </Text>
        {!retry.isPending ? (
          <Ionicons name="refresh" size={12} color={color} />
        ) : null}
      </Pressable>
    );
  }

  return (
    <View style={[styles.pill, { borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  );
}

/** Stretches with the swipe to fill the gap behind the card, and triggers
 * an immediate delete when the user keeps swiping past FULL_SWIPE_THRESHOLD
 * (iOS Mail pattern). */
function DeleteAction({
  drag,
  onFullSwipe,
  onPress,
}: {
  drag: SharedValue<number>;
  onFullSwipe: () => void;
  onPress: () => void;
}) {
  const fired = useSharedValue(false);

  useAnimatedReaction(
    () => drag.value,
    (cur) => {
      if (cur < -FULL_SWIPE_THRESHOLD && !fired.value) {
        fired.value = true;
        runOnJS(onFullSwipe)();
      } else if (cur > -FULL_SWIPE_THRESHOLD * 0.8 && fired.value) {
        fired.value = false;
      }
    },
  );

  // Width = how far the card has been dragged left, with an 88pt minimum so
  // the pill is always visible. Background brightens past the threshold.
  const animatedContainer = useAnimatedStyle(() => {
    const width = Math.max(88, -drag.value);
    const past = drag.value < -FULL_SWIPE_THRESHOLD;
    return {
      width,
      backgroundColor: past ? "#ff2244" : theme.danger,
    };
  });

  return (
    <Animated.View style={[styles.deleteActionWrap, animatedContainer]}>
      <Pressable
        onPress={onPress}
        style={styles.deleteHit}
        accessibilityRole="button"
        accessibilityLabel="Delete item"
      >
        <Ionicons name="trash" size={22} color={theme.text} />
        <Text style={styles.deleteText}>Delete</Text>
      </Pressable>
    </Animated.View>
  );
}

export function ItemCard({
  item,
  onLongPress,
}: {
  item: ItemSummary;
  onLongPress?: (item: ItemSummary) => void;
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const swipeRef = useRef<SwipeableMethods | null>(null);

  const remove = useMutation({
    mutationFn: () => api.deleteItem(item.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["tags"] });
    },
    onError: (err) => {
      Alert.alert("Delete failed", (err as Error).message);
      swipeRef.current?.close();
    },
  });

  const confirmDelete = () => {
    Alert.alert("Delete?", "This removes the item permanently.", [
      {
        text: "Cancel",
        style: "cancel",
        onPress: () => swipeRef.current?.close(),
      },
      {
        text: "Delete",
        style: "destructive",
        onPress: () => {
          void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          remove.mutate();
        },
      },
    ]);
  };

  const fullSwipeDelete = () => {
    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    swipeRef.current?.close();
    remove.mutate();
  };

  const renderRightActions = (
    _progress: SharedValue<number>,
    drag: SharedValue<number>,
  ) => (
    <DeleteAction
      drag={drag}
      onFullSwipe={fullSwipeDelete}
      onPress={confirmDelete}
    />
  );

  return (
    <ReanimatedSwipeable
      ref={swipeRef}
      renderRightActions={renderRightActions}
      friction={1}
      rightThreshold={40}
      overshootFriction={1}
      containerStyle={styles.swipeContainer}
    >
    <TouchableOpacity
      activeOpacity={0.7}
      style={styles.card}
      onPress={() => {
        void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        router.push({ pathname: "/item/[id]", params: { id: item.id } });
      }}
      onLongPress={
        onLongPress
          ? () => {
              void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
              onLongPress(item);
            }
          : undefined
      }
      delayLongPress={350}
      accessibilityRole="button"
      accessibilityLabel={`Open ${item.title ?? item.url}`}
      accessibilityHint={
        onLongPress
          ? "Long-press to add a note. Swipe left to delete."
          : "Swipe left to delete."
      }
    >
      {item.thumbnail_url ? (
        <Image
          source={{ uri: item.thumbnail_url }}
          style={styles.thumb}
          contentFit="cover"
          transition={150}
        />
      ) : (
        <View style={[styles.thumb, styles.thumbPlaceholder]}>
          <SourceIcon source={item.source} size={32} />
        </View>
      )}
      <View style={styles.body}>
        <View style={styles.titleRow}>
          <View style={styles.sourceIconWrap}>
            <SourceIcon source={item.source} size={14} />
          </View>
          <Text style={styles.title} numberOfLines={2}>
            {item.title || item.url}
          </Text>
        </View>
        {item.summary ? (
          <Pressable
            onPress={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            hitSlop={4}
          >
            <Text
              style={styles.summary}
              numberOfLines={expanded ? undefined : 2}
            >
              {item.summary}
            </Text>
            {item.summary.length > 120 ? (
              <Text style={styles.expandHint}>
                {expanded ? "Show less" : "Show more"}
              </Text>
            ) : null}
          </Pressable>
        ) : null}
        <View style={styles.tagRow}>
          <StatusPill status={item.status} itemId={item.id} />
          {(item.tags ?? []).slice(0, 3).map((t) => (
            <View key={t} style={styles.tag}>
              <Text style={styles.tagText}>#{t}</Text>
            </View>
          ))}
        </View>
      </View>
    </TouchableOpacity>
    </ReanimatedSwipeable>
  );
}

const styles = StyleSheet.create({
  swipeContainer: {
    marginBottom: 12,
    borderRadius: 12,
  },
  card: {
    flexDirection: "row",
    backgroundColor: theme.bgElev,
    borderRadius: 12,
    overflow: "hidden",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    minHeight: TOUCH_TARGET * 2,
    ...cardShadow("sm"),
  },
  deleteActionWrap: {
    justifyContent: "center",
    alignItems: "flex-end",
    borderTopRightRadius: 12,
    borderBottomRightRadius: 12,
    overflow: "hidden",
  },
  deleteHit: {
    width: 88,
    height: "100%",
    justifyContent: "center",
    alignItems: "center",
    gap: 4,
  },
  deleteText: {
    color: theme.text,
    fontSize: 12,
    fontWeight: "700",
  },
  thumb: {
    width: 96,
    aspectRatio: 1,
    backgroundColor: theme.bg,
  },
  thumbPlaceholder: {
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    flex: 1,
    padding: 12,
    gap: 6,
  },
  titleRow: {
    flexDirection: "row",
    gap: 6,
    alignItems: "flex-start",
  },
  sourceIconWrap: {
    marginTop: 2,
  },
  title: {
    color: theme.text,
    fontSize: 15,
    fontWeight: "600",
    flex: 1,
  },
  summary: {
    color: theme.textMuted,
    fontSize: 13,
  },
  expandHint: {
    color: theme.accent,
    fontSize: 11,
    fontWeight: "600",
    marginTop: 2,
  },
  tagRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 2,
  },
  tag: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    backgroundColor: theme.bg,
    borderRadius: 6,
  },
  tagText: {
    color: theme.textMuted,
    fontSize: 11,
  },
  pill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderWidth: 1,
    borderRadius: 6,
  },
  pillFailed: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  pillText: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
  },
});
