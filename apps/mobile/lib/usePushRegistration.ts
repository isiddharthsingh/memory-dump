import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { useEffect, useRef } from "react";
import { Platform } from "react-native";

import { api } from "./api";
import { loadConfig } from "./settings";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

/**
 * Asks for push permission, fetches the Expo push token, and registers
 * it with the backend. Silently no-ops on simulators or when permission
 * is denied. Free Apple Developer accounts get a token but APNs delivery
 * won't actually work — that's expected.
 */
export function usePushRegistration(): void {
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    (async () => {
      if (!Device.isDevice) return;

      try {
        const existing = await Notifications.getPermissionsAsync();
        let status = existing.status;
        if (status !== "granted") {
          const req = await Notifications.requestPermissionsAsync();
          status = req.status;
        }
        if (status !== "granted") return;

        const projectId =
          Constants.expoConfig?.extra?.eas?.projectId ??
          Constants.easConfig?.projectId;
        const token = await Notifications.getExpoPushTokenAsync(
          projectId ? { projectId } : undefined,
        );

        const cfg = await loadConfig();
        if (!cfg.serverUrl || !cfg.apiToken) return;
        await api.registerDevice(token.data, Platform.OS);
      } catch {
        // best-effort; silently no-op on errors (simulator, no APNs, etc.)
      }
    })();
  }, []);
}
