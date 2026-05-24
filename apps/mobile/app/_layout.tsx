import { QueryClientProvider } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { Stack, useRouter, useSegments } from "expo-router";
import { ShareIntentProvider } from "expo-share-intent";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider, useAuth } from "../lib/AuthContext";
import { setOnSessionInvalid } from "../lib/api";
import { queryClient } from "../lib/queryClient";
import { TOUCH_TARGET, cardShadow, radii, theme } from "../lib/theme";
import { useIncomingShare } from "../lib/useIncomingShare";
import { usePushRegistration } from "../lib/usePushRegistration";
import { useShareQueueDrain } from "../lib/useShareQueueDrain";

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
      <Pressable
        onPress={onDismiss}
        hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
        style={({ pressed }) => [styles.toastDismissBtn, pressed && { opacity: 0.6 }]}
        accessibilityRole="button"
        accessibilityLabel="Dismiss toast"
      >
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
      void Haptics.notificationAsync(
        r.ok
          ? Haptics.NotificationFeedbackType.Success
          : Haptics.NotificationFeedbackType.Error,
      );
      setToast({ message: r.message, tone: r.ok ? "ok" : "err" });
      if (r.ok) {
        router.replace("/");
      }
      setTimeout(() => setToast(null), 4000);
    },
    [router],
  );

  useIncomingShare(onResult);
  usePushRegistration();
  useShareQueueDrain((sent) => {
    setToast({
      message: `Sent ${sent} queued share${sent === 1 ? "" : "s"}.`,
      tone: "ok",
    });
    setTimeout(() => setToast(null), 3000);
  });

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

/** Redirects in/out of `(auth)/*` based on session status. */
function AuthGate({ children }: { children: React.ReactNode }) {
  const { status, setSession } = useAuth();
  const router = useRouter();
  const segments = useSegments();

  // Let the api client forcibly sign out the user (refresh failed, account
  // deleted, etc.). The callback fires from outside React state, so we
  // bridge it back through setSession.
  useEffect(() => {
    setOnSessionInvalid(() => {
      void setSession(null);
    });
    return () => setOnSessionInvalid(null);
  }, [setSession]);

  useEffect(() => {
    if (status === "loading") return;
    const inAuthGroup = segments[0] === "(auth)";
    if (status === "signedOut" && !inAuthGroup) {
      router.replace("/(auth)/login");
    } else if (status === "signedIn" && inAuthGroup) {
      router.replace("/");
    }
  }, [status, segments, router]);

  return <>{children}</>;
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
      <AuthGate>
        <ShareHandler>
          <StatusBar style="dark" />
          <Stack
            screenOptions={{
              headerStyle: { backgroundColor: theme.surface },
              headerTintColor: theme.accent,
              headerTitleStyle: { color: theme.text, fontWeight: "700" },
              headerShadowVisible: false,
              contentStyle: { backgroundColor: theme.bg },
            }}
          >
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="(auth)" options={{ headerShown: false }} />
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
      </AuthGate>
    </ShareIntentProvider>
  );
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: theme.bg }}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <RootLayoutInner />
          </AuthProvider>
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
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    ...cardShadow("md"),
  },
  toastText: {
    color: theme.text,
    flex: 1,
    marginRight: 12,
    fontWeight: "500",
  },
  toastDismissBtn: {
    minHeight: TOUCH_TARGET,
    paddingHorizontal: 8,
    justifyContent: "center",
  },
  toastDismiss: {
    color: theme.accent,
    fontWeight: "600",
  },
});
