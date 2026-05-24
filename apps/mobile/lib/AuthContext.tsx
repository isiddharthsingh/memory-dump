import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Session, auth, clearSession, loadSession, saveSession } from "./auth";

type AuthStatus = "loading" | "signedIn" | "signedOut";

type AuthContextValue = {
  status: AuthStatus;
  session: Session | null;
  signup: (email: string, password: string, displayName?: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  deleteAccount: () => Promise<void>;
  /** Replace the in-memory + persisted session, e.g. after a token refresh
   *  triggered by the API client. */
  setSession: (s: Session | null) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSessionState] = useState<Session | null>(null);
  // Track whether bootstrap finished so children can wait for loading->ready.
  const bootRef = useRef(false);

  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = true;
    (async () => {
      const s = await loadSession();
      setSessionState(s);
      setStatus(s ? "signedIn" : "signedOut");
    })();
  }, []);

  const setSession = useCallback(async (s: Session | null) => {
    if (s) {
      await saveSession(s);
      setSessionState(s);
      setStatus("signedIn");
    } else {
      await clearSession();
      setSessionState(null);
      setStatus("signedOut");
    }
  }, []);

  const signup = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const s = await auth.signup(email, password, displayName);
      setSessionState(s);
      setStatus("signedIn");
    },
    [],
  );

  const login = useCallback(async (email: string, password: string) => {
    const s = await auth.login(email, password);
    setSessionState(s);
    setStatus("signedIn");
  }, []);

  const logout = useCallback(async () => {
    if (session) {
      await auth.logout(session.refreshToken);
    } else {
      await clearSession();
    }
    setSessionState(null);
    setStatus("signedOut");
  }, [session]);

  const deleteAccount = useCallback(async () => {
    if (!session) return;
    await auth.deleteAccount(session.accessToken);
    setSessionState(null);
    setStatus("signedOut");
  }, [session]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      session,
      signup,
      login,
      logout,
      deleteAccount,
      setSession,
    }),
    [status, session, signup, login, logout, deleteAccount, setSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
