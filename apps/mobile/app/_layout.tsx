import { QueryClientProvider } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { ShareIntentProvider } from "expo-share-intent";
import { StatusBar } from "expo-status-bar";
import { useCallback, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { queryClient } from "../lib/queryClient";
import { theme } from "../lib/theme";
import { useIncomingShare } from "../lib/useIncomingShare";

function ShareToast({
  message,
  tone,
  onDismiss,
}: {
  message: string;
  tone: "ok" | "err";
  onDismiss: () => void;
}) {
  return (
    <View
      style={[
        styles.toast,
        { borderColor: tone === "ok" ? theme.success : theme.danger },
      ]}
    >
      <Text style={styles.toastText}>{message}</Text>
      <Pressable onPress={onDismiss}>
        <Text style={styles.toastDismiss}>Dismiss</Text>
      </Pressable>
    </View>
  );
}

function ShareHandler({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [toast, setToast] = useState<{ message: string; tone: "ok" | "err" } | null>(
    null,
  );

  const onResult = useCallback(
    (r: { ok: boolean; message: string }) => {
      setToast({ message: r.message, tone: r.ok ? "ok" : "err" });
      if (r.ok) {
        router.replace("/");
      }
      setTimeout(() => setToast(null), 4000);
    },
    [router],
  );

  useIncomingShare(onResult);

  return (
    <>
      {children}
      {toast && (
        <ShareToast
          message={toast.message}
          tone={toast.tone}
          onDismiss={() => setToast(null)}
        />
      )}
    </>
  );
}

function BackButton({ fallback = "/" }: { fallback?: "/" | "/wiki" }) {
  const router = useRouter();
  return (
    <Pressable
      onPress={() => {
        if (router.canGoBack()) router.back();
        else router.replace(fallback);
      }}
      hitSlop={12}
      style={({ pressed }) => ({
        paddingHorizontal: 8,
        paddingVertical: 4,
        opacity: pressed ? 0.5 : 1,
      })}
      accessibilityRole="button"
      accessibilityLabel="Back"
    >
      <Text style={{ color: theme.accent, fontSize: 17 }}>‹ Back</Text>
    </Pressable>
  );
}

function RootLayoutInner() {
  const router = useRouter();
  return (
    <ShareIntentProvider
      options={{
        resetOnBackground: true,
        onResetShareIntent: () => router.replace("/"),
      }}
    >
      <ShareHandler>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: theme.bg },
            headerTintColor: theme.text,
            contentStyle: { backgroundColor: theme.bg },
          }}
        >
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen
            name="item/[id]"
            options={{
              title: "Item",
              headerBackVisible: false,
              headerLeft: () => <BackButton fallback="/" />,
            }}
          />
          <Stack.Screen
            name="wiki/[slug]"
            options={{
              title: "Wiki",
              headerBackVisible: false,
              headerLeft: () => <BackButton fallback="/wiki" />,
            }}
          />
        </Stack>
      </ShareHandler>
    </ShareIntentProvider>
  );
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <RootLayoutInner />
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  toast: {
    position: "absolute",
    bottom: 90,
    left: 16,
    right: 16,
    backgroundColor: theme.bgElev,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  toastText: {
    color: theme.text,
    flex: 1,
    marginRight: 12,
  },
  toastDismiss: {
    color: theme.accent,
    fontWeight: "600",
  },
});
