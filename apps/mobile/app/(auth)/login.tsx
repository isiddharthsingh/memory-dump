import { useRouter } from "expo-router";
import { useEffect, useState } from "react";

import { AuthScaffold, Field, FieldGroup, FooterLink, ServerField } from "../../components/AuthScaffold";
import { Button, ErrorNotice } from "../../components/ui";
import { useAuth } from "../../lib/AuthContext";
import { loadConfig, saveConfig, useConfig } from "../../lib/settings";

export default function LoginScreen() {
  const router = useRouter();
  const { status, login } = useAuth();
  const { config } = useConfig();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // The saved server shows until the user edits it.
  const [serverDraft, setServerUrl] = useState<string | null>(null);
  const serverUrl = serverDraft ?? config?.serverUrl ?? "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "signedIn") router.replace("/");
  }, [status, router]);

  async function onSubmit() {
    setError(null);
    setBusy(true);
    try {
      const cleanedUrl = serverUrl.trim().replace(/\/$/, "");
      if (cleanedUrl !== (config?.serverUrl ?? "")) {
        await saveConfig({ serverUrl: cleanedUrl || null });
        await loadConfig();
      }
      await login(email.trim().toLowerCase(), password);
    } catch (e) {
      setError((e as Error).message || "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthScaffold
      footer={
        <FooterLink prompt="New to Memory Dump?" label="Create an account" onPress={() => router.replace("/(auth)/signup")} />
      }
    >
      <FieldGroup>
        <Field
          label="Email"
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="emailAddress"
          autoComplete="email"
        />
        <Field
          divider
          secure
          label="Password"
          value={password}
          onChangeText={setPassword}
          placeholder="Your password"
          textContentType="password"
          autoComplete="current-password"
          returnKeyType="go"
          onSubmitEditing={() => void onSubmit()}
        />
      </FieldGroup>
      <ServerField value={serverUrl} onChange={setServerUrl} />
      {error ? <ErrorNotice title="Couldn’t sign you in" detail={error} /> : null}
      <Button label="Sign in" onPress={() => void onSubmit()} loading={busy} disabled={!email || !password || !serverUrl} />
    </AuthScaffold>
  );
}
