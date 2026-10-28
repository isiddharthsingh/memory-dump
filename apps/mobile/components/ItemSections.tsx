import { useRouter } from "expo-router";
import { StyleSheet, View } from "react-native";

import type { ItemDetail, ItemSummary, TopicPageSummary } from "../lib/api";
import { domainOf, humanizeTag, sourceMeta, topicTitle } from "../lib/format";
import { colors, spacing } from "../lib/theme";
import { SourceThumb } from "./ItemRow";
import { Eyebrow, LinkRow, Tag, WikiTile } from "./ui";

const MAX_RELATED = 5;

/** "In your wiki" and "Related" link lists under the reader. */
export function ItemConnections({
  inWiki,
  related,
}: {
  inWiki: TopicPageSummary[];
  related: ItemSummary[];
}) {
  const router = useRouter();
  const shownRelated = related.slice(0, MAX_RELATED);
  return (
    <>
      {inWiki.length > 0 ? (
        <View style={styles.group}>
          <Eyebrow>In your wiki</Eyebrow>
          {inWiki.map((tp, i) => (
            <LinkRow
              key={tp.id}
              leading={<WikiTile />}
              title={topicTitle(tp.title)}
              onPress={() => router.push({ pathname: "/wiki/[slug]", params: { slug: tp.slug } })}
              last={i === inWiki.length - 1}
            />
          ))}
        </View>
      ) : null}
      {shownRelated.length > 0 ? (
        <View style={styles.group}>
          <Eyebrow>Related</Eyebrow>
          {shownRelated.map((r, i) => (
            <LinkRow
              key={r.id}
              leading={<SourceThumb source={r.source} url={r.url} thumbnailUrl={r.thumbnail_url} size={40} />}
              title={r.title ?? domainOf(r.url)}
              subtitle={sourceMeta(r.url, r.source)}
              onPress={() => router.push({ pathname: "/item/[id]", params: { id: r.id } })}
              last={i === shownRelated.length - 1}
            />
          ))}
        </View>
      ) : null}
    </>
  );
}

function ChipGroup({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <View style={styles.chipGroup}>
      <Eyebrow>{label}</Eyebrow>
      <View style={styles.chips}>
        {values.map((v) => (
          <Tag key={v} label={v} />
        ))}
      </View>
    </View>
  );
}

/** What the analyzer pulled out, as readable chips instead of a raw dump. */
export function ItemDetails({ item }: { item: ItemDetail }) {
  const people = item.people ?? [];
  const mentions = [...(item.handles ?? []), ...(item.brands_products ?? [])];
  const topics = (item.topics ?? []).map(humanizeTag);
  if (people.length + mentions.length + topics.length === 0) return null;
  return (
    <View style={styles.group}>
      <ChipGroup label="People" values={people} />
      <ChipGroup label="Mentioned" values={mentions} />
      <ChipGroup label="Topics" values={topics} />
    </View>
  );
}

const styles = StyleSheet.create({
  group: {
    marginTop: spacing.xl,
    paddingTop: spacing.lg,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairlineStrong,
    gap: spacing.xs,
  },
  chipGroup: { gap: spacing.sm, marginBottom: spacing.md },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
});
