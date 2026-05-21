import { Platform, ViewStyle } from "react-native";

export const theme = {
  bg: "#0b0b0d",
  bgElev: "#15151a",
  border: "#26262d",
  text: "#f5f5f7",
  textMuted: "#a0a0a8",
  accent: "#7c5cff",
  accentPressed: "#6a4ce6",
  danger: "#ff5c7c",
  success: "#39c87a",
} as const;

/** 4px-base spacing grid per ui-mobile skill. */
export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
} as const;

/** Minimum interactive touch target (iOS HIG + Android a11y). */
export const TOUCH_TARGET = 44;

/** iOS-style type scale. */
export const type = {
  largeTitle: { fontSize: 34, fontWeight: "700" as const, letterSpacing: 0.37 },
  title: { fontSize: 22, fontWeight: "700" as const, letterSpacing: 0.35 },
  headline: { fontSize: 17, fontWeight: "600" as const, letterSpacing: -0.41 },
  body: { fontSize: 17, fontWeight: "400" as const, letterSpacing: -0.41, lineHeight: 22 },
  callout: { fontSize: 16, fontWeight: "400" as const, letterSpacing: -0.32 },
  footnote: { fontSize: 13, fontWeight: "400" as const },
  caption: { fontSize: 12, fontWeight: "400" as const },
} as const;

/** Platform-aware shadow helper. iOS uses shadow*, Android uses elevation. */
export const cardShadow = (intensity: "sm" | "md" = "sm"): ViewStyle =>
  Platform.select<ViewStyle>({
    ios: {
      shadowColor: "#000",
      shadowOffset: { width: 0, height: intensity === "md" ? 4 : 2 },
      shadowOpacity: intensity === "md" ? 0.18 : 0.1,
      shadowRadius: intensity === "md" ? 12 : 6,
    },
    android: { elevation: intensity === "md" ? 6 : 2 },
    default: {},
  })!;
