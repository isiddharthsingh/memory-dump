import * as SecureStore from "expo-secure-store";
import { useCallback, useEffect, useState } from "react";

const SERVER_KEY = "memory_dump_server_url";

export type ServerConfig = {
  serverUrl: string | null;
};

export async function loadConfig(): Promise<ServerConfig> {
  const serverUrl = await SecureStore.getItemAsync(SERVER_KEY);
  return { serverUrl };
}

export async function saveConfig(cfg: ServerConfig): Promise<void> {
  if (cfg.serverUrl) {
    await SecureStore.setItemAsync(SERVER_KEY, cfg.serverUrl);
  } else {
    await SecureStore.deleteItemAsync(SERVER_KEY);
  }
}

export function useConfig(): {
  config: ServerConfig | null;
  refresh: () => Promise<void>;
  save: (cfg: ServerConfig) => Promise<void>;
} {
  const [config, setConfig] = useState<ServerConfig | null>(null);

  const refresh = useCallback(async () => {
    setConfig(await loadConfig());
  }, []);

  const save = useCallback(
    async (cfg: ServerConfig) => {
      await saveConfig(cfg);
      setConfig(cfg);
    },
    [],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { config, refresh, save };
}
