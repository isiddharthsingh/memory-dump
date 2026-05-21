import { Link } from "expo-router";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { api, type Citation } from "../../lib/api";
import { theme } from "../../lib/theme";

export default function AskScreen() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [streaming, setStreaming] = useState(false);
  const streamRef = useRef<{ abort: () => void } | null>(null);

  useEffect(() => {
    return () => {
      streamRef.current?.abort();
    };
  }, []);

  const submit = (q: string) => {
    if (!q || streaming) return;
    streamRef.current?.abort();
    setAnswer("");
    setCitations([]);
    setStreaming(true);
    streamRef.current = api.askStream(q, {
      onToken: (t) => setAnswer((prev) => (prev ?? "") + t),
      onDone: (c) => {
        setCitations(c);
        setStreaming(false);
      },
      onError: (err) => {
        setAnswer(`Error: ${err.message}`);
        setCitations([]);
        setStreaming(false);
      },
    });
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
      >
        {answer !== null ? (
          <View style={styles.answerBox}>
            <Text style={styles.answerText}>
              {renderAnswer(answer)}
              {streaming ? <Text style={styles.caret}>▍</Text> : null}
            </Text>
            {citations.length > 0 ? (
              <View style={styles.citations}>
                <Text style={styles.sectionLabel}>Sources</Text>
                {citations.map((c) => (
                  <CitationRow key={`${c.kind}:${c.id}`} citation={c} />
                ))}
              </View>
            ) : null}
          </View>
        ) : (
          <View style={styles.intro}>
            <Text style={styles.introTitle}>Ask your memory</Text>
            <Text style={styles.introText}>
              Try: "that Karpathy video on async runtimes" or "have I saved
              anything about rust async?"
            </Text>
          </View>
        )}
      </ScrollView>

      <View style={styles.inputBar}>
        <TextInput
          style={styles.input}
          placeholder="Ask anything…"
          placeholderTextColor={theme.textMuted}
          value={question}
          onChangeText={setQuestion}
          onSubmitEditing={() => submit(question.trim())}
          returnKeyType="send"
          editable={!streaming}
        />
        <Pressable
          onPress={() => submit(question.trim())}
          style={[styles.sendBtn, streaming && { opacity: 0.6 }]}
          disabled={streaming || !question.trim()}
        >
          {streaming ? (
            <ActivityIndicator color={theme.text} />
          ) : (
            <Text style={styles.sendText}>Ask</Text>
          )}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

function CitationRow({ citation }: { citation: Citation }) {
  if (citation.kind === "item") {
    return (
      <Link href={{ pathname: "/item/[id]", params: { id: citation.id } }} asChild>
        <Pressable style={styles.citationRow}>
          <Text style={styles.citationKind}>item</Text>
          <Text style={styles.citationTitle} numberOfLines={2}>
            {citation.title ?? citation.url ?? "(untitled)"}
          </Text>
        </Pressable>
      </Link>
    );
  }
  return (
    <Link href={{ pathname: "/wiki/[slug]", params: { slug: citation.slug ?? "" } }} asChild>
      <Pressable style={styles.citationRow}>
        <Text style={styles.citationKind}>wiki</Text>
        <Text style={styles.citationTitle} numberOfLines={2}>
          {citation.title ?? citation.slug ?? "(untitled)"}
        </Text>
      </Pressable>
    </Link>
  );
}

/** Strip [item:UUID] and [wiki:slug] markers from the answer text. */
function renderAnswer(answer: string): string {
  return answer.replace(/\[(item|wiki):[^\]]+\]/g, "").replace(/  +/g, " ");
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  scroll: { padding: 16, flexGrow: 1 },
  intro: { flex: 1, justifyContent: "center", gap: 8 },
  introTitle: { color: theme.text, fontSize: 20, fontWeight: "700" },
  introText: { color: theme.textMuted, fontSize: 14 },
  answerBox: {
    backgroundColor: theme.bgElev,
    borderRadius: 12,
    padding: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  answerText: { color: theme.text, fontSize: 16, lineHeight: 22 },
  caret: { color: theme.accent, fontSize: 16 },
  citations: { marginTop: 16, gap: 6 },
  sectionLabel: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  citationRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 6,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
  },
  citationKind: {
    color: theme.accent,
    fontSize: 11,
    fontWeight: "700",
    width: 44,
    textTransform: "uppercase",
  },
  citationTitle: { color: theme.text, fontSize: 14, flex: 1 },
  inputBar: {
    flexDirection: "row",
    padding: 12,
    gap: 8,
    backgroundColor: theme.bgElev,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
  },
  input: {
    flex: 1,
    backgroundColor: theme.bg,
    color: theme.text,
    borderRadius: 999,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 15,
  },
  sendBtn: {
    backgroundColor: theme.accent,
    borderRadius: 999,
    paddingHorizontal: 20,
    justifyContent: "center",
    minWidth: 64,
    alignItems: "center",
  },
  sendText: { color: theme.text, fontWeight: "700" },
});
