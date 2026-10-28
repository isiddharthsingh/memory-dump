import { Redirect } from "expo-router";
import { useShareIntentContext } from "expo-share-intent";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { AppMark } from "../components/ui";
import { colors, spacing, type } from "../lib/theme";

export default function ShareIntentLanding() {
  const { isReady, hasShareIntent } = useShareIntentContext();
  // Nothing to save (stale link, unreadable share, or a dev reload on this
  // route): go to the Library instead of spinning forever. A real share is
  // handled by useIncomingShare, which navigates home once it's sent.
  if (isReady && !hasShareIntent) return <Redirect href="/" />;

  return (
    <View style={styles.container}>
      <AppMark size={56} />
      <View style={styles.status}>
        <ActivityIndicator color={colors.ink2} />
        <Text style={styles.text}>Saving to your library…</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.bg, gap: spacing.lg },
  status: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  text: { ...type.callout, color: colors.ink2 },
});
