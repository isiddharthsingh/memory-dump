import { Tabs } from "expo-router";
import { Text } from "react-native";

import { theme } from "../../lib/theme";

function TabIcon({ label, focused }: { label: string; focused: boolean }) {
  return (
    <Text style={{ fontSize: 22, opacity: focused ? 1 : 0.5 }}>{label}</Text>
  );
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
        headerStyle: { backgroundColor: theme.bg },
        headerTintColor: theme.text,
        sceneStyle: { backgroundColor: theme.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Inbox",
          tabBarIcon: ({ focused }) => <TabIcon label="📥" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="wiki"
        options={{
          title: "Wiki",
          tabBarIcon: ({ focused }) => <TabIcon label="📚" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="ask"
        options={{
          title: "Ask",
          tabBarIcon: ({ focused }) => <TabIcon label="💬" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Settings",
          tabBarIcon: ({ focused }) => <TabIcon label="⚙️" focused={focused} />,
        }}
      />
    </Tabs>
  );
}
