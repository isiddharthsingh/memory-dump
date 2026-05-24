import * as SecureStore from "expo-secure-store";
import { z } from "zod";

import { loadConfig } from "./settings";

const ACCESS_KEY = "memory_dump_access_token";
const REFRESH_KEY = "memory_dump_refresh_token";
const USER_KEY = "memory_dump_user";

const User = z.object({
  id: z.string().uuid(),
  email: z.string(),
  display_name: z.string().nullable(),
  created_at: z.string(),
});

const TokenResponse = z.object({
  access_token: z.string(),
  refresh_token: z.string(),
  token_type: z.literal("bearer"),
  expires_in: z.number(),
  user: User,
});

export type User = z.infer<typeof User>;
export type TokenResponse = z.infer<typeof TokenResponse>;

export type Session = {
  accessToken: string;
  refreshToken: string;
  user: User;
};

export class AuthError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function postAuth<T>(
  path: string,
  body: unknown,
  schema: z.ZodSchema<T>,
): Promise<T> {
  const cfg = await loadConfig();
  if (!cfg.serverUrl) {
    throw new AuthError(0, "Server URL not configured.");
  }
  const url = cfg.serverUrl.replace(/\/$/, "") + path;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = "";
    try {
      const errBody = await resp.json();
      detail = errBody?.detail ?? "";
    } catch {
      detail = await resp.text().catch(() => "");
    }
    throw new AuthError(resp.status, detail || resp.statusText);
  }
  return schema.parse(await resp.json());
}

export async function loadSession(): Promise<Session | null> {
  const [accessToken, refreshToken, userJson] = await Promise.all([
    SecureStore.getItemAsync(ACCESS_KEY),
    SecureStore.getItemAsync(REFRESH_KEY),
    SecureStore.getItemAsync(USER_KEY),
  ]);
  if (!accessToken || !refreshToken || !userJson) return null;
  try {
    const user = User.parse(JSON.parse(userJson));
    return { accessToken, refreshToken, user };
  } catch {
    return null;
  }
}

export async function saveSession(s: Session): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(ACCESS_KEY, s.accessToken),
    SecureStore.setItemAsync(REFRESH_KEY, s.refreshToken),
    SecureStore.setItemAsync(USER_KEY, JSON.stringify(s.user)),
  ]);
}

export async function clearSession(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(ACCESS_KEY),
    SecureStore.deleteItemAsync(REFRESH_KEY),
    SecureStore.deleteItemAsync(USER_KEY),
  ]);
}

function fromToken(r: TokenResponse): Session {
  return {
    accessToken: r.access_token,
    refreshToken: r.refresh_token,
    user: r.user,
  };
}

export const auth = {
  async signup(
    email: string,
    password: string,
    displayName?: string,
  ): Promise<Session> {
    const r = await postAuth(
      "/auth/signup",
      { email, password, display_name: displayName ?? null },
      TokenResponse,
    );
    const s = fromToken(r);
    await saveSession(s);
    return s;
  },

  async login(email: string, password: string): Promise<Session> {
    const r = await postAuth(
      "/auth/login",
      { email, password },
      TokenResponse,
    );
    const s = fromToken(r);
    await saveSession(s);
    return s;
  },

  /**
   * Rotates the refresh token. Throws AuthError(401) if the refresh
   * token is invalid/expired/revoked — caller should clear the session
   * and send the user back to /login.
   */
  async refresh(refreshToken: string): Promise<Session> {
    const r = await postAuth(
      "/auth/refresh",
      { refresh_token: refreshToken },
      TokenResponse,
    );
    const s = fromToken(r);
    await saveSession(s);
    return s;
  },

  async logout(refreshToken: string): Promise<void> {
    try {
      await postAuth("/auth/logout", { refresh_token: refreshToken }, z.unknown());
    } catch {
      // Best-effort — clearing local session is what matters.
    }
    await clearSession();
  },

  async deleteAccount(accessToken: string): Promise<void> {
    const cfg = await loadConfig();
    if (!cfg.serverUrl) throw new AuthError(0, "Server URL not configured.");
    const url = cfg.serverUrl.replace(/\/$/, "") + "/auth/me";
    const resp = await fetch(url, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!resp.ok && resp.status !== 204) {
      throw new AuthError(resp.status, resp.statusText);
    }
    await clearSession();
  },
};
