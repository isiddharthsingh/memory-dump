import * as SecureStore from "expo-secure-store";
import { useCallback, useEffect, useState } from "react";

const SERVER_KEY = "memory_dump_server_url";
const TOKEN_KEY = "memory_dump_api_token";

export type ServerConfig = {
  serverUrl: string | null;
  apiToken: string | null;
};

export async function loadConfig(): Promise<ServerConfig> {
  const [serverUrl, apiToken] = await Promise.all([
    SecureStore.getItemAsync(SERVER_KEY),
    SecureStore.getItemAsync(TOKEN_KEY),
  ]);
  return { serverUrl, apiToken };
}

export async function saveConfig(cfg: ServerConfig): Promise<void> {
  await Promise.all([
    cfg.serverUrl
      ? SecureStore.setItemAsync(SERVER_KEY, cfg.serverUrl)
      : SecureStore.deleteItemAsync(SERVER_KEY),
    cfg.apiToken
      ? SecureStore.setItemAsync(TOKEN_KEY, cfg.apiToken)
      : SecureStore.deleteItemAsync(TOKEN_KEY),
  ]);
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
