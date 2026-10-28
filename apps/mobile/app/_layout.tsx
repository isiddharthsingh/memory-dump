import {
  Newsreader_400Regular,
  Newsreader_500Medium,
  Newsreader_600SemiBold,
  useFonts,
} from "@expo-google-fonts/newsreader";
import { Ionicons } from "@expo/vector-icons";
import { QueryClientProvider } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { ShareIntentProvider } from "expo-share-intent";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState } from "react";
import { Pressable, StyleSheet, Text } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";

import { AuthProvider, useAuth } from "../lib/AuthContext";
import { setOnSessionInvalid } from "../lib/api";
import { queryClient } from "../lib/queryClient";
import { TAB_BAR_HEIGHT, colors, floatingShadow, radii, spacing, type } from "../lib/theme";
import { useIncomingShare } from "../lib/useIncomingShare";
import { usePushRegistration } from "../lib/usePushRegistration";
import { useShareQueueDrain } from "../lib/useShareQueueDrain";

void SplashScreen.preventAutoHideAsync();

type Toast = { message: string; tone: "ok" | "err" };

function ShareToast({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const insets = useSafeAreaInsets();
  const ok = toast.tone === "ok";
  return (
    <Pressable
      onPress={onDismiss}
      style={[
        styles.toast,
        { bottom: insets.bottom + TAB_BAR_HEIGHT + spacing.lg, backgroundColor: ok ? colors.ink : colors.failed },
      ]}
      accessibilityRole="alert"
      accessibilityLabel={`${toast.message}. Tap to dismiss.`}
    >
      <Ionicons name={ok ? "checkmark-circle" : "alert-circle"} size={20} color={colors.onInk} />
      <Text style={styles.toastText}>{toast.message}</Text>
    </Pressable>
  );
}

function ShareHandler({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [toast, setToast] = useState<Toast | null>(null);

  const onResult = useCallback(
    (r: { ok: boolean; message: string }) => {
      void Haptics.notificationAsync(
        r.ok ? Haptics.NotificationFeedbackType.Success : Haptics.NotificationFeedbackType.Error,
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
      {toast && <ShareToast toast={toast} onDismiss={() => setToast(null)} />}
    </>
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

function DoneButton() {
  const router = useRouter();
  return (
    <Pressable
      onPress={() => router.back()}
      hitSlop={8}
      accessibilityRole="button"
      accessibilityLabel="Done"
      style={styles.done}
    >
      <Text style={styles.doneText}>Done</Text>
    </Pressable>
  );
}

// Detail screens draw their own large serif title, so the native bar only
// carries the (iOS 26 glass) back button and actions.
const detailHeader = {
  title: "",
  headerTransparent: true,
  headerShadowVisible: false,
  headerBackButtonDisplayMode: "minimal",
  headerTintColor: colors.ink,
} as const;

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
              headerTintColor: colors.ink,
              headerTitleStyle: { color: colors.ink, fontWeight: "600" },
              headerShadowVisible: false,
              contentStyle: { backgroundColor: colors.bg },
            }}
          >
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="(auth)" options={{ headerShown: false }} />
            <Stack.Screen name="shareintent" options={{ headerShown: false }} />
            <Stack.Screen name="item/[id]" options={detailHeader} />
            <Stack.Screen name="wiki/[slug]" options={detailHeader} />
            <Stack.Screen
              name="settings"
              options={{
                title: "Settings",
                presentation: "modal",
                headerStyle: { backgroundColor: colors.fill },
                contentStyle: { backgroundColor: colors.fill },
                headerRight: () => <DoneButton />,
              }}
            />
          </Stack>
        </ShareHandler>
      </AuthGate>
    </ShareIntentProvider>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Newsreader_400Regular,
    Newsreader_500Medium,
    Newsreader_600SemiBold,
  });
  const ready = fontsLoaded || !!fontError;

  useEffect(() => {
    if (ready) void SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: colors.bg }}>
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
    left: spacing.lg,
    right: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    minHeight: 52,
    paddingHorizontal: spacing.lg - 4,
    paddingVertical: spacing.md,
    borderRadius: radii.xl,
    ...floatingShadow,
  },
  toastText: { ...type.callout, flex: 1, color: colors.onInk, fontWeight: "500" },
  done: { minHeight: 44, minWidth: 44, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.xs },
  doneText: { fontSize: 17, fontWeight: "600", color: colors.accent },
});
