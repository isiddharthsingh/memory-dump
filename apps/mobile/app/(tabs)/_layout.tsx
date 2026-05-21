import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";

import { theme } from "../../lib/theme";

type IoniconName = React.ComponentProps<typeof Ionicons>["name"];

function makeIcon(
  filled: IoniconName,
  outline: IoniconName,
): (props: { focused: boolean; color: string; size: number }) => React.ReactNode {
  const Icon = ({ focused, color, size }: { focused: boolean; color: string; size: number }) => (
    <Ionicons name={focused ? filled : outline} size={size} color={color} />
  );
  return Icon;
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        tabBarStyle: {
          backgroundColor: theme.bgElev,
          borderTopColor: theme.border,
        },
        tabBarActiveTintColor: theme.accent,
        tabBarInactiveTintColor: theme.textMuted,
        tabBarLabelStyle: { fontSize: 11, fontWeight: "600" },
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        sceneStyle: { backgroundColor: theme.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Inbox",
          tabBarIcon: makeIcon("file-tray-full", "file-tray-full-outline"),
        }}
      />
      <Tabs.Screen
        name="wiki"
        options={{
          title: "Wiki",
          tabBarIcon: makeIcon("library", "library-outline"),
        }}
      />
      <Tabs.Screen
        name="ask"
        options={{
          title: "Ask",
          tabBarIcon: makeIcon("sparkles", "sparkles-outline"),
          tabBarHideOnKeyboard: true,
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Settings",
          tabBarIcon: makeIcon("settings", "settings-outline"),
        }}
      />
    </Tabs>
  );
}
