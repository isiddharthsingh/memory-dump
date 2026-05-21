import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { theme } from "../lib/theme";

export default function ShareIntentLanding() {
  return (
    <View style={styles.container}>
      <ActivityIndicator color={theme.accent} />
      <Text style={styles.text}>Saving share…</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.bg,
    gap: 12,
  },
  text: { color: theme.textMuted },
});
