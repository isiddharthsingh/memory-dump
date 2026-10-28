import { Platform, StyleSheet } from "react-native";

import { colors, fonts, radii, spacing, type } from "./theme";

const mono = Platform.select({ ios: "Menlo", default: "monospace" });

/**
 * One markdown style for everything you read: the item reader and wiki
 * pages. Serif body at a comfortable measure, serif headings, ink-blue links.
 */
export const readingMarkdown = StyleSheet.create({
  body: { ...type.readingBody, color: colors.inkReading },
  paragraph: { marginTop: 0, marginBottom: spacing.lg - 4 },
  heading1: { ...type.readingHeading, fontSize: 24, lineHeight: 30, color: colors.ink, marginTop: spacing.lg, marginBottom: spacing.sm },
  heading2: { ...type.readingHeading, color: colors.ink, marginTop: spacing.lg, marginBottom: spacing.sm },
  heading3: { fontFamily: fonts.serifSemibold, fontSize: 19, lineHeight: 25, color: colors.ink, marginTop: spacing.md, marginBottom: spacing.xs },
  strong: { fontFamily: fonts.serifSemibold, fontWeight: "normal" },
  link: { color: colors.accent, textDecorationLine: "underline" },
  blockquote: {
    backgroundColor: colors.surfaceMuted,
    borderLeftWidth: 0,
    borderRadius: radii.md,
    paddingHorizontal: spacing.lg - 4,
    paddingVertical: spacing.sm,
    marginVertical: spacing.sm,
  },
  code_inline: {
    backgroundColor: colors.fill,
    color: colors.ink,
    fontFamily: mono,
    fontSize: 15,
  },
  code_block: {
    backgroundColor: colors.fill,
    color: colors.ink,
    padding: spacing.md,
    borderRadius: radii.md,
    fontFamily: mono,
    fontSize: 14,
    lineHeight: 20,
  },
  fence: {
    backgroundColor: colors.fill,
    color: colors.ink,
    padding: spacing.md,
    borderRadius: radii.md,
    fontFamily: mono,
    fontSize: 14,
    lineHeight: 20,
    borderWidth: 0,
  },
  bullet_list: { marginVertical: spacing.xs },
  ordered_list: { marginVertical: spacing.xs },
  list_item: { marginBottom: spacing.xs },
  hr: { backgroundColor: colors.hairline, height: StyleSheet.hairlineWidth, marginVertical: spacing.lg - 4 },
});

/** Ask answers: same structure as reading, but in the UI sans at 17/26. */
export const answerMarkdown = StyleSheet.create({
  ...readingMarkdown,
  body: { fontSize: 17, lineHeight: 26, color: colors.inkReading },
  paragraph: { marginTop: 0, marginBottom: spacing.md },
  heading1: { ...type.section, color: colors.ink, marginTop: spacing.md, marginBottom: spacing.xs },
  heading2: { ...type.headline, fontSize: 18, color: colors.ink, marginTop: spacing.md, marginBottom: spacing.xs },
  heading3: { ...type.headline, color: colors.ink, marginTop: spacing.sm, marginBottom: spacing.xs },
  strong: { fontWeight: "600" },
});
