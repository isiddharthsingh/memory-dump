import { Platform, TextStyle, ViewStyle } from "react-native";

/**
 * "Quiet Library" tokens. The UI stays neutral so saved content (thumbnails)
 * carries the color; one ink-blue accent marks links, citations and AI
 * output, and status colors are reserved for item state and destructive
 * actions. Every text color here passes WCAG AA on `bg`.
 */
export const colors = {
  bg: "#FFFFFF",
  // Grouped backgrounds (settings sheet) and input / chip fills.
  fill: "#F2F2F4",
  // Callouts that sit on `bg` (summary, source cards).
  surfaceMuted: "#F7F7F8",
  hairline: "#EDEDEF",
  hairlineStrong: "#DCDCE0",

  ink: "#0B0B0C",
  ink2: "#5C5C61",
  ink3: "#6E6E73",
  // Body copy in long-form reading; a touch softer than pure ink.
  inkReading: "#1C1C1E",
  onInk: "#FFFFFF",

  accent: "#2B4ACB",
  accentSoft: "#EEF1FC",

  processing: "#9A5B00",
  processingDot: "#D98A0B",
  processingSoft: "#FFF5E5",
  failed: "#C42B2B",
  failedStrong: "#8F1D1D",
  failedSoft: "#FDEDED",
  ready: "#1F7A47",

  scrim: "rgba(0,0,0,0.4)",
} as const;

/** Newsreader is for things you read; the system face is for things you do. */
export const fonts = {
  serif: "Newsreader_400Regular",
  serifMedium: "Newsreader_500Medium",
  serifSemibold: "Newsreader_600SemiBold",
} as const;

/** 4pt grid. Screen gutter is `lg` (20) everywhere. */
export const spacing = {
  xxs: 2,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 20,
  xl: 24,
  xxl: 32,
} as const;

export const radii = {
  sm: 8,
  md: 12,
  lg: 14,
  xl: 16,
  sheet: 22,
  pill: 999,
} as const;

/** Minimum interactive touch target (iOS HIG + Android a11y). */
export const TOUCH_TARGET = 44;

/** Height of the floating tab bar plus its bottom margin, for content insets. */
export const TAB_BAR_HEIGHT = 62;
export const TAB_BAR_GAP = 12;

export const type = {
  largeTitle: { fontSize: 34, lineHeight: 41, fontWeight: "700", letterSpacing: -0.4 },
  section: { fontSize: 20, lineHeight: 25, fontWeight: "700", letterSpacing: -0.3 },
  headline: { fontSize: 16, lineHeight: 21, fontWeight: "600", letterSpacing: -0.2 },
  body: { fontSize: 17, lineHeight: 24 },
  callout: { fontSize: 15, lineHeight: 21 },
  subhead: { fontSize: 14, lineHeight: 19 },
  footnote: { fontSize: 13, lineHeight: 18 },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: "500" },
  tabLabel: { fontSize: 10, lineHeight: 12, fontWeight: "600" },
  readingTitle: { fontFamily: fonts.serifSemibold, fontSize: 30, lineHeight: 36, letterSpacing: -0.3 },
  readingHeading: { fontFamily: fonts.serifSemibold, fontSize: 22, lineHeight: 28 },
  readingBody: { fontFamily: fonts.serif, fontSize: 18, lineHeight: 29 },
  cardTitle: { fontFamily: fonts.serifMedium, fontSize: 19, lineHeight: 24 },
} as const satisfies Record<string, TextStyle>;

/** Shadow for floating chrome only; list content never gets shadows. */
export const floatingShadow: ViewStyle = Platform.select<ViewStyle>({
  ios: {
    shadowColor: "#000000",
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 24,
  },
  android: { elevation: 8 },
  default: {},
})!;

export const pressedOpacity = 0.6;
