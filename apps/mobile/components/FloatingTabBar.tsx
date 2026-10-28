import { Ionicons } from "@expo/vector-icons";
import type { BottomTabBarProps } from "expo-router/tabs";
import * as Haptics from "expo-haptics";
import { useEffect, useState } from "react";
import { Keyboard, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { TAB_BAR_GAP, TAB_BAR_HEIGHT, colors, floatingShadow, radii, type } from "../lib/theme";
import { SaveLinkSheet } from "./SaveLinkSheet";
import { BottomFade } from "./ui";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

const TABS: Record<string, { label: string; icon: IoniconName; iconActive: IoniconName }> = {
  index: { label: "Library", icon: "library-outline", iconActive: "library" },
  wiki: { label: "Wiki", icon: "book-outline", iconActive: "book" },
  ask: { label: "Ask", icon: "sparkles-outline", iconActive: "sparkles" },
};

/** Distance from the bottom of the screen to the top of the floating bar. */
export function useTabBarSpace(): number {
  const insets = useSafeAreaInsets();
  return tabBarBottom(insets.bottom) + TAB_BAR_HEIGHT + TAB_BAR_GAP;
}

function tabBarBottom(insetBottom: number): number {
  return insetBottom > 0 ? insetBottom - 6 : 16;
}

export function useKeyboardVisible(): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const showEvt = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvt = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const a = Keyboard.addListener(showEvt, () => setVisible(true));
    const b = Keyboard.addListener(hideEvt, () => setVisible(false));
    return () => {
      a.remove();
      b.remove();
    };
  }, []);
  return visible;
}

/** iOS 26-style floating capsule: Library · Wiki · Ask, plus a save button. */
export function FloatingTabBar({ state, emitter, navigateToTab, insets }: BottomTabBarProps) {
  const keyboardVisible = useKeyboardVisible();
  const [saving, setSaving] = useState(false);
  // The sheet stays mounted while the keyboard is up (its own input raises it).
  const sheet = <SaveLinkSheet visible={saving} onClose={() => setSaving(false)} />;
  if (keyboardVisible) return sheet;

  const bottom = tabBarBottom(insets.bottom);
  return (
    <>
      <BottomFade height={bottom + TAB_BAR_HEIGHT + 28} />
      <View style={[styles.wrap, { bottom }]} pointerEvents="box-none">
        <View style={styles.capsule} accessibilityRole="tablist">
          {state.routes.map((route, index) => {
            const tab = TABS[route.name];
            if (!tab) return null;
            const focused = state.index === index;
            return (
              <Pressable
                key={route.key}
                onPress={() => {
                  const event = emitter.emit({ type: "tabPress", target: route.key, canPreventDefault: true });
                  if (!focused && !event.defaultPrevented) {
                    void Haptics.selectionAsync();
                    navigateToTab(route.key);
                  }
                }}
                style={[styles.tab, focused && styles.tabActive]}
                accessibilityRole="tab"
                accessibilityState={{ selected: focused }}
                accessibilityLabel={tab.label}
              >
                <Ionicons
                  name={focused ? tab.iconActive : tab.icon}
                  size={22}
                  color={focused ? colors.ink : colors.ink2}
                />
                <Text style={[styles.tabLabel, { color: focused ? colors.ink : colors.ink2 }]}>{tab.label}</Text>
              </Pressable>
            );
          })}
        </View>
        <Pressable
          onPress={() => {
            void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
            setSaving(true);
          }}
          style={({ pressed }) => [styles.add, pressed && { opacity: 0.8 }]}
          accessibilityRole="button"
          accessibilityLabel="Save a link"
        >
          <Ionicons name="add" size={28} color={colors.onInk} />
        </Pressable>
        {sheet}
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "absolute",
    left: 20,
    right: 20,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  capsule: {
    flex: 1,
    height: TAB_BAR_HEIGHT,
    borderRadius: radii.pill,
    // Opaque so the shadow can be computed cheaply; it sits on BottomFade anyway.
    backgroundColor: colors.bg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairlineStrong,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 5,
    ...floatingShadow,
  },
  tab: {
    flex: 1,
    height: TAB_BAR_HEIGHT - 10,
    borderRadius: radii.pill,
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
  },
  tabActive: { backgroundColor: colors.fill },
  tabLabel: { ...type.tabLabel },
  add: {
    width: TAB_BAR_HEIGHT,
    height: TAB_BAR_HEIGHT,
    borderRadius: TAB_BAR_HEIGHT / 2,
    backgroundColor: colors.ink,
    alignItems: "center",
    justifyContent: "center",
    ...floatingShadow,
  },
});
