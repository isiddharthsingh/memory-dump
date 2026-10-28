import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { StyleSheet, Text } from "react-native";

import { AuthScaffold, Field, FieldGroup, FooterLink, ServerField } from "../../components/AuthScaffold";
import { Button, ErrorNotice } from "../../components/ui";
import { useAuth } from "../../lib/AuthContext";
import { loadConfig, saveConfig, useConfig } from "../../lib/settings";
import { colors, spacing, type } from "../../lib/theme";

const MIN_PASSWORD_LEN = 10;

export default function SignupScreen() {
  const router = useRouter();
  const { status, signup } = useAuth();
  const { config } = useConfig();
  // The saved server shows until the user edits it.
  const [serverDraft, setServerUrl] = useState<string | null>(null);
  const serverUrl = serverDraft ?? config?.serverUrl ?? "";
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "signedIn") router.replace("/");
  }, [status, router]);

  async function onSubmit() {
    setError(null);
    if (password.length < MIN_PASSWORD_LEN) {
      setError(`Use at least ${MIN_PASSWORD_LEN} characters for your password.`);
      return;
    }
    setBusy(true);
    try {
      const cleanedUrl = serverUrl.trim().replace(/\/$/, "");
      if (cleanedUrl !== (config?.serverUrl ?? "")) {
        await saveConfig({ serverUrl: cleanedUrl || null });
        await loadConfig();
      }
      await signup(email.trim().toLowerCase(), password, displayName.trim() || undefined);
    } catch (e) {
      setError((e as Error).message || "Couldn’t create your account.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthScaffold
      footer={<FooterLink prompt="Already have an account?" label="Sign in" onPress={() => router.replace("/(auth)/login")} />}
    >
      <FieldGroup>
        <Field
          label="Name (optional)"
          value={displayName}
          onChangeText={setDisplayName}
          placeholder="Jane Doe"
          autoCapitalize="words"
          textContentType="name"
        />
        <Field
          divider
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
          placeholder={`At least ${MIN_PASSWORD_LEN} characters`}
          textContentType="newPassword"
          autoComplete="new-password"
        />
      </FieldGroup>
      <Text style={styles.hint}>Use at least {MIN_PASSWORD_LEN} characters.</Text>
      <ServerField value={serverUrl} onChange={setServerUrl} />
      {error ? <ErrorNotice title="Couldn’t create your account" detail={error} /> : null}
      <Button
        label="Create account"
        onPress={() => void onSubmit()}
        loading={busy}
        disabled={!email || !password || !serverUrl}
      />
    </AuthScaffold>
  );
}

const styles = StyleSheet.create({
  hint: { ...type.footnote, color: colors.ink2, marginTop: -spacing.xs, marginLeft: spacing.xs },
});
