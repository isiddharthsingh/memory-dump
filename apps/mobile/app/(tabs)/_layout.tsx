import { Tabs } from "expo-router";

import { FloatingTabBar } from "../../components/FloatingTabBar";
import { colors } from "../../lib/theme";

export default function TabsLayout() {
  return (
    <Tabs
      tabBar={(props) => <FloatingTabBar {...props} />}
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen name="index" options={{ title: "Library" }} />
      <Tabs.Screen name="wiki" options={{ title: "Wiki" }} />
      <Tabs.Screen name="ask" options={{ title: "Ask" }} />
    </Tabs>
  );
}
