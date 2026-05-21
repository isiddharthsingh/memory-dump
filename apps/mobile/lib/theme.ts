import { Platform, ViewStyle } from "react-native";

/**
 * Monochrome palette — pure grayscale, no warm tints.
 * White surfaces, black text, neutral gray borders. Accent is true black.
 */
export const theme = {
  // Page / scene background.
  bg: "#FFFFFF",
  // Elevated surfaces (cards, sheets, headers).
  surface: "#FFFFFF",
  // Sunken / inset surfaces (input fills, code blocks, tag chips).
  surfaceSunken: "#F2F2F4",
  // Hairline dividers and unselected borders.
  border: "rgba(0, 0, 0, 0.08)",
  borderStrong: "rgba(0, 0, 0, 0.18)",

  // Primary text.
  text: "#000000",
  // Secondary text.
  textMuted: "#6B6B70",
  // Tertiary text.
  textFaint: "#A1A1A6",

  // Accent (links, primary buttons, focus rings). Pure black for monochrome.
  accent: "#000000",
  accentPressed: "#2B2B2E",
  // Tinted background for selected chips / pills.
  accentSoft: "rgba(0, 0, 0, 0.06)",

  // Status — kept in grayscale. Danger is darker so it reads as "heavy".
  danger: "#1C1C1E",
  dangerSoft: "rgba(0, 0, 0, 0.08)",
  success: "#000000",
  warning: "#6B6B70",

  // Tints for legacy glass material tokens — kept so any stray import still works.
  glassTint: "rgba(255, 255, 255, 0.72)",
  glassTintStrong: "rgba(255, 255, 255, 0.88)",

  // Backdrop scrim for modals.
  scrim: "rgba(0, 0, 0, 0.45)",
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

/** iOS-standard corner radii. */
export const radii = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  pill: 999,
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

/**
 * Platform-aware shadow helper. iOS uses shadow*, Android uses elevation.
 */
export const cardShadow = (intensity: "sm" | "md" | "lg" = "sm"): ViewStyle =>
  Platform.select<ViewStyle>({
    ios: {
      shadowColor: "#000000",
      shadowOffset: {
        width: 0,
        height: intensity === "lg" ? 12 : intensity === "md" ? 6 : 2,
      },
      shadowOpacity: intensity === "lg" ? 0.10 : intensity === "md" ? 0.08 : 0.04,
      shadowRadius: intensity === "lg" ? 24 : intensity === "md" ? 14 : 6,
    },
    android: {
      elevation: intensity === "lg" ? 10 : intensity === "md" ? 6 : 2,
    },
    default: {},
  })!;

/** Legacy glass token kept for any unmigrated imports. */
export const glassBlur = {
  tint: "systemUltraThinMaterialLight" as const,
  intensity: 60,
};
